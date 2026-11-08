"""Tests for the CalDAV transport: pure iCalendar helpers and a real server."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from caldav.lib.error import NotFoundError
from icalendar import Calendar

from custom_components.home_upkeep import remote as remote_module
from custom_components.home_upkeep.remote import (
    CalDavRemote,
    ServerSettings,
    apply_fields,
    build_todo,
    parse_todo,
)

TODO = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Tasks.org//EN
BEGIN:VTODO
UID:abc-123
DTSTAMP:20260101T000000Z
SUMMARY:Mop floors
STATUS:NEEDS-ACTION
DUE;VALUE=DATE:20260301
DESCRIPTION:Kitchen
RRULE:FREQ=WEEKLY
CATEGORIES:chores
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:reminder
END:VALARM
END:VTODO
END:VCALENDAR
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


def _apply(ics: str, **changes: object) -> str:
    """Apply field changes to the VTODO in `ics` and return the new document."""
    calendar = Calendar.from_ical(ics)
    (component,) = calendar.walk("VTODO")
    apply_fields(component, _fields(**changes))
    return calendar.to_ical().decode()


def test_parse_reads_the_synced_fields() -> None:
    """A VTODO is reduced to summary, completed, due date and description."""
    item = parse_todo(TODO)
    assert item is not None
    assert item.fields() == _fields()
    assert item.uid == "abc-123"


def test_parse_treats_cancelled_as_completed_and_datetime_due_as_a_date() -> None:
    """CANCELLED counts as done, and a due time is reduced to its date."""
    ics = TODO.replace("STATUS:NEEDS-ACTION", "STATUS:CANCELLED").replace(
        "DUE;VALUE=DATE:20260301", "DUE:20260301T093000Z"
    )
    item = parse_todo(ics)
    assert item is not None
    assert item.completed is True
    assert item.due == "2026-03-01"


def test_parse_ignores_documents_without_a_usable_todo() -> None:
    """Events, garbage and a UID-less VTODO yield nothing."""
    assert parse_todo("not ics at all") is None
    assert parse_todo("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n") is None
    assert parse_todo(TODO.replace("UID:abc-123\n", "")) is None


def test_apply_changes_only_what_differs_and_keeps_other_properties() -> None:
    """Recurrence, alarms and categories set by other clients survive an update."""
    updated = _apply(TODO, summary="Mop all floors", completed=True, due=None)

    item = parse_todo(updated)
    assert item is not None
    assert (item.summary, item.completed, item.due) == ("Mop all floors", True, None)
    assert item.description == "Kitchen"
    for kept in ("RRULE:FREQ=WEEKLY", "CATEGORIES:chores", "BEGIN:VALARM"):
        assert kept in updated
    assert "STATUS:COMPLETED" in updated
    assert "PERCENT-COMPLETE:100" in updated
    assert "COMPLETED:" in updated


def test_apply_reopens_cleanly() -> None:
    """Re-opening drops the completion markers."""
    done = _apply(TODO, completed=True)
    reopened = _apply(done, completed=False)

    item = parse_todo(reopened)
    assert item is not None
    assert item.completed is False
    assert "PERCENT-COMPLETE" not in reopened
    assert "\nCOMPLETED:" not in reopened
    assert "STATUS:NEEDS-ACTION" in reopened


def test_apply_leaves_a_due_time_alone_when_the_date_is_unchanged() -> None:
    """Only the date is synced, so another client's due time is not flattened."""
    ics = TODO.replace("DUE;VALUE=DATE:20260301", "DUE:20260301T093000Z")

    updated = _apply(ics, summary="Renamed")

    assert "DUE:20260301T093000Z" in updated


def test_build_creates_a_document_that_parses_back() -> None:
    """A new item round-trips through build_todo and parse_todo."""
    ics = build_todo("new-uid", _fields(description=None, due=None))

    item = parse_todo(ics)
    assert item is not None
    assert item.uid == "new-uid"
    assert item.fields() == _fields(description=None, due=None)
    assert "STATUS:NEEDS-ACTION" in ics


# ---- how CalDavRemote handles its connection (mocked; no server) ----


class _FakeHass:
    """Just enough of HomeAssistant to run executor jobs."""

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    """Replace the caldav client factory; every client it hands out is recorded."""
    created: list[MagicMock] = []

    def make_client(_settings: ServerSettings) -> MagicMock:
        client = MagicMock()
        client.calendar.return_value.search.return_value = []
        created.append(client)
        return client

    monkeypatch.setattr(remote_module, "_client", make_client)
    return created


def _remote() -> CalDavRemote:
    settings = ServerSettings("http://server", "u", "p")
    return CalDavRemote(_FakeHass(), settings, "http://server/u/tasks/")  # type: ignore[arg-type]


async def test_the_connection_is_reused_between_calls(clients: list[MagicMock]) -> None:
    """One client serves many calls instead of reconnecting every time."""
    remote = _remote()

    assert await remote.async_items() == []
    assert await remote.async_items() == []

    assert len(clients) == 1
    clients[0].close.assert_not_called()


async def test_close_releases_the_client_and_the_next_call_reconnects(
    clients: list[MagicMock],
) -> None:
    """async_close closes the client; using the remote again opens a new one."""
    remote = _remote()
    await remote.async_items()

    await remote.async_close()
    clients[0].close.assert_called_once()

    await remote.async_items()
    assert len(clients) == 2  # noqa: PLR2004


async def test_a_network_error_closes_the_client_and_reports_unreadable(
    clients: list[MagicMock],
) -> None:
    """After a network error the client is dropped, so the next call starts fresh."""
    remote = _remote()
    await remote.async_items()
    clients[0].calendar.return_value.search.side_effect = requests.ConnectionError

    assert await remote.async_items() is None  # unreadable, not "no items"
    clients[0].close.assert_called_once()

    clients[0].calendar.return_value.search.side_effect = None
    assert await remote.async_items() == []
    assert len(clients) == 2  # noqa: PLR2004


async def test_deleting_an_item_that_is_already_gone_counts_as_deleted(
    clients: list[MagicMock],
) -> None:
    """A 404 on delete is success: the goal state is reached."""
    remote = _remote()
    await remote.async_items()
    clients[0].calendar.return_value.todo_by_uid.side_effect = NotFoundError

    assert await remote.async_delete("gone") is True
