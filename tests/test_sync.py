"""
Tests for the two-way sync between a Home Upkeep list and a to-do list.

CalDAV isn't available in tests, so a second Home Upkeep list ("Remote")
stands in for it: it exposes the same todo services the sync uses
(get_items / add_item / update_item / remove_item). Changes made "in
Tasks.org" are simulated by changing tasks in the Remote list directly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.home_upkeep import sync as sync_module
from custom_components.home_upkeep.const import CONF_LINKS
from custom_components.home_upkeep.logic import calculate_next_due_date
from custom_components.home_upkeep.store import async_get_store

from .test_todo import _todo_entity_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

DUE = date(2026, 3, 1)


class Env:
    """The synced pair of lists plus helpers to drive and inspect them."""

    def __init__(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        """Create the Source and Remote lists."""
        self.hass = hass
        self.entry = entry
        self.store = async_get_store(hass)
        self.src = self.store.create_list("Source")
        self.remote = self.store.create_list("Remote")

    async def link(self, remote_entity: str | None = None) -> None:
        """Link Source to Remote (or another entity id) and let it sync."""
        await self.hass.async_block_till_done()
        entity = remote_entity or _todo_entity_id(self.hass, self.remote.id)
        self.hass.config_entries.async_update_entry(
            self.entry, options={CONF_LINKS: {str(self.src.id): entity}}
        )
        await self.settle()

    async def settle(self) -> None:
        """Wait for entity updates and for sync passes (background tasks)."""
        for _ in range(3):
            await self.hass.async_block_till_done(wait_background_tasks=True)

    async def periodic(self) -> None:
        """Fire the periodic pass (catches changes that don't move entity state)."""
        async_fire_time_changed(
            self.hass, dt_util.utcnow() + sync_module.PERIODIC_INTERVAL * 2
        )
        await self.settle()

    def titles(self, list_id: int, *, completed: bool | None = None) -> list[str]:
        """Sorted task titles in a list, optionally only (not) completed ones."""
        return sorted(
            t.title
            for t in self.store.list_tasks(list_id)
            if completed is None or t.completed == completed
        )

    def one(self, list_id: int, title: str) -> Any:
        """Return the single task with this title in a list."""
        (task,) = (t for t in self.store.list_tasks(list_id) if t.title == title)
        return task


@pytest.fixture(autouse=True)
def _no_deletion_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Believe a deletion at once, and trust reads right after our own writes."""
    monkeypatch.setattr(sync_module, "DELETION_GRACE", timedelta(0))
    monkeypatch.setattr(sync_module, "WRITE_GUARD", timedelta(0))


@pytest.fixture
async def env(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> AsyncIterator[Env]:
    """Source + Remote lists; the sync stays off until a test calls `link()`."""
    yield Env(hass, setup_integration)
    # Unload to cancel the periodic timer (no lingering timers after the test).
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def _no_add_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't really sleep between attempts to find a freshly created item."""
    monkeypatch.setattr(sync_module, "ADD_ITEM_RETRY_DELAY", 0)


@pytest.fixture
def slow_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide a just-created item from the first two forced refreshes (like CalDAV)."""
    get_items = sync_module.CalDavSyncManager._get_items  # noqa: SLF001
    add_item = sync_module.CalDavSyncManager._add_item  # noqa: SLF001
    state: dict[str, Any] = {"hide": 0, "known": set()}

    async def slow_add(self: Any, entity_id: str, fields: dict[str, Any]) -> Any:
        state["known"] = {i["uid"] for i in await get_items(self, entity_id) or []}
        state["hide"] = 2
        return await add_item(self, entity_id, fields)

    async def slow_get(self: Any, entity_id: str, *, refresh: bool = False) -> Any:
        items = await get_items(self, entity_id, refresh=refresh)
        if items is not None and refresh and state["hide"] > 0:
            state["hide"] -= 1
            items = [i for i in items if i["uid"] in state["known"]]
        return items

    monkeypatch.setattr(sync_module.CalDavSyncManager, "_add_item", slow_add)
    monkeypatch.setattr(sync_module.CalDavSyncManager, "_get_items", slow_get)


@pytest.fixture
def lagging(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make every read of the linked entity show the world as of the read before.

    CalDAV's entity keeps showing an item we just removed (or its old state)
    for a few seconds, even after a forced refresh. One read of lag is enough
    to reproduce that.
    """
    monkeypatch.setattr(sync_module, "WRITE_GUARD", timedelta(seconds=30))
    original = sync_module.CalDavSyncManager._get_items  # noqa: SLF001
    history: dict[str, list[Any]] = {}

    async def get_items(self: Any, entity_id: str, *, refresh: bool = False) -> Any:
        fresh = await original(self, entity_id, refresh=refresh)
        if fresh is None:
            return None
        reads = history.setdefault(entity_id, [])
        reads.append(fresh)
        return reads[-2] if len(reads) >= 2 else fresh  # noqa: PLR2004

    monkeypatch.setattr(sync_module.CalDavSyncManager, "_get_items", get_items)


@pytest.fixture
def stale_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make the linked entity behave like CalDAV: reads are stale until refreshed.

    The CalDAV to-do entity only polls every 15 minutes and refreshes in a
    background task after writes, so its item list lags behind the server
    unless a refresh is forced.
    """
    original = sync_module.CalDavSyncManager._get_items  # noqa: SLF001
    cache: dict[str, Any] = {}

    async def get_items(self: Any, entity_id: str, *, refresh: bool = False) -> Any:
        if refresh or entity_id not in cache:
            cache[entity_id] = await original(self, entity_id, refresh=refresh)
        return cache[entity_id]

    monkeypatch.setattr(sync_module.CalDavSyncManager, "_get_items", get_items)


# ---- Home Upkeep -> other list ----


async def test_new_task_is_copied_with_fields(env: Env) -> None:
    """A new task is copied to the other list with its due date and notes."""
    await env.link()

    env.store.create_task(env.src.id, "Mop floors", "Kitchen", due_date=DUE)
    await env.settle()

    copy = env.one(env.remote.id, "Mop floors")
    assert (copy.description, copy.due_date, copy.completed) == ("Kitchen", DUE, False)
    assert len(env.store.list_tasks(env.src.id)) == 1  # not echoed back


async def test_complete_and_reopen_follow_to_the_copy(env: Env) -> None:
    """Completing and re-opening a task follows to its copy."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    env.store.update_task(task.id, completed=True)
    await env.settle()
    assert env.titles(env.remote.id, completed=True) == ["Mop floors"]

    env.store.update_task(task.id, completed=False)
    await env.settle()
    assert env.titles(env.remote.id, completed=False) == ["Mop floors"]
    assert env.store.get_task(task.id).completed is False  # not re-completed


async def test_deleted_task_is_not_resurrected_from_a_stale_list(
    env: Env, stale_cache: None
) -> None:
    """A removed copy still shows in a stale read; it must not be re-imported."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    assert env.titles(env.remote.id) == ["Mop floors"]

    env.store.delete_task(task.id)
    await env.settle()
    await env.periodic()

    assert env.store.list_tasks(env.src.id) == []
    assert env.store.list_tasks(env.remote.id) == []


async def test_new_copy_is_found_when_it_appears_late(
    env: Env, slow_visibility: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A copy that shows up a few refreshes late is still linked by uid."""
    await env.link()

    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    assert "could not find it" not in caplog.text
    assert env.store.get_sync_link(task.id) is not None
    assert env.titles(env.remote.id) == ["Mop floors"]


async def test_new_copy_is_found_even_when_reads_are_stale(
    env: Env, stale_cache: None
) -> None:
    """The uid of a just-created copy is found, so it is linked (not re-adopted)."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)  # twin
    await env.settle()

    # Title + due date are identical, so only real links keep these apart.
    assert env.titles(env.remote.id) == ["Mop floors", "Mop floors"]
    assert env.titles(env.src.id) == ["Mop floors", "Mop floors"]


async def test_lagging_view_does_not_resurrect_a_deleted_task(
    env: Env, lagging: None
) -> None:
    """Right after a removal the entity still lists the item: don't re-import it."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.store.delete_task(task.id)
    await env.settle()
    await env.periodic()

    assert env.store.list_tasks(env.src.id) == []


async def test_lagging_view_does_not_revert_a_completion(
    env: Env, lagging: None
) -> None:
    """Right after we complete the copy it still reads open: don't reopen the task."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.store.update_task(task.id, completed=True)
    await env.settle()

    assert env.store.get_task(task.id).completed is True
    assert env.titles(env.remote.id, completed=True) == ["Mop floors"]


async def test_change_deferred_by_the_write_guard_is_picked_up_afterwards(
    env: Env, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """A change made right after our own write is applied once the guard expires."""
    monkeypatch.setattr(sync_module, "WRITE_GUARD", timedelta(seconds=30))
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    # "Tasks.org" completes the copy a moment after we created it.
    env.store.update_task(env.one(env.remote.id, "Mop floors").id, completed=True)
    await env.settle()
    assert env.store.get_task(task.id).completed is False  # deferred, not lost

    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(env.hass)
    await env.settle()
    assert env.store.get_task(task.id).completed is True


async def test_edits_follow_to_the_copy(env: Env) -> None:
    """Title, notes and due date edits are pushed to the copy."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", "Kitchen", due_date=DUE)
    await env.settle()

    env.store.update_task(
        task.id, title="Mop all floors", description=None, due_date=date(2026, 4, 2)
    )
    await env.settle()

    copy = env.one(env.remote.id, "Mop all floors")
    assert (copy.description, copy.due_date) == (None, date(2026, 4, 2))
    assert len(env.store.list_tasks(env.remote.id)) == 1  # renamed, not duplicated


