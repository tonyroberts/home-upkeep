"""
CalDAV transport for the Home Upkeep sync.

The sync engine (`sync.py`) only knows the small `Remote` interface below.
`CalDavRemote` implements it against a real CalDAV server (Radicale, Nextcloud,
...) using the `caldav` library, which Home Assistant's own CalDAV integration
uses as well. Talking to the server directly - instead of through Home
Assistant's cached `todo` entity - means every read is the server's current
state, and we choose each item's UID ourselves.

Only four fields cross the wire: summary, completed, due (a date) and
description. Everything else on a VTODO (recurrence rules, alarms, categories,
...) is left exactly as it was when an item is updated.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

import caldav
import requests
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError
from homeassistant.util import dt as dt_util
from icalendar import Calendar, Todo

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_NETWORK_ERRORS = (requests.RequestException, DAVError)
_COMPLETED_STATUSES = {"COMPLETED", "CANCELLED"}


@dataclass(frozen=True)
class RemoteItem:
    """One to-do item on the remote side, reduced to the synced fields."""

    uid: str
    summary: str
    completed: bool
    due: str | None  # ISO date
    description: str | None

    def fields(self) -> dict[str, Any]:
        """Return the synced fields in the shape the engine merges."""
        return {
            "summary": self.summary,
            "completed": self.completed,
            "due": self.due,
            "description": self.description,
        }


class Remote(Protocol):
    """What the sync engine needs from the other side."""

    async def async_items(self) -> list[RemoteItem] | None:
        """Return every item, or None if the server could not be read."""

    async def async_create(self, fields: dict[str, Any]) -> str | None:
        """Create an item and return its uid (None on failure)."""

    async def async_update(self, uid: str, fields: dict[str, Any]) -> bool:
        """Set the synced fields of an item; True on success."""

    async def async_delete(self, uid: str) -> bool:
        """Delete an item; True on success (also if it was already gone)."""

    async def async_close(self) -> None:
        """Release the connection."""


@dataclass(frozen=True)
class ServerSettings:
    """How to reach a CalDAV server."""

    url: str
    username: str
    password: str
    verify_ssl: bool = True


@dataclass(frozen=True)
class Collection:
    """A to-do collection (task list) on the server."""

    name: str
    url: str


# ---- pure iCalendar helpers (no network; unit-tested directly) ----


def _plain_date(value: Any) -> date | None:
    """Reduce a DUE value (date or datetime) to a date, in local time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = dt_util.as_local(value)
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _vtodo(ics: str) -> Any | None:
    try:
        calendar = Calendar.from_ical(ics)
    except ValueError:
        return None
    return next(iter(calendar.walk("VTODO")), None)


def _item_from_component(component: Any) -> RemoteItem | None:
    """Reduce a VTODO component to the synced fields (None if it has no UID)."""
    if not component.get("UID"):
        return None
    due = component.get("DUE")
    due_date = _plain_date(due.dt) if due is not None else None
    description = component.get("DESCRIPTION")
    return RemoteItem(
        uid=str(component["UID"]),
        summary=str(component.get("SUMMARY", "")),
        completed=str(component.get("STATUS", "")).upper() in _COMPLETED_STATUSES,
        due=due_date.isoformat() if due_date else None,
        description=(str(description) or None) if description is not None else None,
    )


def parse_todo(ics: str) -> RemoteItem | None:
    """Parse the first VTODO of an iCalendar document (None if there is none)."""
    component = _vtodo(ics)
    return None if component is None else _item_from_component(component)


def apply_fields(component: Any, fields: dict[str, Any]) -> None:
    """
    Write the synced fields into a VTODO component, touching nothing else.

    A field that already has the wanted value is left alone, so e.g. a due
    *time* set by another client survives when only the date is compared.
    """
    current = _item_from_component(component)
    if current is None:
        return
    if fields["summary"] != current.summary:
        component["SUMMARY"] = fields["summary"]
    if fields["completed"] != current.completed:
        for key in ("STATUS", "COMPLETED", "PERCENT-COMPLETE"):
            component.pop(key, None)
        if fields["completed"]:
            component.add("status", "COMPLETED")
            component.add("completed", datetime.now(UTC))
            component.add("percent-complete", 100)
        else:
            component.add("status", "NEEDS-ACTION")
    if fields["due"] != current.due:
        component.pop("DUE", None)
        if fields["due"]:
            component.add("due", date.fromisoformat(fields["due"]))
    if fields["description"] != current.description:
        component.pop("DESCRIPTION", None)
        if fields["description"]:
            component.add("description", fields["description"])
    component.pop("LAST-MODIFIED", None)
    component.add("last-modified", datetime.now(UTC))


def build_todo(uid: str, fields: dict[str, Any]) -> str:
    """Build the iCalendar document for a new item."""
    now = datetime.now(UTC)
    calendar = Calendar()
    calendar.add("prodid", "-//Home Upkeep//EN")
    calendar.add("version", "2.0")
    todo = Todo()
    todo.add("uid", uid)
    todo.add("dtstamp", now)
    todo.add("created", now)
    todo.add("last-modified", now)
    todo.add("summary", fields["summary"])
    if fields["completed"]:
        todo.add("status", "COMPLETED")
        todo.add("completed", now)
    else:
        todo.add("status", "NEEDS-ACTION")
    if fields["due"]:
        todo.add("due", date.fromisoformat(fields["due"]))
    if fields["description"]:
        todo.add("description", fields["description"])
    calendar.add_component(todo)
    return calendar.to_ical().decode()


