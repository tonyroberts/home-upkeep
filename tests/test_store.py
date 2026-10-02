"""Tests for HomeUpkeepStore CRUD, dispatcher notifications, and persistence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pytest_homeassistant_custom_component.common import flush_store

from custom_components.home_upkeep.const import SIGNAL_UPKEEP_CHANGED
from custom_components.home_upkeep.models import StoredList, StoredTask
from custom_components.home_upkeep.store import HomeUpkeepStore, ImportConflictError

# ruff (TC002) wants type-only imports under TYPE_CHECKING to avoid an
# unnecessary runtime import, since `from __future__ import annotations`
# means annotations are never evaluated at runtime anyway.
if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_create_and_get_list(hass: HomeAssistant) -> None:
    """Creating a list makes it retrievable and listed."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    assert lst.id == 1
    assert lst.name == "Cleaning"
    assert store.get_list(1) is lst
    assert store.list_lists() == [lst]


async def test_rename_and_delete_list(hass: HomeAssistant) -> None:
    """Renaming and deleting a list behaves, including missing-id cases."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    renamed = store.rename_list(lst.id, "Chores")
    assert renamed is not None
    assert renamed.name == "Chores"

    assert store.rename_list(999, "Nope") is None

    assert store.delete_list(lst.id) is True
    assert store.get_list(lst.id) is None
    assert store.delete_list(lst.id) is False


async def test_create_task_defaults(hass: HomeAssistant) -> None:
    """A freshly created task gets the documented defaults."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop floors", None)

    assert task.id == 1
    assert task.list_id == lst.id
    assert task.completed is False
    assert task.reschedule_base == "completed"
    assert task.prohibited_months == []
    assert task.constraints == []
    assert store.list_tasks(lst.id) == [task]


async def test_update_task_sets_completed_at(hass: HomeAssistant) -> None:
    """Toggling `completed` sets/clears `completed_at`; unknown id is a no-op."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop floors", None)

    updated = store.update_task(task.id, completed=True)
    assert updated is not None
    assert updated.completed is True
    assert updated.completed_at is not None

    reverted = store.update_task(task.id, completed=False)
    assert reverted is not None
    assert reverted.completed_at is None

    assert store.update_task(999, title="nope") is None


async def test_update_task_distinguishes_omitted_from_explicit_none(
    hass: HomeAssistant,
) -> None:
    """
    Omitting a nullable kwarg leaves it alone; passing `None` clears it.

    `update_task`'s nullable kwargs (`due_date`, `reschedule_period`,
    `reschedule_base`, `completed_at`) default to a sentinel rather than
    `None` specifically so the two cases aren't conflated.
    """
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    task = store.create_task(
        lst.id,
        "Mop floors",
        None,
        due_date=date(2026, 3, 1),
        reschedule_period="1m",
        reschedule_base="due",
    )

    # Omitted entirely: unchanged.
    unchanged = store.update_task(task.id, title="Mop floors (renamed)")
    assert unchanged is not None
    assert unchanged.due_date == date(2026, 3, 1)
    assert unchanged.reschedule_period == "1m"
    assert unchanged.reschedule_base == "due"

    # Explicit None: cleared.
    cleared = store.update_task(
        task.id,
        due_date=None,
        reschedule_period=None,
        reschedule_base=None,
    )
    assert cleared is not None
    assert cleared.due_date is None
    assert cleared.reschedule_period is None
    assert cleared.reschedule_base is None


async def test_delete_task(hass: HomeAssistant) -> None:
    """Deleting a task removes it; deleting again reports not-found."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    task = store.create_task(lst.id, "Mop floors", None)

    assert store.delete_task(task.id) is True
    assert store.get_task(task.id) is None
    assert store.delete_task(task.id) is False