async def test_deleting_a_task_removes_the_copy(env: Env) -> None:
    """Deleting a task removes its copy."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    assert env.titles(env.remote.id) == ["Mop floors"]

    env.store.delete_task(task.id)
    await env.settle()

    assert env.store.list_tasks(env.remote.id) == []


# ---- other list -> Home Upkeep ----


async def test_completing_the_copy_completes_the_task(env: Env) -> None:
    """Completing the copy (e.g. in Tasks.org) completes the task."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    env.store.update_task(env.one(env.remote.id, "Mop floors").id, completed=True)
    await env.settle()

    assert env.store.get_task(task.id).completed is True
    assert [t.completed for t in env.store.list_tasks(env.remote.id)] == [True]


async def test_reopening_the_copy_reopens_the_task(env: Env) -> None:
    """Re-opening the copy re-opens the task."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    env.store.update_task(task.id, completed=True)
    await env.settle()

    env.store.update_task(env.one(env.remote.id, "Mop floors").id, completed=False)
    await env.settle()

    assert env.store.get_task(task.id).completed is False
    assert env.titles(env.remote.id, completed=False) == ["Mop floors"]


async def test_edits_in_the_other_list_come_back(env: Env) -> None:
    """Edits made in the other list are applied to the task."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    env.store.update_task(
        env.one(env.remote.id, "Mop floors").id,
        title="Mop the floors",
        description="Upstairs",
        due_date=date(2026, 5, 5),
    )
    await env.periodic()

    updated = env.store.get_task(task.id)
    assert (updated.title, updated.description, updated.due_date) == (
        "Mop the floors",
        "Upstairs",
        date(2026, 5, 5),
    )
    assert len(env.store.list_tasks(env.remote.id)) == 1


