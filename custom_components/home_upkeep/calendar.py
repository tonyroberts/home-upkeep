"""
`calendar` entities for the Home Upkeep integration.

Two kinds of read-only entity, both derived live from the store:

- One per list (`HomeUpkeepListCalendarEntity`): every task with a due date is
  an all-day event on that date, past, present or future, completed or not.
- A single aggregate (`HomeUpkeepOverdueCalendarEntity`): every *incomplete*
  task whose due date has passed is an all-day event on *today*. Calendar
  cards that only look forward from today (e.g. Bubble Card) can show this
  entity so overdue tasks stay visible until they are checked off.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import SIGNAL_UPKEEP_CHANGED
from .store import async_get_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import HomeUpkeepConfigEntry
    from .models import StoredTask
    from .store import HomeUpkeepStore

_TASK_EVENTS = ("task_created", "task_updated", "task_deleted")


def _all_day_event(task: StoredTask, day: date, summary: str) -> CalendarEvent:
    return CalendarEvent(
        start=day,
        end=day + timedelta(days=1),
        summary=summary,
        description=task.description,
        uid=str(task.id),
    )


def _overlaps(event: CalendarEvent, start: datetime, end: datetime) -> bool:
    """Whether an all-day event overlaps the half-open window [start, end)."""
    event_start = dt_util.start_of_local_day(event.start)
    event_end = dt_util.start_of_local_day(event.end)
    return event_start < end and event_end > start


class _UpkeepCalendarEntity(CalendarEntity, ABC):
    """Shared plumbing: live from the store, refreshed on store changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, store: HomeUpkeepStore) -> None:
        """Initialize with the store events are derived from."""
        self._store = store

    @abstractmethod
    def _all_events(self, today: date) -> list[CalendarEvent]:
        """Every event this entity knows about, ordered by start date."""

    @abstractmethod
    def _affected_by(self, event: dict[str, Any]) -> bool:
        """Whether a store change event should refresh this entity's state."""

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
        if self._affected_by(event):
            self.async_write_ha_state()

    @property
    def event(self) -> CalendarEvent | None:
        """The current or next upcoming event."""
        today = dt_util.now().date()
        return next(iter(self._all_events(today)), None)

    async def async_get_events(
        self,
        hass: HomeAssistant,  # noqa: ARG002
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events overlapping the requested window."""
        today = dt_util.now().date()
        return [
            e for e in self._all_events(today) if _overlaps(e, start_date, end_date)
        ]


class HomeUpkeepListCalendarEntity(_UpkeepCalendarEntity):
    """A Home Upkeep list, exposed as a `calendar` entity."""

    def __init__(self, store: HomeUpkeepStore, list_id: int, name: str) -> None:
        """Initialize the entity for the given list."""
        super().__init__(store)
        self._list_id = list_id
        self._attr_unique_id = f"home_upkeep_calendar_{list_id}"
        self._attr_name = name

    @property
    def list_id(self) -> int:
        """The Home Upkeep list ID this entity mirrors."""
        return self._list_id

    def _all_events(self, today: date) -> list[CalendarEvent]:  # noqa: ARG002
        """All dated tasks; `event` wants upcoming ones, so keep them sorted."""
        dated = sorted(
            (
                (t.due_date, t)
                for t in self._store.list_tasks(self._list_id)
                if t.due_date is not None
            ),
            key=lambda pair: pair[0],
        )
        return [_all_day_event(t, due, t.title) for due, t in dated]

    @property
    def event(self) -> CalendarEvent | None:
        """The first event today or later (HA's 'current or next' event)."""
        today = dt_util.now().date()
        return next((e for e in self._all_events(today) if e.start >= today), None)

    def _affected_by(self, event: dict[str, Any]) -> bool:
        event_type = event["type"]
        if event_type == "data_imported":
            return True
        return event_type in _TASK_EVENTS and event.get("list_id") == self._list_id

    @callback
    def async_update_list_name(self, name: str) -> None:
        """Update this entity's name after the underlying list is renamed."""
        self._attr_name = name
        self.async_write_ha_state()


class HomeUpkeepOverdueCalendarEntity(_UpkeepCalendarEntity):
    """Incomplete overdue tasks from every list, pinned to today."""

    _attr_unique_id = "home_upkeep_calendar_overdue"
    _attr_name = "Home Upkeep Overdue"

    def _all_events(self, today: date) -> list[CalendarEvent]:
        overdue: list[tuple[date, StoredTask]] = []
        for lst in self._store.list_lists():
            for task in self._store.list_tasks(lst.id):
                due = task.due_date
                if due is not None and due < today and not task.completed:
                    overdue.append((due, task))
        overdue.sort(key=lambda pair: pair[0])
        return [
            _all_day_event(t, today, _overdue_summary(t, due, today))
            for due, t in overdue
        ]

    def _affected_by(self, event: dict[str, Any]) -> bool:
        return event["type"] in (*_TASK_EVENTS, "list_deleted", "data_imported")

    async def async_added_to_hass(self) -> None:
        """Also refresh at midnight, when 'today' moves without a store event."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=0
            )
        )

    @callback
    def _handle_midnight(self, _now: datetime) -> None:
        self.async_write_ha_state()


def _overdue_summary(task: StoredTask, due: date, today: date) -> str:
    days = (today - due).days
    return f"{task.title} ({days} {'day' if days == 1 else 'days'} overdue)"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeUpkeepConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one calendar per list plus the aggregate overdue calendar."""
    store = async_get_store(hass)
    entities: dict[int, HomeUpkeepListCalendarEntity] = {}

    @callback
    def _sync_lists() -> None:
        current = {lst.id: lst.name for lst in store.list_lists()}

        new_entities = [
            HomeUpkeepListCalendarEntity(store, list_id, name)
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
            elif entities[list_id].name != current[list_id]:
                # An import can overwrite a list in place under the same ID.
                entities[list_id].async_update_list_name(current[list_id])

    @callback
    def _handle_event(event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type in ("list_created", "list_deleted", "data_imported"):
            _sync_lists()
        elif event_type == "list_updated":
            entity = entities.get(event["list"].id)
            if entity is not None:
                entity.async_update_list_name(event["list"].name)

    async_add_entities([HomeUpkeepOverdueCalendarEntity(store)])
    _sync_lists()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_UPKEEP_CHANGED, _handle_event)
    )