# ---- the real CalDAV server ----


def _client(settings: ServerSettings) -> caldav.DAVClient:
    return caldav.DAVClient(
        url=settings.url,
        username=settings.username,
        password=settings.password,
        ssl_verify_cert=settings.verify_ssl,
    )


class CalDavRemote:
    """One CalDAV collection, read and written through the `caldav` library."""

    def __init__(
        self, hass: HomeAssistant, settings: ServerSettings, collection_url: str
    ) -> None:
        """Remember how to reach the collection (no network access yet)."""
        self._hass = hass
        self._settings = settings
        self._collection_url = collection_url
        self._client: caldav.DAVClient | None = None
        self._calendar: caldav.Calendar | None = None

    def _get_calendar(self) -> caldav.Calendar:
        if self._calendar is None:
            client = _client(self._settings)
            self._client = client
            self._calendar = client.calendar(url=self._collection_url)
        return self._calendar

    def _close(self) -> None:
        """Close the connection (caldav 3.x runs a watcher thread per client)."""
        client, self._client, self._calendar = self._client, None, None
        if client is not None:
            _close_quietly(client)

    async def async_close(self) -> None:
        """Release the connection."""
        await self._hass.async_add_executor_job(self._close)

    async def _run(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking caldav call in the executor; reconnect next time on error."""
        try:
            return await self._hass.async_add_executor_job(
                partial(func, *args, **kwargs)
            )
        except _NETWORK_ERRORS:
            await self.async_close()
            raise

    async def async_items(self) -> list[RemoteItem] | None:
        """Return every item, or None if the server could not be read."""
        try:
            resources = await self._run(self._search)
        except _NETWORK_ERRORS:
            _LOGGER.warning("Could not read %s", self._collection_url, exc_info=True)
            return None
        items = (parse_todo(data) for data in resources)
        return [item for item in items if item is not None]

    def _search(self) -> list[str]:
        calendar = self._get_calendar()
        return [
            resource.data
            for resource in calendar.search(todo=True, include_completed=True)
            if resource.data
        ]

    async def async_create(self, fields: dict[str, Any]) -> str | None:
        """Create an item with a UID of our choosing and return it."""
        uid = str(uuid.uuid4())
        try:
            await self._run(
                lambda: self._get_calendar().save_todo(
                    ical=build_todo(uid, fields), no_overwrite=True
                )
            )
        except _NETWORK_ERRORS:
            _LOGGER.warning("Could not create item", exc_info=True)
            return None
        return uid

    async def async_update(self, uid: str, fields: dict[str, Any]) -> bool:
        """Set the synced fields of an item, leaving all other properties alone."""
        try:
            await self._run(self._update, uid, fields)
        except _NETWORK_ERRORS:
            _LOGGER.warning("Could not update item", exc_info=True)
            return False
        return True

    def _update(self, uid: str, fields: dict[str, Any]) -> None:
        todo = self._get_calendar().todo_by_uid(uid)
        apply_fields(todo.icalendar_component, fields)
        todo.save(no_create=True, obj_type="todo")

    async def async_delete(self, uid: str) -> bool:
        """Delete an item; an item that is already gone counts as deleted."""
        try:
            await self._run(lambda: self._get_calendar().todo_by_uid(uid).delete())
        except NotFoundError:
            return True
        except _NETWORK_ERRORS:
            _LOGGER.warning("Could not delete item", exc_info=True)
            return False
        return True


def _close_quietly(client: caldav.DAVClient) -> None:
    """Close a client; closing must never raise."""
    try:
        client.close()
    except Exception:
        _LOGGER.debug("Error closing CalDAV client", exc_info=True)


class CannotConnectError(Exception):
    """The server could not be reached."""


class InvalidAuthError(Exception):
    """The server rejected the credentials."""


async def _async_server_call(hass: HomeAssistant, func: Any) -> Any:
    """Run a blocking server call, mapping failures to our two error types."""
    try:
        return await hass.async_add_executor_job(func)
    except AuthorizationError as err:
        raise InvalidAuthError from err
    except _NETWORK_ERRORS as err:
        raise CannotConnectError from err


async def async_discover_collections(
    hass: HomeAssistant, settings: ServerSettings
) -> list[Collection]:
    """List the to-do collections the account can use."""

    def discover() -> list[Collection]:
        client = _client(settings)
        try:
            return [
                Collection(
                    calendar.get_display_name() or str(calendar.url), str(calendar.url)
                )
                for calendar in client.principal().calendars()
                if "VTODO" in (calendar.get_supported_components() or [])
            ]
        finally:
            _close_quietly(client)

    return await _async_server_call(hass, discover)


async def async_create_collection(
    hass: HomeAssistant, settings: ServerSettings, name: str
) -> Collection:
    """Create a new to-do collection, so a Home Upkeep list gets its own list."""

    def create() -> Collection:
        calendar = (
            _client(settings)
            .principal()
            .make_calendar(name=name, supported_calendar_component_set=["VTODO"])
        )
        return Collection(name, str(calendar.url))

    return await _async_server_call(hass, create)