async def test_new_item_in_the_other_list_is_imported(env: Env) -> None:
    """An item created in the other list becomes a Home Upkeep task."""
    await env.link()

    env.store.create_task(env.remote.id, "From Tasks.org", "hi", due_date=DUE)
    await env.settle()

    imported = env.one(env.src.id, "From Tasks.org")
    assert (imported.description, imported.due_date) == ("hi", DUE)
    assert imported.reschedule_period is None
    # ...and it is linked, not copied back as a second item.
    assert len(env.store.list_tasks(env.remote.id)) == 1
    assert len(env.store.list_tasks(env.src.id)) == 1


async def test_deleting_the_copy_deletes_the_task(env: Env) -> None:
    """Deleting the copy deletes the task."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.store.delete_task(env.one(env.remote.id, "Mop floors").id)
    await env.periodic()

    assert env.store.list_tasks(env.src.id) == []


async def test_deletion_is_only_believed_after_the_grace_period(
    env: Env, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """A vanished item must stay missing for the grace period before it counts."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    monkeypatch.setattr(sync_module, "DELETION_GRACE", timedelta(seconds=60))

    env.store.delete_task(env.one(env.remote.id, "Mop floors").id)
    await env.settle()
    assert env.titles(env.src.id) == ["Mop floors"]  # not believed yet

    # The re-check timer is a real timer: it must run on the event loop and
    # finish the job without waiting for the next periodic pass.
    freezer.tick(timedelta(seconds=65))
    async_fire_time_changed(env.hass)
    await env.settle()
    assert env.store.list_tasks(env.src.id) == []


