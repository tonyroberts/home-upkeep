"""
`todo` entities for the Home Upkeep integration.

One `TodoListEntity` per list, mapping task <-> `TodoItem` (summary=title,
status from `completed`, due=`due_date`, description, completed=
`completed_at`). This absorbs the separate `home-upkeep-component` repo's
role. The mapping is intentionally lossy: seasonal/constraints richness lives in
the panel, though completing a recurring task still reschedules it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_UPKEEP_CHANGED
from .store import async_get_store

# ruff (TC002) wants type-only imports under TYPE_CHECKING to avoid an
# unnecessary runtime import, since `from __future__ import annotations`
# means annotations are never evaluated at runtime anyway.
if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import HomeUpkeepConfigEntry
    from .models import StoredTask
    from .store import HomeUpkeepStore

_SUPPORTED_FEATURES = (
    TodoListEntityFeature.CREATE_TODO_ITEM
    | TodoListEntityFeature.UPDATE_TODO_ITEM
    | TodoListEntityFeature.DELETE_TODO_ITEM
    | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
)


def _task_to_todo_item(task: StoredTask) -> TodoItem:
    """Convert a StoredTask into a TodoItem (lossy: see module docstring)."""
    return TodoItem(
        uid=str(task.id),
        summary=task.title,
        status=TodoItemStatus.COMPLETED
        if task.completed
        else TodoItemStatus.NEEDS_ACTION,
        due=task.due_date,
        description=task.description,
        completed=task.completed_at,
    )


class HomeUpkeepTodoListEntity(TodoListEntity):
    """A Home Upkeep list, exposed as a `todo` entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = _SUPPORTED_FEATURES

    def __init__(self, store: HomeUpkeepStore, list_id: int, name: str) -> None:
        """Initialize the entity for the given list."""
        self._store = store
        self._list_id = list_id
        self._attr_unique_id = f"home_upkeep_list_{list_id}"
        self._attr_name = name
        self._refresh_items()

    @property
    def list_id(self) -> int:
        """The Home Upkeep list ID this entity mirrors."""
        return self._list_id

    async def async_added_to_hass(self) -> None:
        """Subscribe to store changes once added."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPKEEP_CHANGED, self._handle_event
            )
        )

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type == "data_imported":
            # An import may have replaced this list's tasks entirely; the
            # event doesn't carry per-list detail, so just refresh.
            self._refresh_items()
            self.async_write_ha_state()
            return
        if event_type not in ("task_created", "task_updated", "task_deleted"):
            return
        if event.get("list_id") != self._list_id:
            return
        self._refresh_items()
        self.async_write_ha_state()

    @callback
    def _refresh_items(self) -> None:
        self.todo_items = [
            _task_to_todo_item(task) for task in self._store.list_tasks(self._list_id)
        ]

    @callback
    def async_update_list_name(self, name: str) -> None:
        """Update this entity's name after the underlying list is renamed."""
        self._attr_name = name
        self.async_write_ha_state()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create a new task from a todo item."""
        self._store.create_task(
            self._list_id,
            item.summary or "",
            item.description,
            completed=item.status == TodoItemStatus.COMPLETED,
            due_date=item.due,
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """
        Update a task from a todo item.

        Completing a recurring task creates its rescheduled follow-up, same
        as completing it from the panel.
        """
        previous = self._store.get_task(int(item.uid))
        was_completed = previous is not None and previous.completed
        task = self._store.update_task(
            int(item.uid),
            title=item.summary,
            description=item.description,
            completed=item.status == TodoItemStatus.COMPLETED,
            due_date=item.due,
        )
        if task and not was_completed and task.completed and task.reschedule_period:
            self._store.create_followup_task(task)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete tasks by their todo item uid."""
        for uid in uids:
            self._store.delete_task(int(uid))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeUpkeepConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one HomeUpkeepTodoListEntity per list, kept in sync with the store."""
    store = async_get_store(hass)
    entities: dict[int, HomeUpkeepTodoListEntity] = {}

    @callback
    def _sync_lists() -> None:
        current = {lst.id: lst.name for lst in store.list_lists()}

        new_entities = [
            HomeUpkeepTodoListEntity(store, list_id, name)
            for list_id, name in current.items()
            if list_id not in entities
        ]
        for entity in new_entities:
            entities[entity.list_id] = entity
        if new_entities:
            async_add_entities(new_entities)

        for list_id in list(entities):
            if list_id not in current:
                hass.async_create_task(
                    entities.pop(list_id).async_remove(force_remove=True)
                )

    @callback
    def _handle_event(event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type in ("list_created", "list_deleted", "data_imported"):
            # Imports can add new lists (and overwrite existing ones), so
            # resync entities the same way as an explicit list_created.
            _sync_lists()
        elif event_type == "list_updated":
            entity = entities.get(event["list"].id)
            if entity is not None:
                entity.async_update_list_name(event["list"].name)

    _sync_lists()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_UPKEEP_CHANGED, _handle_event)
    )
