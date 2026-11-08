"""
Two-way sync between Home Upkeep lists and CalDAV collections.

Syncing is optional: it only runs for lists that were linked to a CalDAV
collection in the integration's options, and with no server configured the
integration never opens a network connection.

Each task is tied to its CalDAV item by the item's UID, together with a
snapshot of the synced fields as of the last successful sync. A sync pass
merges every field three ways against that snapshot:

- changed on one side only -> that side wins and is copied to the other;
- changed on both sides    -> Home Upkeep wins.

That single rule gives create / complete / reopen / edit / delete in both
directions without sync loops: after a pass both sides equal the snapshot, so
the next pass is a no-op. Title + due date is only used once, to *adopt*
items that already exist when a list is first linked.

Only summary, completed, due (a date) and description are synced. Home
Upkeep's own recurrence rules and constraints stay in Home Upkeep, and
properties other CalDAV clients put on an item (RRULE, alarms, ...) are left
untouched.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.start import async_at_started

from .const import CONF_LINKS, CONF_SERVER, SIGNAL_UPKEEP_CHANGED
from .models import SyncLink
from .remote import CalDavRemote, ServerSettings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from homeassistant.core import HomeAssistant

    from . import HomeUpkeepConfigEntry
    from .models import StoredTask
    from .remote import Remote, RemoteItem
    from .store import HomeUpkeepStore

_LOGGER = logging.getLogger(__name__)

# How often every link is checked for changes made on the server (e.g. in
# Tasks.org). Changes made in Home Upkeep are synced immediately.
POLL_INTERVAL = timedelta(seconds=60)

# How long an item must stay missing from the server before the deletion is
# believed. Guards against a flaky server briefly returning a short list.
DELETION_GRACE = timedelta(minutes=2)

_FIELDS = ("summary", "completed", "due", "description")
_STORE_EVENTS = {"task_created", "task_updated", "task_deleted", "data_imported"}


def create_remote(
    hass: HomeAssistant, settings: ServerSettings, collection_url: str
) -> Remote:
    """Create the transport for one collection (replaced by a fake in tests)."""
    return CalDavRemote(hass, settings, collection_url)


def server_settings(options: Mapping[str, Any]) -> ServerSettings | None:
    """Read the configured server out of the options (None if not configured)."""
    server = options.get(CONF_SERVER)
    if not server or not server.get("url"):
        return None
    return ServerSettings(
        url=server["url"],
        username=server.get("username", ""),
        password=server.get("password", ""),
        verify_ssl=server.get("verify_ssl", True),
    )


def _task_fields(task: StoredTask) -> dict[str, Any]:
    return {
        "summary": task.title,
        "completed": bool(task.completed),
        "due": task.due_date.isoformat() if task.due_date else None,
        "description": task.description or None,
    }


@dataclass
class _ListLink:
    """Runtime state of one list <-> collection link."""

    list_id: int
    collection_url: str
    settings: ServerSettings
    remote: Remote
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    scheduled: bool = False
    # task id -> time.monotonic() when its item was first seen missing
    missing: dict[int, float] = field(default_factory=dict)
    # Cancels the pending re-check timer, if one is set.
    recheck: Callable[[], None] | None = None


class CalDavSyncManager:
    """Keeps the configured Home Upkeep lists in sync with their collections."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HomeUpkeepConfigEntry,
        store: HomeUpkeepStore,
    ) -> None:
        """Initialize the manager (nothing runs until `async_start`)."""
        self._hass = hass
        self._entry = entry
        self._store = store
        self._links: dict[int, _ListLink] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._poll_unsub: Callable[[], None] | None = None
        self._started = False

    # ---- lifecycle ----

    async def async_start(self) -> None:
        """Start listening; the first sync runs once HA has started."""
        self._unsubs.append(
            async_dispatcher_connect(
                self._hass, SIGNAL_UPKEEP_CHANGED, self._handle_store_event
            )
        )
        self._unsubs.append(async_at_started(self._hass, self._handle_started))
        self.async_configure()

    @callback
    def async_stop(self) -> None:
        """Stop listening, cancel timers and close server connections."""
        self._stop_polling()
        self._drop_links(self._links.values())
        self._links = {}
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def async_configure(self) -> None:
        """(Re)read the server and linked lists from the config entry options."""
        settings = server_settings(self._entry.options)
        old = self._links
        new: dict[int, _ListLink] = {}
        if settings is not None:
            for raw_list_id, url in self._entry.options.get(CONF_LINKS, {}).items():
                try:
                    list_id = int(raw_list_id)
                except (TypeError, ValueError):
                    _LOGGER.warning("Ignoring invalid sync link %r", raw_list_id)
                    continue
                existing = old.get(list_id)
                # A link to the same collection on the same server keeps its
                # runtime state; anything else is rebuilt.
                if (
                    existing is not None
                    and existing.collection_url == url
                    and existing.settings == settings
                ):
                    new[list_id] = existing
                else:
                    remote = create_remote(self._hass, settings, str(url))
                    new[list_id] = _ListLink(list_id, str(url), settings, remote)
        self._drop_links(
            link for list_id, link in old.items() if new.get(list_id) is not link
        )
        self._links = new

        self._stop_polling()
        if self._links:
            self._poll_unsub = async_track_time_interval(
                self._hass, self._handle_poll, POLL_INTERVAL
            )
        if self._started:
            self._schedule_all()

    @callback
    def _stop_polling(self) -> None:
        if self._poll_unsub is not None:
            self._poll_unsub()
            self._poll_unsub = None

    @callback
    def _drop_links(self, links: Iterable[_ListLink]) -> None:
        for link in list(links):
            self._cancel_recheck(link)
            self._entry.async_create_background_task(
                self._hass, link.remote.async_close(), "home_upkeep_sync_close"
            )

    # ---- triggers ----

    @callback
    def _handle_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self._schedule_all()

    @callback
    def _handle_poll(self, _now: Any) -> None:
        self._schedule_all()

    @callback
    def _handle_store_event(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type not in _STORE_EVENTS:
            return
        if event_type == "data_imported":
            self._schedule_all()
            return
        list_id = event.get("list_id")
        link = self._links.get(list_id) if isinstance(list_id, int) else None
        if link is not None:
            self._schedule(link)

    @callback
    def _schedule_all(self) -> None:
        for link in self._links.values():
            self._schedule(link)

    @callback
    def _schedule(self, link: _ListLink) -> None:
        """
        Queue a sync pass for a link, coalescing bursts of triggers.

        `scheduled` is cleared when the pass *starts*, so a change that
        arrives mid-pass queues exactly one follow-up pass.
        """
        if link.scheduled or not self._started:
            return
        link.scheduled = True
        self._entry.async_create_background_task(
            self._hass, self._run(link), f"home_upkeep_sync_{link.list_id}"
        )

    @callback
    def _schedule_recheck(self, link: _ListLink, delay: float) -> None:
        """Run one more pass after `delay` seconds (keeps the earliest request)."""
        if link.recheck is not None:
            return

        @callback
        def _fire(_now: Any) -> None:
            link.recheck = None
            self._schedule(link)

        link.recheck = async_call_later(self._hass, delay, _fire)

    @callback
    def _cancel_recheck(self, link: _ListLink) -> None:
        if link.recheck is not None:
            link.recheck()
            link.recheck = None

    async def _run(self, link: _ListLink) -> None:
        async with link.lock:
            link.scheduled = False
            try:
                await self._sync_list(link)
            except Exception:
                _LOGGER.exception("Sync of list %s failed", link.list_id)

    # ---- one sync pass ----

    async def _sync_list(self, link: _ListLink) -> None:  # noqa: PLR0912
        store = self._store
        if store.get_list(link.list_id) is None:
            return
        items = await link.remote.async_items()
        if items is None:
            return  # server unreachable: never act on a missing/partial view

        by_uid = {item.uid: item for item in items}
        tasks = {t.id: t for t in store.list_tasks(link.list_id)}
        url = link.collection_url

        # 1. Pairs that are already linked: three-way merge, or propagate a
        #    deletion made on either side.
        for task_id, sl in store.sync_links(url).items():
            task = tasks.get(task_id)
            item = by_uid.get(sl.uid)
            if task is None and item is None:
                store.remove_sync_link(task_id)
            elif task is None:
                # Deleted in Home Upkeep -> remove the copy.
                if await link.remote.async_delete(sl.uid):
                    store.remove_sync_link(task_id)
                    by_uid.pop(sl.uid, None)
            elif item is None:
                self._handle_missing_item(link, task_id)
            else:
                link.missing.pop(task_id, None)
                await self._merge(link, task, item, sl)

        # 2. Open Home Upkeep tasks with no linked item yet: adopt an
        #    identical existing item, otherwise create one.
        linked = store.sync_links(url)
        linked_uids = {sl.uid for sl in linked.values()}
        free = [
            item
            for uid, item in by_uid.items()
            if uid not in linked_uids and not item.completed
        ]
        for task in tasks.values():
            if task.completed or task.id in linked:
                continue
            fields = _task_fields(task)
            match = next(
                (
                    item
                    for item in free
                    if item.summary == fields["summary"] and item.due == fields["due"]
                ),
                None,
            )
            if match is not None:
                free.remove(match)
                store.set_sync_link(task.id, SyncLink(url, match.uid, match.fields()))
            elif (uid := await link.remote.async_create(fields)) is not None:
                store.set_sync_link(task.id, SyncLink(url, uid, fields))

        # 3. Open items that exist only on the server: import them. (Items
        #    just created above are linked now, so they are excluded.)
        linked_uids = {sl.uid for sl in store.sync_links(url).values()}
        for item in by_uid.values():
            if item.uid in linked_uids or item.completed or not item.summary.strip():
                continue
            task = store.create_task(
                link.list_id,
                item.summary,
                item.description,
                due_date=date.fromisoformat(item.due) if item.due else None,
            )
            store.set_sync_link(task.id, SyncLink(url, item.uid, item.fields()))

    def _handle_missing_item(self, link: _ListLink, task_id: int) -> None:
        """Delete the task once its item has stayed missing from the server."""
        grace = DELETION_GRACE.total_seconds()
        first_seen = link.missing.setdefault(task_id, time.monotonic())
        if time.monotonic() - first_seen < grace:
            # Look again after the grace period instead of waiting for the
            # next poll.
            self._schedule_recheck(link, grace + 1)
            return
        link.missing.pop(task_id, None)
        self._store.delete_task(task_id)
        self._store.remove_sync_link(task_id)

    async def _merge(
        self,
        link: _ListLink,
        task: StoredTask,
        item: RemoteItem,
        sl: SyncLink,
    ) -> None:
        """Three-way merge one linked task/item pair against its snapshot."""
        ours = _task_fields(task)
        theirs = item.fields()
        merged: dict[str, Any] = {}
        for name in _FIELDS:
            if ours[name] == theirs[name] or ours[name] != sl.snapshot.get(name):
                merged[name] = ours[name]  # equal, or Home Upkeep changed
            else:
                merged[name] = theirs[name]  # only the server side changed

        if merged != ours:
            self._apply_to_task(task, ours, merged)
        if merged != theirs and not await link.remote.async_update(sl.uid, merged):
            return  # keep the old snapshot so the next pass retries
        if merged != sl.snapshot:
            self._store.set_sync_link(
                task.id, SyncLink(link.collection_url, sl.uid, merged)
            )

    def _apply_to_task(
        self, task: StoredTask, ours: dict[str, Any], merged: dict[str, Any]
    ) -> None:
        """Copy the fields that changed on the server into the task."""
        kwargs: dict[str, Any] = {}
        if merged["summary"] != ours["summary"] and merged["summary"].strip():
            kwargs["title"] = merged["summary"]
        if merged["description"] != ours["description"]:
            kwargs["description"] = merged["description"]
        if merged["due"] != ours["due"]:
            kwargs["due_date"] = (
                date.fromisoformat(merged["due"]) if merged["due"] else None
            )
        if merged["completed"] != ours["completed"]:
            kwargs["completed"] = merged["completed"]
        updated = self._store.update_task(task.id, **kwargs)
        if (
            updated is not None
            and kwargs.get("completed")
            and not ours["completed"]
            and updated.reschedule_period
        ):
            # Same as completing a recurring task from the panel/todo UI.
            self._store.create_followup_task(updated)