async def test_both_sides_changed_means_home_upkeep_wins(env: Env) -> None:
    """When both sides changed the same field, Home Upkeep wins."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    # Change the same field on both sides before any pass runs.
    env.store.update_task(env.one(env.remote.id, "Mop floors").id, title="Remote title")
    env.store.update_task(task.id, title="Upkeep title")
    await env.periodic()

    assert env.store.get_task(task.id).title == "Upkeep title"
    assert env.titles(env.remote.id) == ["Upkeep title"]


# ---- recurring tasks ----


async def test_completing_a_recurring_copy_creates_followup_on_both_sides(
    env: Env,
) -> None:
    """Completing a recurring copy creates the follow-up on both sides."""
    await env.link()
    task = env.store.create_task(
        env.src.id, "Mop floors", None, due_date=DUE, reschedule_period="1m"
    )
    await env.settle()

    env.store.update_task(env.one(env.remote.id, "Mop floors").id, completed=True)
    await env.settle()

    assert env.store.get_task(task.id).completed is True
    for list_id in (env.src.id, env.remote.id):
        tasks = env.store.list_tasks(list_id)
        assert sorted(t.completed for t in tasks) == [False, True]
    open_due = {
        lid: next(t for t in env.store.list_tasks(lid) if not t.completed).due_date
        for lid in (env.src.id, env.remote.id)
    }
    assert open_due[env.src.id] == open_due[env.remote.id] != DUE


async def test_followup_with_same_title_and_due_does_not_churn(env: Env) -> None:
    """
    A follow-up may land on the same due date as the occurrence just completed.

    Matching by title + due date would confuse the two (and keep completing
    the follow-up); matching by link must not.
    """
    await env.link()
    due = calculate_next_due_date(dt_util.now().date(), "1m", [])
    task = env.store.create_task(
        env.src.id, "Mop floors", None, due_date=due, reschedule_period="1m"
    )
    await env.settle()

    env.store.update_task(task.id, completed=True)
    env.store.create_followup_task(env.store.get_task(task.id))
    await env.settle()
    await env.periodic()
    await env.periodic()

    for list_id in (env.src.id, env.remote.id):
        tasks = env.store.list_tasks(list_id)
        assert sorted(t.completed for t in tasks) == [False, True], list_id
        assert {t.due_date for t in tasks} == {due}


# ---- first link / safety ----


async def test_first_link_adopts_creates_and_imports(env: Env) -> None:
    """First link: adopt identical items, copy the rest, import the other side's."""
    # Before linking: one identical pair, one task only in Source, one open and
    # one completed item only in Remote.
    env.store.create_task(env.src.id, "Same", None, due_date=DUE)
    env.store.create_task(env.remote.id, "Same", None, due_date=DUE)
    env.store.create_task(env.src.id, "Only here", None)
    env.store.create_task(env.remote.id, "Only there", None)
    env.store.create_task(env.remote.id, "Old news", None, completed=True)

    await env.link()

    assert env.titles(env.src.id) == ["Only here", "Only there", "Same"]
    assert env.titles(env.remote.id) == ["Old news", "Only here", "Only there", "Same"]


async def test_unavailable_entity_changes_nothing(env: Env) -> None:
    """A missing/unavailable linked entity never changes or deletes anything."""
    env.store.create_task(env.src.id, "Mop floors", None)

    await env.link(remote_entity="todo.does_not_exist")

    assert env.titles(env.src.id) == ["Mop floors"]


async def test_links_survive_a_restart_of_the_sync(env: Env) -> None:
    """Linking is persisted: re-reading the options must not duplicate anything."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    await env.link()  # same options again
    await env.periodic()

    assert env.titles(env.src.id) == ["Mop floors"]
    assert env.titles(env.remote.id) == ["Mop floors"]


async def test_deleting_the_linked_list_leaves_the_other_side_alone(env: Env) -> None:
    """Deleting the Home Upkeep list does not delete the copies."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.store.delete_list(env.src.id)
    await env.periodic()

    assert env.titles(env.remote.id) == ["Mop floors"]
