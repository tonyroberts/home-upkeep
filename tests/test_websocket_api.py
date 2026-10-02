"""Tests for the home_upkeep websocket command surface."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from custom_components.home_upkeep.store import async_get_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.typing import WebSocketGenerator


async def test_lists_crud(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Create, list, rename, and delete a list over the websocket API."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "home_upkeep/lists/create", "name": "Cleaning"}
    )
    resp = await client.receive_json()
    assert resp["success"]
    lst = resp["result"]
    assert lst["name"] == "Cleaning"
    list_id = lst["id"]

    await client.send_json_auto_id({"type": "home_upkeep/lists/list"})
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"] == [lst]

    await client.send_json_auto_id(
        {"type": "home_upkeep/lists/update", "list_id": list_id, "name": "Chores"}
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["name"] == "Chores"

    await client.send_json_auto_id(
        {"type": "home_upkeep/lists/update", "list_id": 999, "name": "Nope"}
    )
    resp = await client.receive_json()
    assert resp["success"] is False
    assert resp["error"]["code"] == "not_found"

    await client.send_json_auto_id(
        {"type": "home_upkeep/lists/delete", "list_id": list_id}
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"] == {"success": True}

    await client.send_json_auto_id(
        {"type": "home_upkeep/lists/delete", "list_id": list_id}
    )
    resp = await client.receive_json()
    assert resp["success"] is False


async def test_tasks_crud(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Create, get, list, update, and delete a task over the websocket API."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "home_upkeep/lists/create", "name": "Cleaning"}
    )
    resp = await client.receive_json()
    list_id = resp["result"]["id"]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": list_id,
            "title": "Mop floors",
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    task = resp["result"]
    assert task["title"] == "Mop floors"
    assert task["completed"] is False
    assert task["reschedule_base"] == "completed"
    task_id = task["id"]

    await client.send_json_auto_id(
        {"type": "home_upkeep/tasks/get", "task_id": task_id}
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"] == task

    await client.send_json_auto_id({"type": "home_upkeep/tasks/get", "task_id": 999})
    resp = await client.receive_json()
    assert resp["success"] is False
    assert resp["error"]["code"] == "not_found"

    await client.send_json_auto_id(
        {"type": "home_upkeep/tasks/list", "list_id": list_id}
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"] == [task]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "title": "Mop all floors",
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["task"]["title"] == "Mop all floors"
    assert resp["result"]["created_task"] is None

    await client.send_json_auto_id(
        {"type": "home_upkeep/tasks/delete", "task_id": task_id}
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"] == {"success": True}

    await client.send_json_auto_id(
        {"type": "home_upkeep/tasks/delete", "task_id": task_id}
    )
    resp = await client.receive_json()
    assert resp["success"] is False


async def test_tasks_create_validates_reschedule_period(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """An invalid reschedule_period is rejected before it reaches the store."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": 1,
            "title": "Mop floors",
            "reschedule_period": "not-a-period",
        }
    )
    resp = await client.receive_json()
    assert resp["success"] is False
    assert resp["error"]["code"] == "invalid_format"


async def test_tasks_update_completion_creates_followup(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Completing a task with a reschedule_period creates a follow-up task."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": 1,
            "title": "Mop floors",
            "due_date": "2026-03-01",
            "reschedule_period": "1m",
        }
    )
    resp = await client.receive_json()
    task_id = resp["result"]["id"]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "completed": True,
            "updated_at": "2026-03-05T10:00:00-05:00",
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["task"]["completed"] is True

    created_task = resp["result"]["created_task"]
    assert created_task is not None
    assert created_task["title"] == "Mop floors"
    assert created_task["completed"] is False
    assert created_task["due_date"] == date(2026, 4, 5).isoformat()
    assert created_task["reschedule_period"] == "1m"


async def test_tasks_update_completion_without_period_has_no_followup(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Completing a task with no reschedule_period creates no follow-up."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "home_upkeep/tasks/create", "list_id": 1, "title": "One-off"}
    )
    resp = await client.receive_json()
    task_id = resp["result"]["id"]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "completed": True,
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["created_task"] is None


async def test_tasks_update_accepts_explicit_null_fields(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """
    Explicit null for due_date/reschedule_period/completed_at is valid.

    The Lit edit-task dialog sends explicit `null` (not an omitted key) to
    represent "field is unset", matching the add-on REST API's nullable
    fields. A regression once made the whole message fail schema
    validation whenever any of these were null, silently dropping every
    other field in the same update (e.g. prohibited_months).
    """
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": 1,
            "title": "Mop floors",
            "due_date": "2026-03-01",
            "reschedule_period": "1m",
            "prohibited_months": [7, 8],
        }
    )
    resp = await client.receive_json()
    task_id = resp["result"]["id"]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "due_date": "2026-03-01",
            "reschedule_period": "1m",
            "reschedule_base": "completed",
            "completed_at": None,
            "prohibited_months": [7, 8, 3],
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["task"]["prohibited_months"] == [7, 8, 3]


