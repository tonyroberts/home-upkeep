"""
Storage layer for the Home Upkeep integration, backed by HA's Store helper.

Ported from the add-on backend's `Store` ABC / `FileStore` / `MemoryStore`
(`backend/app/storage/`). CRUD surface is unchanged; persistence moves from
one-JSON-file-per-list to a single HA `Store` document, and every mutation
notifies listeners (websocket subscribers, later `todo` entities) via the
dispatcher instead of a custom WebSocket `ConnectionManager`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_UPKEEP_CHANGED, STORAGE_KEY, STORAGE_VERSION
from .logic import calculate_next_due_date
from .models import StoredList, StoredTask

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 10


class _Unset:
    """
    Sentinel default for `update_task`'s nullable keyword arguments.

    Distinguishes "caller omitted this argument, leave the field alone"
    from "caller explicitly passed `None`, clear the field" — both of
    which would otherwise look identical if the default were `None`.
    """


_UNSET = _Unset()


class ImportConflictError(Exception):
    """
    Raised when an import's list IDs collide with existing lists.

    Carries the conflicting `StoredList`s so the caller can ask the user
    whether to overwrite them, then retry with their IDs in
    `async_import`'s `overwrite_list_ids`.
    """

    def __init__(self, conflicting_lists: list[StoredList]) -> None:
        """Store the conflicting lists for the caller to inspect."""
        self.conflicting_lists = conflicting_lists
        names = ", ".join(lst.name for lst in conflicting_lists)
        super().__init__(f"Would overwrite existing list(s): {names}")


def async_get_store(hass: HomeAssistant) -> HomeUpkeepStore:
    """Get the single Home Upkeep store instance (single-instance integration)."""
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0].runtime_data


class HomeUpkeepStore:
    """In-memory task/list store, persisted via HA's Store helper."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._tasks: dict[int, StoredTask] = {}
        self._lists: dict[int, StoredList] = {}
        self._next_task_id = 1
        self._next_list_id = 1
        self._migrated_from_addon = False

    async def async_load(self) -> None:
        """
        Load tasks and lists from storage.

        A malformed entry is logged and skipped rather than aborting the
        whole load (and thus integration setup) — matching the old
        `FileStore`, which skipped only the bad file.
        """
        data = await self._store.async_load()
        if data is None:
            return
        self._migrated_from_addon = bool(data.get("migrated_from_addon", False))
        self._lists = {}
        for item in data.get("lists", []):
            try:
                lst = StoredList.from_storage(item)
            except (KeyError, TypeError, ValueError):
                _LOGGER.exception("Skipping malformed stored list: %s", item)
                continue
            self._lists[lst.id] = lst
        self._tasks = {}
        for item in data.get("tasks", []):
            try:
                task = StoredTask.from_storage(item)
            except (KeyError, TypeError, ValueError):
                _LOGGER.exception("Skipping malformed stored task: %s", item)
                continue
            self._tasks[task.id] = task
        if self._lists:
            self._next_list_id = max(self._lists) + 1
        if self._tasks:
            self._next_task_id = max(self._tasks) + 1

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "lists": [lst.to_storage() for lst in self._lists.values()],
            "tasks": [task.to_storage() for task in self._tasks.values()],
            "migrated_from_addon": self._migrated_from_addon,
        }

    @callback
    def _async_notify(self, event: dict[str, Any]) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
        async_dispatcher_send(self._hass, SIGNAL_UPKEEP_CHANGED, event)

    async def async_import(
        self,
        lists: list[StoredList],
        tasks: list[StoredTask],
        *,
        overwrite_list_ids: set[int] | None = None,
        remap_conflicting_list_ids: bool = False,
    ) -> tuple[int, int]:
        """
        Merge previously-exported lists/tasks into the store, preserving IDs.

        A list whose ID already exists is a conflict. Unless its ID is in
        `overwrite_list_ids`, one of two things happens:

        - `remap_conflicting_list_ids=False` (default, used by the manual
          import paths, where a user is available to decide): the whole
          import is refused via `ImportConflictError` (carrying the
          existing lists that would be overwritten) so the caller can ask
          the user to confirm and retry.
        - `remap_conflicting_list_ids=True` (used by the unattended
          automatic add-on migration, where there is no user to ask):
          the incoming list is assigned a fresh, unused ID instead, along
          with its tasks. List IDs are independently sequential in both
          the add-on and the panel, so a collision on first migration
          (e.g. both starting at ID 1) is the common case, not a rare one.

        Confirmed overwrites have their existing tasks replaced entirely.
        Task IDs are remapped on collision with an unrelated task, since
        preserving the original ID only matters when it doesn't clash with
        anything.
        """
        overwrite_list_ids = overwrite_list_ids or set()
        conflicting_lists = [
            lst
            for lst in lists
            if lst.id in self._lists and lst.id not in overwrite_list_ids
        ]
        if conflicting_lists and not remap_conflicting_list_ids:
            raise ImportConflictError(
                [self._lists[lst.id] for lst in conflicting_lists]
            )

        remapped_list_ids: dict[int, int] = {}
        for lst in conflicting_lists:
            new_id = self._next_list_id
            self._next_list_id += 1
            remapped_list_ids[lst.id] = new_id
            lst.id = new_id

        tasks_by_list: dict[int, list[StoredTask]] = defaultdict(list)
        for task in tasks:
            if task.list_id in remapped_list_ids:
                task.list_id = remapped_list_ids[task.list_id]
            tasks_by_list[task.list_id].append(task)

        for lst in lists:
            if lst.id in self._lists:
                self._tasks = {
                    tid: t for tid, t in self._tasks.items() if t.list_id != lst.id
                }
            self._lists[lst.id] = lst
            for task in tasks_by_list.get(lst.id, []):
                task_id = task.id
                if task_id in self._tasks:
                    task_id = self._next_task_id
                    self._next_task_id += 1
                    task.id = task_id
                self._tasks[task_id] = task

        if self._lists:
            self._next_list_id = max(self._next_list_id, max(self._lists) + 1)
        if self._tasks:
            self._next_task_id = max(self._next_task_id, max(self._tasks) + 1)

        await self._store.async_save(self._data_to_save())
        async_dispatcher_send(
            self._hass,
            SIGNAL_UPKEEP_CHANGED,
            {
                "type": "data_imported",
                "list_count": len(lists),
                "task_count": len(tasks),
            },
        )
        return len(lists), len(tasks)

    @property
    def migrated_from_addon(self) -> bool:
        """Whether an automatic add-on migration has already completed."""
        return self._migrated_from_addon

    async def async_mark_migrated_from_addon(self) -> None:
        """
        Record that an automatic add-on migration attempt has completed.

        Set once the one-time upgrade migration in `migration.py`'s
        `async_migrate_addon_docs` finishes — this flag only drives the
        panel's "uninstall the add-on" banner, it is never read by import
        logic itself.
        """
        self._migrated_from_addon = True
        await self._store.async_save(self._data_to_save())
        async_dispatcher_send(
            self._hass,
            SIGNAL_UPKEEP_CHANGED,
            {"type": "migrated_from_addon", "migrated_from_addon": True},
        )

    # -------- Tasks --------

    def create_followup_task(
        self, task: StoredTask, updated_at: datetime | None = None
    ) -> StoredTask:
        """
        Create the rescheduled follow-up task for a just-completed task.

        The client-supplied `updated_at` (local timezone) is preferred over
        server time so due dates land on the correct local day, matching the
        add-on backend's `update_task` behavior.
        """
        if task.reschedule_base == "due":
            base_date = task.due_date
        elif updated_at is not None:
            base_date = updated_at.date()
        elif task.completed_at is not None:
            # completed_at is stored in UTC; convert to local time before
            # taking the date, or this reintroduces the raw-UTC day-early
            # bug fixed in commit 8439bf6.
            base_date = dt_util.as_local(task.completed_at).date()
        else:
            base_date = None
        base_date = base_date or dt_util.now().date()

        next_due = calculate_next_due_date(
            base_date, task.reschedule_period, task.prohibited_months
        )
        return self.create_task(
            task.list_id,
            task.title,
            task.description,
            due_date=next_due,
            reschedule_period=task.reschedule_period,
            reschedule_base=task.reschedule_base,
            prohibited_months=task.prohibited_months,
            constraints=task.constraints,
        )

    def list_tasks(self, list_id: int) -> list[StoredTask]:
        """Get all tasks for a specific list."""
        return [t for t in self._tasks.values() if t.list_id == list_id]

    def get_task(self, task_id: int) -> StoredTask | None:
        """Get a task by its ID."""
        return self._tasks.get(task_id)

    def create_task(  # noqa: PLR0913
        self,
        list_id: int,
        title: str,
        description: str | None,
        *,
        completed: bool = False,
        due_date: date | None = None,
        reschedule_period: str | None = None,
        reschedule_base: str | None = "completed",
        prohibited_months: list[int] | None = None,
        constraints: list[str] | None = None,
    ) -> StoredTask:
        """Create a new task."""
        now = datetime.now(UTC)
        task_id = self._next_task_id
        self._next_task_id += 1
        task = StoredTask(
            id=task_id,
            list_id=list_id,
            title=title,
            description=description,
            completed=completed,
            due_date=due_date,
            reschedule_period=reschedule_period,
            reschedule_base=reschedule_base,
            completed_at=None,
            created_at=now,
            updated_at=now,
            prohibited_months=prohibited_months or [],
            constraints=constraints or [],
        )
        self._tasks[task_id] = task
        self._async_notify(
            {"type": "task_created", "list_id": list_id, "task": task}
        )
        return task

    def update_task(  # noqa: PLR0912, PLR0913
        self,
        task_id: int,
        *,
        list_id: int | None = None,
        title: str | None = None,
        description: str | _Unset | None = _UNSET,
        completed: bool | None = None,
        due_date: date | _Unset | None = _UNSET,
        reschedule_period: str | _Unset | None = _UNSET,
        reschedule_base: str | _Unset | None = _UNSET,
        completed_at: datetime | _Unset | None = _UNSET,
        prohibited_months: list[int] | None = None,
        constraints: list[str] | None = None,
    ) -> StoredTask | None:
        """
        Update an existing task.

        `description`, `due_date`, `reschedule_period`, `reschedule_base`,
        and `completed_at` default to a sentinel (not `None`) so that
        passing an explicit `None` clears the field, while omitting the
        argument leaves it untouched — matching the WS API's
        nullable-field contract (see `websocket_api.py`'s
        `vol.Any(None, ...)` schemas).
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        now = datetime.now(UTC)
        if list_id is not None:
            task.list_id = list_id
        if title is not None:
            task.title = title
        if not isinstance(description, _Unset):
            task.description = description
        if completed is not None:
            # Only stamp/clear completed_at on an actual transition, not
            # on every save of an already-completed task (native `todo`
            # UI edits and resaves resend the current `completed` value
            # unconditionally) — otherwise completed_at drifts forward
            # on unrelated edits.
            if completed and not task.completed:
                task.completed_at = now
            elif not completed:
                task.completed_at = None
            task.completed = completed
        if not isinstance(due_date, _Unset):
            task.due_date = due_date
        if not isinstance(reschedule_period, _Unset):
            task.reschedule_period = reschedule_period
        if not isinstance(reschedule_base, _Unset):
            task.reschedule_base = reschedule_base
        if not isinstance(completed_at, _Unset):
            task.completed_at = completed_at
        if prohibited_months is not None:
            task.prohibited_months = prohibited_months
        if constraints is not None:
            task.constraints = constraints
        task.updated_at = now
        self._async_notify(
            {"type": "task_updated", "list_id": task.list_id, "task": task}
        )
        return task

    def delete_task(self, task_id: int) -> bool:
        """Delete a task by its ID."""
        task = self._tasks.pop(task_id, None)
        if task is None:
            return False
        self._async_notify(
            {"type": "task_deleted", "list_id": task.list_id, "task_id": task_id}
        )
        return True

    # -------- Lists --------

    def list_lists(self) -> list[StoredList]:
        """Get all task lists."""
        return list(self._lists.values())

    def create_list(self, name: str) -> StoredList:
        """Create a new task list."""
        now = datetime.now(UTC)
        list_id = self._next_list_id
        self._next_list_id += 1
        lst = StoredList(id=list_id, name=name, created_at=now, updated_at=now)
        self._lists[list_id] = lst
        self._async_notify({"type": "list_created", "list": lst})
        return lst

    def get_list(self, list_id: int) -> StoredList | None:
        """Get a list by its ID."""
        return self._lists.get(list_id)

    def rename_list(self, list_id: int, name: str) -> StoredList | None:
        """Rename a task list."""
        lst = self._lists.get(list_id)
        if lst is None:
            return None
        lst.name = name
        lst.updated_at = datetime.now(UTC)
        self._async_notify({"type": "list_updated", "list": lst})
        return lst

    def delete_list(self, list_id: int) -> bool:
        """Delete a task list and all its tasks."""
        if list_id not in self._lists:
            return False
        self._tasks = {
            tid: t for tid, t in self._tasks.items() if t.list_id != list_id
        }
        del self._lists[list_id]
        self._async_notify({"type": "list_deleted", "list_id": list_id})
        return True
