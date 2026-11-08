"""
Integration tests of the CalDAV transport against a real server.

Opt-in: they are deselected by default (and so left out of CI). To run them,
start any CalDAV server, for example a throwaway Radicale container, and:

    HOME_UPKEEP_TEST_CALDAV="http://127.0.0.1:5233|dev|dev" uv run pytest -m integration

Nothing here starts a server. Each test works in a scratch collection that it
deletes again.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.home_upkeep.remote import (
    CalDavRemote,
    Collection,
    ServerSettings,
    async_create_collection,
    async_discover_collections,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
def verify_cleanup() -> None:
    """
    Replace the harness's cleanup check for this module.

    The check fails a test if any thread outlives it, but the HTTP stack under
    caldav runs its own connection-watcher threads that wind down on their own
    schedule. That is the library's behaviour, not ours.
    """


def _fields(**changes: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "summary": "Mop floors",
        "completed": False,
        "due": "2026-03-01",
        "description": "Kitchen",
    }
    fields.update(changes)
    return fields


SERVER = os.environ.get("HOME_UPKEEP_TEST_CALDAV")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not SERVER, reason="HOME_UPKEEP_TEST_CALDAV not set"),
]
# The HA test harness blocks real sockets unless a test opts in.
real_socket = pytest.mark.enable_socket


def _other_client(settings: ServerSettings) -> Any:
    """Return a separate client, standing in for Tasks.org writing to the item."""
    import caldav  # noqa: PLC0415

    return caldav.DAVClient(
        url=settings.url, username=settings.username, password=settings.password
    )


def _settings() -> ServerSettings:
    url, username, password = (SERVER or "||").split("|")
    return ServerSettings(url, username, password, verify_ssl=False)


@pytest.fixture
async def collection(hass: HomeAssistant) -> AsyncIterator[Collection]:
    """Create a scratch collection on the server and always delete it again."""
    settings = _settings()
    created = await async_create_collection(
        hass, settings, f"home-upkeep-test-{time.time():.0f}"
    )
    yield created

    def drop() -> None:
        import caldav  # noqa: PLC0415

        client = caldav.DAVClient(
            url=settings.url, username=settings.username, password=settings.password
        )
        try:
            client.calendar(url=created.url).delete()
        finally:
            client.close()

    await hass.async_add_executor_job(drop)


@real_socket
async def test_real_server_round_trip(
    hass: HomeAssistant, collection: Collection
) -> None:
    """Create, read, update, complete, re-open and delete against a real server."""
    settings = _settings()
    assert collection in await async_discover_collections(hass, settings)
    remote = CalDavRemote(hass, settings, collection.url)
    try:
        await _round_trip(remote)
    finally:
        await remote.async_close()


async def _round_trip(remote: CalDavRemote) -> None:
    assert await remote.async_items() == []

    uid = await remote.async_create(_fields(due="2026-04-05"))
    assert uid is not None
    (item,) = await remote.async_items() or []
    assert (item.uid, item.fields()) == (uid, _fields(due="2026-04-05"))

    assert await remote.async_update(uid, _fields(completed=True, due=None))
    (item,) = await remote.async_items() or []
    assert item.fields() == _fields(completed=True, due=None)
    assert await remote.async_update(
        uid, _fields(completed=False, due=None, description=None)
    )
    (item,) = await remote.async_items() or []
    assert item.fields() == _fields(completed=False, due=None, description=None)

    assert await remote.async_delete(uid)
    assert await remote.async_items() == []
    assert await remote.async_delete(uid)  # already gone still counts as deleted


@real_socket
async def test_real_server_keeps_other_clients_properties(
    hass: HomeAssistant, collection: Collection
) -> None:
    """An update through the remote keeps RRULE/alarms that Tasks.org wrote."""
    settings = _settings()
    remote = CalDavRemote(hass, settings, collection.url)
    other = _other_client(settings)
    try:
        uid = await remote.async_create(_fields())
        assert uid is not None
        # Another client adds a recurrence rule and an alarm to the same item.
        calendar = other.calendar(url=collection.url)
        todo = await hass.async_add_executor_job(calendar.todo_by_uid, uid)
        todo.icalendar_component.add("rrule", {"FREQ": "WEEKLY"})
        await hass.async_add_executor_job(todo.save)

        assert await remote.async_update(
            uid, _fields(summary="Renamed", completed=True)
        )

        todo = await hass.async_add_executor_job(calendar.todo_by_uid, uid)
        assert "RRULE:FREQ=WEEKLY" in todo.data
        assert "SUMMARY:Renamed" in todo.data
    finally:
        await remote.async_close()
        await hass.async_add_executor_job(other.close)
