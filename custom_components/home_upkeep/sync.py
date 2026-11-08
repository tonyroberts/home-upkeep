"""
Two-way sync between Home Upkeep lists and other to-do lists (e.g. CalDAV).

A Home Upkeep list can be linked to any `todo` entity (typically a CalDAV
list that Tasks.org syncs with). Everything goes through HA's own `todo`
services, so no CalDAV code or extra dependency is needed.

Every task/item pair is remembered by a stable link (the item's uid) plus a
snapshot of its fields as of the last successful sync. A sync pass merges
each field three ways against that snapshot:

- changed on one side only -> that side wins and is copied to the other;
- changed on both sides    -> Home Upkeep wins.

That single rule gives create / complete / reopen / edit / delete in both
directions without the loops a naive "mirror" has: after a pass both sides
equal the snapshot, so the next pass is a no-op.

Matching tasks to items by title + due date is only used once, to *adopt*
items that already exist when a list is first linked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started

from .const import CONF_LINKS, SIGNAL_UPKEEP_CHANGED
from .models import SyncLink

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, EventStateChangedData, HomeAssistant

    from . import HomeUpkeepConfigEntry
    from .models import StoredTask
    from .store import HomeUpkeepStore

_LOGGER = logging.getLogger(__name__)

# How often every link is re-checked, to catch changes that don't move the
# linked entity's state (e.g. one item completed while another is added).
PERIODIC_INTERVAL = timedelta(minutes=5)

# How long an item must stay missing from the other list before the deletion
# is believed. Guards against a flaky server briefly returning a short list.
DELETION_GRACE = timedelta(minutes=2)

# After creating an item, how often (and how far apart) to look for it. Polling
# entities such as CalDAV can take a few seconds to show a new item, even after
# a forced refresh.
ADD_ITEM_ATTEMPTS = 10
ADD_ITEM_RETRY_DELAY = 1.0  # seconds

# After we write to an item (add, update, remove) the entity may keep showing
# its old state - or the deleted item - for a few seconds, even after a forced
# refresh. For this long that item's reads are not believed: it is not
# imported, and nothing is read back from it.
WRITE_GUARD = timedelta(seconds=30)

_FIELDS = ("summary", "completed", "due", "description")
_STORE_EVENTS = {"task_created", "task_updated", "task_deleted", "data_imported"}


def _guard_seconds() -> float:
    return WRITE_GUARD.total_seconds()


def _task_fields(task: StoredTask) -> dict[str, Any]:
    return {
        "summary": task.title,
        "completed": bool(task.completed),
        "due": task.due_date.isoformat() if task.due_date else None,
        "description": task.description or None,
    }


def _item_fields(item: dict[str, Any]) -> dict[str, Any]:
    due = item.get("due")
    return {
        "summary": item.get("summary", ""),
        "completed": item.get("status") == "completed",
        # CalDAV items may carry a time; Home Upkeep only knows dates.
        "due": str(due)[:10] if due else None,
        "description": item.get("description") or None,
    }


@dataclass
class _ListLink:
    """Runtime state of one list <-> entity link."""

    list_id: int
    entity_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    scheduled: bool = False
    # task id -> time.monotonic() when its item was first seen missing
    missing: dict[int, float] = field(default_factory=dict)
    # item uid -> time.monotonic() of our last write to it (see WRITE_GUARD)
    touched: dict[str, float] = field(default_factory=dict)
    # Cancels the pending re-check timer, if one is set.
    recheck: Callable[[], None] | None = None

    def touch(self, uid: str) -> None:
        """Remember that we just wrote to this item."""
        self.touched[uid] = time.monotonic()

    def guard_remaining(self, uid: str) -> float:
        """Seconds until reads of this item are trusted again (0 if already)."""
        touched = self.touched.get(uid)
        if touched is None:
            return 0.0
        return max(0.0, _guard_seconds() - (time.monotonic() - touched))

    def recently_touched(self, uid: str) -> bool:
        """Whether reads of this item are still untrustworthy after our write."""
        now = time.monotonic()
        for key in [k for k, t in self.touched.items() if now - t >= _guard_seconds()]:
            del self.touched[key]
        return uid in self.touched


class CalDavSyncManager:
    """Keeps the configured Home Upkeep lists in sync with their linked lists."""

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
        self._link_unsubs: list[Callable[[], None]] = []
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
        """Stop listening and drop timers."""
        self._clear_link_listeners()
        for link in self._links.values():
            self._cancel_recheck(link)
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def async_configure(self) -> None:
        """(Re)read the linked lists from the config entry options."""
        wanted: dict[int, str] = {}
        for raw_list_id, entity_id in self._entry.options.get(CONF_LINKS, {}).items():
            try:
                wanted[int(raw_list_id)] = str(entity_id)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid sync link %r", raw_list_id)

        new_links: dict[int, _ListLink] = {}
        for list_id, entity_id in wanted.items():
            existing = self._links.get(list_id)
            new_links[list_id] = (
                existing
                if existing is not None and existing.entity_id == entity_id
                else _ListLink(list_id, entity_id)
            )
        changed = {
            list_id
            for list_id, link in new_links.items()
            if self._links.get(list_id) is not link
        }
        self._links = new_links

        self._clear_link_listeners()
        if self._links:
            entity_ids = [link.entity_id for link in self._links.values()]
            self._link_unsubs.append(
                async_track_state_change_event(
                    self._hass, entity_ids, self._handle_entity_state
                )
            )
            self._link_unsubs.append(
                async_track_time_interval(
                    self._hass, self._handle_periodic, PERIODIC_INTERVAL
                )
            )
        if self._started:
            for list_id in changed:
                self._schedule(self._links[list_id])

    @callback
    def _clear_link_listeners(self) -> None:
        for unsub in self._link_unsubs:
            unsub()
        self._link_unsubs.clear()

    # ---- triggers ----

    @callback
    def _handle_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self._schedule_all()

    @callback
    def _handle_periodic(self, _now: Any) -> None:
        self._schedule_all()

    @callback
    def _handle_entity_state(self, event: Event[EventStateChangedData]) -> None:
        for link in self._links.values():
            if link.entity_id == event.data["entity_id"]:
                self._schedule(link)

    @callback
    def _handle_store_event(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type not in _STORE_EVENTS:
            return
        if event_type == "data_imported":
            self._schedule_all()
            return
        link = self._links.get(event.get("list_id"))
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
        items = await self._get_items(link.entity_id, refresh=True)
        if items is None:
            return  # entity unavailable: never act on a missing/partial view

        by_uid = {item["uid"]: item for item in items if item.get("uid")}
        tasks = {t.id: t for t in store.list_tasks(link.list_id)}

        # 1. Pairs that are already linked: three-way merge, or propagate a
        #    deletion made on either side.
        for task_id, sl in store.sync_links(link.entity_id).items():
            task = tasks.get(task_id)
            item = by_uid.get(sl.uid)
            if task is None and item is None:
                store.remove_sync_link(task_id)
            elif task is None:
                # Deleted in Home Upkeep -> remove the copy.
                if await self._remove_item(link.entity_id, sl.uid):
                    link.touch(sl.uid)
                    store.remove_sync_link(task_id)
                    by_uid.pop(sl.uid, None)
            elif item is None:
                self._handle_missing_item(link, task_id)
            else:
                link.missing.pop(task_id, None)
                await self._merge(link, task, item, sl)

        # 2. Open Home Upkeep tasks with no linked item yet: adopt an
        #    identical existing item, otherwise create one.
        linked_uids = {sl.uid for sl in store.sync_links(link.entity_id).values()}
        free = [
            item
            for uid, item in by_uid.items()
            if uid not in linked_uids
            and item.get("status") != "completed"
            and not link.recently_touched(uid)
        ]
        linked_tasks = store.sync_links(link.entity_id)
        for task in tasks.values():
            if task.completed or task.id in linked_tasks:
                continue
            fields = _task_fields(task)
            match = next(
                (
                    item
                    for item in free
                    if _item_fields(item)["summary"] == fields["summary"]
                    and _item_fields(item)["due"] == fields["due"]
                ),
                None,
            )
            if match is not None:
                free.remove(match)
                store.set_sync_link(
                    task.id, SyncLink(link.entity_id, match["uid"], _item_fields(match))
                )
            elif (uid := await self._add_item(link.entity_id, fields)) is not None:
                link.touch(uid)
                store.set_sync_link(task.id, SyncLink(link.entity_id, uid, fields))

        # 3. Open items that exist only on the other side: import them. This
        #    deliberately reuses the view read (and refreshed) at the start of
        #    the pass: a fresh read could still show items we just removed,
        #    because polling entities lag behind their server, and would
        #    resurrect them. Items created above are linked, so excluded.
        linked_uids = {sl.uid for sl in store.sync_links(link.entity_id).values()}
        for item in list(by_uid.values()):
            if (
                item.get("uid") in linked_uids
                or item.get("status") == "completed"
                or link.recently_touched(item["uid"])
            ):
                continue
            fields = _item_fields(item)
            if not fields["summary"].strip():
                continue
            task = store.create_task(
                link.list_id,
                fields["summary"],
                fields["description"],
                due_date=date.fromisoformat(fields["due"]) if fields["due"] else None,
            )
            store.set_sync_link(task.id, SyncLink(link.entity_id, item["uid"], fields))

    def _handle_missing_item(self, link: _ListLink, task_id: int) -> None:
        """Delete the task once its item has stayed missing from the other list."""
        grace = DELETION_GRACE.total_seconds()
        first_seen = link.missing.setdefault(task_id, time.monotonic())
        if time.monotonic() - first_seen < grace:
            # Look again after the grace period instead of waiting for the
            # next periodic pass.
            self._schedule_recheck(link, grace + 1)
            return
        link.missing.pop(task_id, None)
        self._store.delete_task(task_id)
        self._store.remove_sync_link(task_id)

    async def _merge(
        self,
        link: _ListLink,
        task: StoredTask,
        item: dict[str, Any],
        sl: SyncLink,
    ) -> None:
        """Three-way merge one linked task/item pair against its snapshot."""
        ours = _task_fields(task)
        theirs = _item_fields(item)
        if link.recently_touched(sl.uid):
            # The entity may still show the state from before our write; read
            # nothing back from it for now (our own changes still go out).
            if theirs != sl.snapshot:
                # Something differs: look again once the guard has expired,
                # so a real change is picked up without waiting for the
                # periodic pass.
                self._schedule_recheck(link, link.guard_remaining(sl.uid) + 1)
            theirs = dict(sl.snapshot)
        merged: dict[str, Any] = {}
        for name in _FIELDS:
            if ours[name] == theirs[name] or ours[name] != sl.snapshot.get(name):
                merged[name] = ours[name]  # equal, or Home Upkeep changed
            else:
                merged[name] = theirs[name]  # only the other side changed

        if merged != ours:
            self._apply_to_task(task, ours, merged)
        if merged != theirs:
            if not await self._update_item(
                link.entity_id, sl.uid, _item_fields(item), merged
            ):
                return  # keep the old snapshot so the next pass retries
            link.touch(sl.uid)
        if merged != sl.snapshot:
            self._store.set_sync_link(task.id, SyncLink(link.entity_id, sl.uid, merged))

    def _apply_to_task(
        self, task: StoredTask, ours: dict[str, Any], merged: dict[str, Any]
    ) -> None:
        """Copy the fields that changed on the other side into the task."""
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

    # ---- talking to the other to-do list ----

    def _available(self, entity_id: str) -> bool:
        state = self._hass.states.get(entity_id)
        return state is not None and state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )

    async def _refresh(self, entity_id: str) -> None:
        """
        Make the entity re-read its list from the server.

        Polling entities (CalDAV re-reads every 15 minutes, and refreshes in a
        background task after our own writes) can show stale items, which
        would look like changes that never happened - or hide ones that did.
        """
        try:
            await self._hass.services.async_call(
                "homeassistant",
                "update_entity",
                {"entity_id": [entity_id]},
                blocking=True,
            )
        except HomeAssistantError:
            _LOGGER.debug("Could not refresh %s", entity_id, exc_info=True)

    async def _get_items(
        self, entity_id: str, *, refresh: bool = False
    ) -> list[dict[str, Any]] | None:
        if not self._available(entity_id):
            return None
        if refresh:
            await self._refresh(entity_id)
            if not self._available(entity_id):
                return None
        try:
            response = await self._hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": entity_id},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError:
            _LOGGER.warning("Could not read items of %s", entity_id, exc_info=True)
            return None
        return list((response or {}).get(entity_id, {}).get("items", []))

    async def _add_item(self, entity_id: str, fields: dict[str, Any]) -> str | None:
        """Create an item and return its uid (todo.add_item does not return it)."""
        before = {i["uid"] for i in (await self._get_items(entity_id)) or []}
        data: dict[str, Any] = {"entity_id": entity_id, "item": fields["summary"]}
        if fields["due"]:
            data["due_date"] = fields["due"]
        if fields["description"]:
            data["description"] = fields["description"]
        try:
            await self._hass.services.async_call(
                "todo", "add_item", data, blocking=True
            )
        except HomeAssistantError:
            _LOGGER.warning("Could not add item to %s", entity_id, exc_info=True)
            return None
        match = None
        for attempt in range(ADD_ITEM_ATTEMPTS):
            if attempt:
                await asyncio.sleep(ADD_ITEM_RETRY_DELAY)
            new = [
                i
                for i in (await self._get_items(entity_id, refresh=True)) or []
                if i.get("uid") and i["uid"] not in before
            ]
            match = next(
                (i for i in new if i.get("summary") == fields["summary"]), None
            )
            if match is None and len(new) == 1:
                match = new[0]
            if match is not None:
                break
        if match is None:
            # Not fatal: the next pass adopts the item by title + due date.
            _LOGGER.warning("Created an item in %s but could not find it", entity_id)
            return None
        return str(match["uid"])

    async def _update_item(
        self,
        entity_id: str,
        uid: str,
        current: dict[str, Any],
        wanted: dict[str, Any],
    ) -> bool:
        data: dict[str, Any] = {"entity_id": entity_id, "item": uid}
        if wanted["summary"] != current["summary"] and wanted["summary"].strip():
            data["rename"] = wanted["summary"]
        if wanted["completed"] != current["completed"]:
            data["status"] = "completed" if wanted["completed"] else "needs_action"
        if wanted["due"] != current["due"]:
            data["due_date"] = wanted["due"]
        if wanted["description"] != current["description"]:
            data["description"] = wanted["description"]
        if len(data) == 2:  # noqa: PLR2004 - only entity_id + item: nothing to do
            return True
        try:
            await self._hass.services.async_call(
                "todo", "update_item", data, blocking=True
            )
        except HomeAssistantError:
            _LOGGER.warning("Could not update item in %s", entity_id, exc_info=True)
            return False
        return True

    async def _remove_item(self, entity_id: str, uid: str) -> bool:
        try:
            await self._hass.services.async_call(
                "todo",
                "remove_item",
                {"entity_id": entity_id, "item": [uid]},
                blocking=True,
            )
        except HomeAssistantError:
            _LOGGER.warning("Could not remove item from %s", entity_id, exc_info=True)
            return False
        return True