async def test_delete_list_cascades_tasks(hass: HomeAssistant) -> None:
    """Deleting a list also removes all tasks that belonged to it."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    store.create_task(lst.id, "Mop floors", None)
    store.create_task(lst.id, "Dust shelves", None)

    assert store.delete_list(lst.id) is True
    assert store.list_tasks(lst.id) == []


async def test_dispatcher_signal_fires_on_mutation(hass: HomeAssistant) -> None:
    """Every mutation notifies dispatcher subscribers with a typed event."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    events: list[dict] = []
    async_dispatcher_connect(hass, SIGNAL_UPKEEP_CHANGED, events.append)

    lst = store.create_list("Cleaning")
    await hass.async_block_till_done()
    assert events[-1]["type"] == "list_created"

    store.create_task(lst.id, "Mop floors", None)
    await hass.async_block_till_done()
    assert events[-1]["type"] == "task_created"


async def test_persistence_round_trip(hass: HomeAssistant) -> None:
    """Data saved by one store instance loads correctly in a fresh one."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    lst = store.create_list("Cleaning")
    store.create_task(
        lst.id,
        "Mop floors",
        "Kitchen and hallway",
        due_date=date(2026, 3, 1),
        reschedule_period="1m",
        prohibited_months=[7, 8],
    )

    await flush_store(store._store)  # noqa: SLF001

    reloaded = HomeUpkeepStore(hass)
    await reloaded.async_load()

    assert [lst.name for lst in reloaded.list_lists()] == ["Cleaning"]
    [task] = reloaded.list_tasks(lst.id)
    assert task.title == "Mop floors"
    assert task.due_date == date(2026, 3, 1)
    assert task.reschedule_period == "1m"
    assert task.prohibited_months == [7, 8]


def _imported_list(list_id: int, name: str) -> StoredList:
    now = datetime.now(UTC)
    return StoredList(id=list_id, name=name, created_at=now, updated_at=now)


def _imported_task(task_id: int, list_id: int, title: str = "Task") -> StoredTask:
    now = datetime.now(UTC)
    return StoredTask(
        id=task_id,
        list_id=list_id,
        title=title,
        description=None,
        completed=False,
        due_date=None,
        reschedule_period=None,
        reschedule_base=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
        prohibited_months=[],
        constraints=[],
    )


async def test_async_import_into_empty_store(hass: HomeAssistant) -> None:
    """Importing into an empty store preserves original list/task IDs."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    task_id = 5

    list_count, task_count = await store.async_import(
        [_imported_list(1, "Cleaning")], [_imported_task(task_id, 1, "Mop floors")]
    )

    assert (list_count, task_count) == (1, 1)
    assert [lst.id for lst in store.list_lists()] == [1]
    [task] = store.list_tasks(1)
    assert task.id == task_id


async def test_async_import_merges_alongside_existing_data(
    hass: HomeAssistant,
) -> None:
    """A non-conflicting list ID imports alongside pre-existing lists."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    existing = store.create_list("Existing")  # takes list ID 1

    await store.async_import(
        [_imported_list(99, "Cleaning")], [_imported_task(1, 99, "Mop floors")]
    )

    assert sorted(lst.id for lst in store.list_lists()) == [existing.id, 99]
    [task] = store.list_tasks(99)
    assert task.id == 1  # no collision with the existing store's own task IDs


async def test_async_import_remaps_colliding_task_id(hass: HomeAssistant) -> None:
    """An imported task ID that collides with an unrelated task gets a new ID."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    existing_list = store.create_list("Existing")
    existing_task = store.create_task(existing_list.id, "Existing task", None)
    assert existing_task.id == 1

    await store.async_import(
        [_imported_list(99, "Cleaning")],
        [_imported_task(1, 99, "Mop floors")],  # id=1 collides with existing_task
    )

    [imported_task] = store.list_tasks(99)
    assert imported_task.id != existing_task.id
    assert imported_task.title == "Mop floors"


