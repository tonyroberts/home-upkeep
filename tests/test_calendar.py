"""Tests for the home_upkeep calendar entities."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.home_upkeep.const import DOMAIN
from custom_components.home_upkeep.models import StoredList
from custom_components.home_upkeep.store import async_get_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

_OVERDUE_UNIQUE_ID = "home_upkeep_calendar_overdue"


@pytest.fixture(autouse=True)
async def _utc(hass: HomeAssistant) -> None:
    """Make local time equal frozen (UTC) time so 'today' is unambiguous."""
    await hass.config.async_set_time_zone("UTC")


def _calendar_entity_id(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("calendar", DOMAIN, unique_id)
    assert entity_id is not None, f"no calendar entity registered for {unique_id}"
    return entity_id


async def _events(
    hass: HomeAssistant, entity_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    """Fetch events through HA's public `calendar.get_events` service."""
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": entity_id,
            "start_date_time": datetime.combine(
                start, datetime.min.time(), dt_util.get_default_time_zone()
            ),
            "end_date_time": datetime.combine(
                end, datetime.min.time(), dt_util.get_default_time_zone()
            ),
        },
        blocking=True,
        return_response=True,
    )
    return response[entity_id]["events"]


async def test_calendar_created_per_list(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Each list gets its own calendar entity named after the list."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    await hass.async_block_till_done()

    entity_id = _calendar_entity_id(hass, f"home_upkeep_calendar_{lst.id}")
    assert hass.states.get(entity_id).name == "Cleaning"


async def test_calendar_shows_tasks_on_their_due_dates(
    setup_integration: MockConfigEntry, hass: HomeAssistant, freezer: Any
) -> None:
    """Past, present and future due dates all appear on their own day."""
    freezer.move_to("2026-10-10 12:00:00")
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    store.create_task(lst.id, "Past", None, due_date=date(2026, 10, 1))
    store.create_task(lst.id, "Today", None, due_date=date(2026, 10, 10))
    store.create_task(lst.id, "Future", None, due_date=date(2026, 10, 15))
    store.create_task(lst.id, "No due date", None)
    await hass.async_block_till_done()

    entity_id = _calendar_entity_id(hass, f"home_upkeep_calendar_{lst.id}")
    events = await _events(hass, entity_id, date(2026, 9, 1), date(2026, 11, 1))

    assert {e["summary"]: e["start"] for e in events} == {
        "Past": "2026-10-01",
        "Today": "2026-10-10",
        "Future": "2026-10-15",
    }
    # All-day events: HA expects an exclusive end date.
    assert {e["summary"]: e["end"] for e in events}["Past"] == "2026-10-02"


async def test_calendar_keeps_completed_tasks_on_their_due_date(
    setup_integration: MockConfigEntry, hass: HomeAssistant, freezer: Any
) -> None:
    """Completed tasks stay in the per-list calendar as history."""
    freezer.move_to("2026-10-10 12:00:00")
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    store.create_task(lst.id, "Done", None, completed=True, due_date=date(2026, 10, 1))
    await hass.async_block_till_done()

    entity_id = _calendar_entity_id(hass, f"home_upkeep_calendar_{lst.id}")
    events = await _events(hass, entity_id, date(2026, 10, 1), date(2026, 10, 2))

    assert [e["summary"] for e in events] == ["Done"]


async def test_calendar_updates_when_task_changes(
    setup_integration: MockConfigEntry, hass: HomeAssistant, freezer: Any
) -> None:
    """Moving a task's due date moves its event (reads live from the store)."""
    freezer.move_to("2026-10-10 12:00:00")
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop", None, due_date=date(2026, 10, 12))
    store.update_task(task.id, due_date=date(2026, 10, 20))
    await hass.async_block_till_done()

    entity_id = _calendar_entity_id(hass, f"home_upkeep_calendar_{lst.id}")
    events = await _events(hass, entity_id, date(2026, 10, 1), date(2026, 11, 1))

    assert [e["start"] for e in events] == ["2026-10-20"]


async def test_overdue_calendar_pins_incomplete_overdue_tasks_to_today(
    setup_integration: MockConfigEntry, hass: HomeAssistant, freezer: Any
) -> None:
    """Overdue incomplete tasks from every list land on today with a hint."""
    freezer.move_to("2026-10-10 12:00:00")
    store = async_get_store(hass)
    a = store.create_list("Cleaning")
    b = store.create_list("Garden")
    store.create_task(a.id, "Mop", None, due_date=date(2026, 10, 7))
    store.create_task(b.id, "Mow", None, due_date=date(2026, 10, 9))
    store.create_task(a.id, "Done", None, completed=True, due_date=date(2026, 10, 1))
    store.create_task(a.id, "Due today", None, due_date=date(2026, 10, 10))
    store.create_task(a.id, "Upcoming", None, due_date=date(2026, 10, 12))
    store.create_task(a.id, "No due date", None)
    await hass.async_block_till_done()

    entity_id = _calendar_entity_id(hass, _OVERDUE_UNIQUE_ID)
    events = await _events(hass, entity_id, date(2026, 10, 10), date(2026, 10, 17))

    assert {e["summary"]: e["start"] for e in events} == {
        "Mop (3 days overdue)": "2026-10-10",
        "Mow (1 day overdue)": "2026-10-10",
    }


async def test_overdue_calendar_drops_task_once_checked_off(
    setup_integration: MockConfigEntry, hass: HomeAssistant, freezer: Any
) -> None:
    """Checking a task off removes it from the overdue calendar."""
    freezer.move_to("2026-10-10 12:00:00")
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop", None, due_date=date(2026, 10, 7))
    store.update_task(task.id, completed=True)
    await hass.async_block_till_done()

    entity_id = _calendar_entity_id(hass, _OVERDUE_UNIQUE_ID)
    events = await _events(hass, entity_id, date(2026, 10, 10), date(2026, 10, 17))

    assert events == []


async def test_overdue_calendar_follows_today_after_midnight(
    setup_integration: MockConfigEntry, hass: HomeAssistant, freezer: Any
) -> None:
    """The entity state flips to the new day's overdue event at midnight."""
    freezer.move_to("2026-10-10 12:00:00")
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    store.create_task(lst.id, "Mop", None, due_date=date(2026, 10, 10))
    await hass.async_block_till_done()
    entity_id = _calendar_entity_id(hass, _OVERDUE_UNIQUE_ID)
    assert hass.states.get(entity_id).state == "off"

    freezer.move_to("2026-10-11 00:00:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["message"] == "Mop (1 day overdue)"


async def test_calendar_renamed_when_import_overwrites_list(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """An import that overwrites a list under the same ID renames its calendar."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    await hass.async_block_till_done()
    entity_id = _calendar_entity_id(hass, f"home_upkeep_calendar_{lst.id}")

    await store.async_import(
        [
            StoredList(
                id=lst.id,
                name="Garden",
                created_at=lst.created_at,
                updated_at=lst.updated_at,
            )
        ],
        [],
        overwrite_list_ids={lst.id},
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).name == "Garden"