async def test_tasks_update_explicit_null_clears_field(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """
    Explicit null for a nullable field actually clears it in storage.

    Regression test: `store.update_task` used to guard every field with
    `if x is not None`, which is indistinguishable from "field omitted",
    so an explicit `null` sent by the frontend to clear e.g. `due_date`
    was silently dropped instead of clearing the field.
    """
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": 1,
            "title": "Mop floors",
            "due_date": "2026-03-01",
            "reschedule_period": "1m",
        }
    )
    resp = await client.receive_json()
    task_id = resp["result"]["id"]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "due_date": None,
            "reschedule_period": None,
            "reschedule_base": None,
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    task = resp["result"]["task"]
    assert task["due_date"] is None
    assert task["reschedule_period"] is None
    assert task["reschedule_base"] is None

    # Omitting the field entirely on a later update must leave it alone.
    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "title": "Mop floors twice",
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["task"]["due_date"] is None


async def test_tasks_update_resave_of_completed_task_has_no_duplicate_followup(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """
    Re-saving an already-completed recurring task creates no extra follow-up.

    Regression test: `handle_tasks_update` used to create a follow-up
    whenever the task was completed and had a `reschedule_period` *after*
    the update, regardless of whether this update was the one that
    completed it — so editing an unrelated field on an already-completed
    recurring task (which the edit dialog always re-sends `completed` for)
    created a duplicate follow-up task on every save.
    """
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": 1,
            "title": "Mop floors",
            "reschedule_period": "1m",
        }
    )
    resp = await client.receive_json()
    task_id = resp["result"]["id"]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "completed": True,
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["created_task"] is not None

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/update",
            "task_id": task_id,
            "title": "Mop floors (renamed)",
            "completed": True,
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["created_task"] is None


async def test_tasks_snooze(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Snoozing a task pushes its due date forward by the given period."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": 1,
            "title": "Water plants",
            "due_date": "2026-03-01",
        }
    )
    resp = await client.receive_json()
    task_id = resp["result"]["id"]

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/snooze",
            "task_id": task_id,
            "period": "5d",
            "updated_at": "2026-03-01T09:00:00+00:00",
        }
    )
    resp = await client.receive_json()
    assert resp["success"]
    assert resp["result"]["due_date"] == date(2026, 3, 6).isoformat()

    await client.send_json_auto_id(
        {"type": "home_upkeep/tasks/snooze", "task_id": 999, "period": "5d"}
    )
    resp = await client.receive_json()
    assert resp["success"] is False
    assert resp["error"]["code"] == "not_found"


async def test_subscribe_receives_events(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """
    Subscribers receive JSON-safe events for every mutation.

    The dispatcher fires synchronously from inside the store mutation, so
    the pushed event can arrive over the wire before the RPC result for the
    very same command — don't assume ordering between the two.
    """
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "home_upkeep/subscribe"})
    resp = await client.receive_json()
    assert resp["success"]

    await client.send_json_auto_id(
        {"type": "home_upkeep/lists/create", "name": "Cleaning"}
    )
    msg_1 = await client.receive_json()
    msg_2 = await client.receive_json()
    result = next(m["result"] for m in (msg_1, msg_2) if m["type"] == "result")
    event = next(m["event"] for m in (msg_1, msg_2) if m["type"] == "event")
    list_id = result["id"]
    assert event["type"] == "list_created"
    assert event["list"]["name"] == "Cleaning"

    await client.send_json_auto_id(
        {
            "type": "home_upkeep/tasks/create",
            "list_id": list_id,
            "title": "Mop floors",
        }
    )
    msg_1 = await client.receive_json()
    msg_2 = await client.receive_json()
    event = next(m["event"] for m in (msg_1, msg_2) if m["type"] == "event")
    assert event["type"] == "task_created"
    assert event["task"]["title"] == "Mop floors"


async def test_migration_status_reflects_store_flag(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """migration_status returns the store's migrated_from_addon flag."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "home_upkeep/migration_status"})
    resp = await client.receive_json()
    # No Supervisor in the test environment, so addon_running is always False.
    assert resp["result"] == {"migrated_from_addon": False, "addon_running": False}

    store = async_get_store(hass)
    await store.async_mark_migrated_from_addon()

    await client.send_json_auto_id({"type": "home_upkeep/migration_status"})
    resp = await client.receive_json()
    assert resp["result"] == {"migrated_from_addon": True, "addon_running": False}