async def test_async_import_refuses_conflicting_list_id(hass: HomeAssistant) -> None:
    """A list ID that already exists is refused without `overwrite_list_ids`."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    existing = store.create_list("Existing")  # takes list ID 1

    with pytest.raises(ImportConflictError) as exc_info:
        await store.async_import([_imported_list(1, "Cleaning")], [])

    assert exc_info.value.conflicting_lists == [existing]
    assert [lst.name for lst in store.list_lists()] == ["Existing"]


async def test_async_import_overwrites_confirmed_conflict(
    hass: HomeAssistant,
) -> None:
    """Confirming `overwrite_list_ids` replaces the list and its old tasks."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    existing_list = store.create_list("Existing")  # takes list ID 1
    store.create_task(existing_list.id, "Old task", None)

    await store.async_import(
        [_imported_list(1, "Cleaning")],
        [_imported_task(1, 1, "New task")],
        overwrite_list_ids={1},
    )

    [lst] = store.list_lists()
    assert lst.name == "Cleaning"
    [task] = store.list_tasks(1)
    assert task.title == "New task"


async def test_async_import_remaps_conflicting_list_id_when_requested(
    hass: HomeAssistant,
) -> None:
    """A conflicting list ID gets a fresh ID instead of being refused."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    existing = store.create_list("Existing")  # takes list ID 1

    list_count, task_count = await store.async_import(
        [_imported_list(1, "Cleaning")],
        [_imported_task(5, 1, "Mop floors")],
        remap_conflicting_list_ids=True,
    )

    assert (list_count, task_count) == (1, 1)
    lists_by_name = {lst.name: lst for lst in store.list_lists()}
    assert lists_by_name["Existing"].id == existing.id
    imported = lists_by_name["Cleaning"]
    assert imported.id != existing.id
    [task] = store.list_tasks(imported.id)
    assert task.title == "Mop floors"


async def test_async_import_remap_does_not_affect_non_conflicting_lists(
    hass: HomeAssistant,
) -> None:
    """A non-conflicting list ID is preserved even when remap is enabled."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    imported_list_id = 99

    await store.async_import(
        [_imported_list(imported_list_id, "Cleaning")],
        [_imported_task(1, imported_list_id, "Mop floors")],
        remap_conflicting_list_ids=True,
    )

    [lst] = store.list_lists()
    assert lst.id == imported_list_id


async def test_async_import_overwrite_takes_priority_over_remap(
    hass: HomeAssistant,
) -> None:
    """A list ID in both overwrite_list_ids and remap-eligible is overwritten."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    existing_list = store.create_list("Existing")  # takes list ID 1
    store.create_task(existing_list.id, "Old task", None)

    await store.async_import(
        [_imported_list(1, "Cleaning")],
        [_imported_task(1, 1, "New task")],
        overwrite_list_ids={1},
        remap_conflicting_list_ids=True,
    )

    [lst] = store.list_lists()
    assert lst.id == 1
    assert lst.name == "Cleaning"
    [task] = store.list_tasks(1)
    assert task.title == "New task"


async def test_migrated_from_addon_defaults_false(hass: HomeAssistant) -> None:
    """A fresh store has not been marked as migrated from the add-on."""
    store = HomeUpkeepStore(hass)
    await store.async_load()

    assert store.migrated_from_addon is False


async def test_async_mark_migrated_from_addon_sets_flag_and_notifies(
    hass: HomeAssistant,
) -> None:
    """Marking the flag persists it and dispatches a change event."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    events: list[dict] = []
    async_dispatcher_connect(hass, SIGNAL_UPKEEP_CHANGED, events.append)

    await store.async_mark_migrated_from_addon()

    assert store.migrated_from_addon is True
    assert events == [{"type": "migrated_from_addon", "migrated_from_addon": True}]


async def test_migrated_from_addon_flag_survives_reload(hass: HomeAssistant) -> None:
    """The flag is persisted, not just in-memory."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    await store.async_mark_migrated_from_addon()

    reloaded = HomeUpkeepStore(hass)
    await reloaded.async_load()

    assert reloaded.migrated_from_addon is True
