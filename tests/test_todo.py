"""Tests for the home_upkeep todo entities."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from custom_components.home_upkeep.const import DOMAIN
from custom_components.home_upkeep.store import async_get_store

from .test_store import _imported_list, _imported_task

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

EXPECTED_TASKS = 2


def _todo_entity_id(hass: HomeAssistant, list_id: int) -> str | None:
    registry = er.async_get(hass)
    return registry.async_get_entity_id("todo", DOMAIN, f"home_upkeep_list_{list_id}")


async def test_entity_created_per_list(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Each existing list gets its own todo entity on startup."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    await hass.async_block_till_done()

    entity_id = _todo_entity_id(hass, lst.id)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.name == "Cleaning"


async def test_todo_items_reflect_tasks(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """todo_items mirror the store's tasks for that list."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    task = store.create_task(
        lst.id,
        "Mop floors",
        "Kitchen",
        due_date=date(2026, 3, 1),
    )
    await hass.async_block_till_done()

    entity_id = _todo_entity_id(hass, lst.id)
    state = hass.states.get(entity_id)
    # The todo component reflects item counts on the entity state's attrs.
    assert state.attributes["supported_features"]

    resp = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    items = resp[entity_id]["items"]
    assert len(items) == 1
    assert items[0]["summary"] == "Mop floors"
    assert items[0]["uid"] == str(task.id)
    assert items[0]["status"] == "needs_action"
    assert items[0]["due"] == "2026-03-01"


async def test_add_item_service_creates_task(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """todo.add_item creates a task in the underlying list."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    await hass.async_block_till_done()
    entity_id = _todo_entity_id(hass, lst.id)

    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": entity_id, "item": "Water plants"},
        blocking=True,
    )
    await hass.async_block_till_done()

    [task] = store.list_tasks(lst.id)
    assert task.title == "Water plants"
    assert task.completed is False


async def test_update_item_service_updates_task(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """todo.update_item (status=completed) completes the task."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop floors", None)
    await hass.async_block_till_done()
    entity_id = _todo_entity_id(hass, lst.id)

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": entity_id, "item": str(task.id), "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert store.get_task(task.id).completed is True


async def test_completing_recurring_task_creates_followup(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Completing a recurring task via todo creates the rescheduled follow-up."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop floors", None, reschedule_period="1m")
    await hass.async_block_till_done()
    entity_id = _todo_entity_id(hass, lst.id)

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": entity_id, "item": str(task.id), "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    tasks = store.list_tasks(lst.id)
    assert len(tasks) == EXPECTED_TASKS
    followup = next(t for t in tasks if t.id != task.id)
    assert followup.completed is False
    assert followup.reschedule_period == "1m"
    assert followup.due_date is not None

    # Re-saving the already-completed task must not create another follow-up.
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": entity_id, "item": str(task.id), "rename": "Mop floors!"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(store.list_tasks(lst.id)) == EXPECTED_TASKS


async def test_completing_non_recurring_task_creates_no_followup(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Completing a one-off task via todo creates nothing extra."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop floors", None)
    await hass.async_block_till_done()
    entity_id = _todo_entity_id(hass, lst.id)

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": entity_id, "item": str(task.id), "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(store.list_tasks(lst.id)) == 1


async def test_remove_item_service_deletes_task(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """todo.remove_item deletes the underlying task."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop floors", None)
    await hass.async_block_till_done()
    entity_id = _todo_entity_id(hass, lst.id)

    await hass.services.async_call(
        "todo",
        "remove_item",
        {"entity_id": entity_id, "item": str(task.id)},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert store.get_task(task.id) is None


async def test_list_created_and_deleted_syncs_entities(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Creating/deleting a list adds/removes its todo entity."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    await hass.async_block_till_done()

    entity_id = _todo_entity_id(hass, lst.id)
    assert hass.states.get(entity_id) is not None

    store.delete_list(lst.id)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id) is None


async def test_list_renamed_updates_entity_name(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Renaming a list updates its todo entity's friendly name."""
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    await hass.async_block_till_done()
    entity_id = _todo_entity_id(hass, lst.id)

    store.rename_list(lst.id, "Chores")
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).name == "Chores"


async def test_import_creates_entity_and_refreshes_overwritten_list(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """
    An import creates entities for new lists and refreshes overwritten ones.

    Regression test: the store's `async_import` dispatches a
    `data_imported` event (not `list_created`), which the todo platform's
    dispatcher handler used to ignore entirely, leaving imported lists
    without a todo entity until Home Assistant restarted.
    """
    store = async_get_store(hass)
    existing = store.create_list("Cleaning")
    store.create_task(existing.id, "Old task", None)
    await hass.async_block_till_done()

    existing_entity_id = _todo_entity_id(hass, existing.id)
    assert existing_entity_id is not None

    imported_list = _imported_list(existing.id, "Cleaning (imported)")
    new_list = _imported_list(999, "New list")
    imported_task = _imported_task(1, existing.id, "Fresh task")
    new_task = _imported_task(2, 999, "New list task")

    await store.async_import(
        [imported_list, new_list],
        [imported_task, new_task],
        overwrite_list_ids={existing.id},
    )
    await hass.async_block_till_done()

    # The overwritten list's todo entity still exists and reflects the
    # imported task (its old task was replaced entirely).
    assert hass.states.get(existing_entity_id).name == "Cleaning (imported)"
    [task] = store.list_tasks(existing.id)
    assert task.title == "Fresh task"

    # The new list gets its own todo entity without a HA restart.
    new_entity_id = _todo_entity_id(hass, new_list.id)
    assert new_entity_id is not None
    assert hass.states.get(new_entity_id) is not None
