"""
Tests for the two-way sync between Home Upkeep lists and a CalDAV collection.

The engine talks to a small `Remote` interface, so these tests use an
in-memory `FakeRemote` instead of a server. Changes made "in Tasks.org" are
simulated by changing the fake directly. The real CalDAV transport is tested
in test_remote.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.home_upkeep import sync as sync_module
from custom_components.home_upkeep.const import CONF_LINKS, CONF_SERVER
from custom_components.home_upkeep.logic import calculate_next_due_date
from custom_components.home_upkeep.remote import RemoteItem
from custom_components.home_upkeep.store import async_get_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

DUE = date(2026, 3, 1)
COLLECTION = "http://fake/dev/tasks/"
SERVER = {"url": "http://fake", "username": "u", "password": "p", "verify_ssl": True}


class FakeRemote:
    """An in-memory CalDAV collection."""

    def __init__(self) -> None:
        """Start empty, reachable and writable."""
        self.items: dict[str, dict[str, Any]] = {}
        self.reachable = True
        self.writable = True
        self._counter = 0

    # -- the Remote interface the engine uses --

    async def async_items(self) -> list[RemoteItem] | None:
        """Return all items, or None while unreachable."""
        if not self.reachable:
            return None
        return [RemoteItem(uid, **fields) for uid, fields in self.items.items()]

    async def async_create(self, fields: dict[str, Any]) -> str | None:
        """Create an item."""
        if not (self.reachable and self.writable):
            return None
        return self.add(**fields)

    async def async_update(self, uid: str, fields: dict[str, Any]) -> bool:
        """Update an item."""
        if not (self.reachable and self.writable) or uid not in self.items:
            return False
        self.items[uid] = dict(fields)
        return True

    async def async_delete(self, uid: str) -> bool:
        """Delete an item."""
        if not (self.reachable and self.writable):
            return False
        self.items.pop(uid, None)
        return True

    async def async_close(self) -> None:
        """Release the connection (nothing to do)."""

    # -- helpers that act like another client (Tasks.org) --

    def add(
        self,
        summary: str,
        *,
        completed: bool = False,
        due: str | None = None,
        description: str | None = None,
    ) -> str:
        """Add an item as another client would."""
        self._counter += 1
        uid = f"uid-{self._counter}"
        self.items[uid] = {
            "summary": summary,
            "completed": completed,
            "due": due,
            "description": description,
        }
        return uid

    def change(self, uid: str, **fields: Any) -> None:
        """Change an item as another client would."""
        self.items[uid].update(fields)

    def uid_of(self, summary: str) -> str:
        """Return the uid of the single item with this summary."""
        (uid,) = (u for u, f in self.items.items() if f["summary"] == summary)
        return uid

    def summaries(self, *, completed: bool | None = None) -> list[str]:
        """Sorted summaries, optionally only (not) completed ones."""
        return sorted(
            f["summary"]
            for f in self.items.values()
            if completed is None or f["completed"] == completed
        )


class Env:
    """A Source list wired to a FakeRemote, plus helpers to drive and inspect it."""

    def __init__(
        self, hass: HomeAssistant, entry: MockConfigEntry, remote: FakeRemote
    ) -> None:
        """Create the Source list."""
        self.hass = hass
        self.entry = entry
        self.remote = remote
        self.store = async_get_store(hass)
        self.src = self.store.create_list("Source")

    async def link(self) -> None:
        """Configure the server and link Source to the collection."""
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={CONF_SERVER: SERVER, CONF_LINKS: {str(self.src.id): COLLECTION}},
        )
        await self.settle()

    async def settle(self) -> None:
        """Wait for sync passes (they run as background tasks)."""
        for _ in range(3):
            await self.hass.async_block_till_done(wait_background_tasks=True)

    async def poll(self) -> None:
        """Fire the periodic poll (this is how server-side changes are noticed)."""
        async_fire_time_changed(
            self.hass, dt_util.utcnow() + sync_module.POLL_INTERVAL * 2
        )
        await self.settle()

    def titles(self, *, completed: bool | None = None) -> list[str]:
        """Sorted titles of the Source list's tasks."""
        return sorted(
            t.title
            for t in self.store.list_tasks(self.src.id)
            if completed is None or t.completed == completed
        )

    def task(self, title: str) -> Any:
        """Return the single Source task with this title."""
        (task,) = (t for t in self.store.list_tasks(self.src.id) if t.title == title)
        return task


@pytest.fixture(autouse=True)
def _no_deletion_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Believe a deletion at once, so tests need no timers."""
    monkeypatch.setattr(sync_module, "DELETION_GRACE", timedelta(0))


@pytest.fixture
async def env(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Env]:
    """Provide a Source list and a FakeRemote; sync stays off until `link()`."""
    remote = FakeRemote()
    monkeypatch.setattr(sync_module, "create_remote", lambda *_args: remote)
    yield Env(hass, setup_integration, remote)
    # Unload to cancel the poll timer (no lingering timers after the test).
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()


# ---- Home Upkeep -> server ----


async def test_new_task_is_copied_with_fields(env: Env) -> None:
    """A new task is copied to the server with its due date and notes."""
    await env.link()

    env.store.create_task(env.src.id, "Mop floors", "Kitchen", due_date=DUE)
    await env.settle()

    (fields,) = env.remote.items.values()
    assert fields == {
        "summary": "Mop floors",
        "completed": False,
        "due": "2026-03-01",
        "description": "Kitchen",
    }
    assert env.titles() == ["Mop floors"]  # not echoed back as a second task


async def test_complete_and_reopen_follow_to_the_copy(env: Env) -> None:
    """Completing and re-opening a task follows to its copy."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    env.store.update_task(task.id, completed=True)
    await env.settle()
    assert env.remote.summaries(completed=True) == ["Mop floors"]

    env.store.update_task(task.id, completed=False)
    await env.settle()
    assert env.remote.summaries(completed=False) == ["Mop floors"]
    assert env.store.get_task(task.id).completed is False  # not re-completed


async def test_edits_follow_to_the_copy(env: Env) -> None:
    """Title, notes and due date edits are pushed to the same item."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", "Kitchen", due_date=DUE)
    await env.settle()
    uid = env.remote.uid_of("Mop floors")

    env.store.update_task(
        task.id, title="Mop all floors", description=None, due_date=date(2026, 4, 2)
    )
    await env.settle()

    assert list(env.remote.items) == [uid]  # renamed in place, not re-created
    assert env.remote.items[uid]["summary"] == "Mop all floors"
    assert env.remote.items[uid]["description"] is None
    assert env.remote.items[uid]["due"] == "2026-04-02"


async def test_deleting_a_task_removes_the_copy(env: Env) -> None:
    """Deleting a task removes its copy from the server."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    assert env.remote.summaries() == ["Mop floors"]

    env.store.delete_task(task.id)
    await env.settle()

    assert env.remote.items == {}


# ---- server -> Home Upkeep ----


async def test_completing_the_copy_completes_the_task(env: Env) -> None:
    """Completing the item (e.g. in Tasks.org) completes the task."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    env.remote.change(env.remote.uid_of("Mop floors"), completed=True)
    await env.poll()

    assert env.store.get_task(task.id).completed is True


async def test_reopening_the_copy_reopens_the_task(env: Env) -> None:
    """Re-opening the item re-opens the task."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    env.store.update_task(task.id, completed=True)
    await env.settle()

    env.remote.change(env.remote.uid_of("Mop floors"), completed=False)
    await env.poll()

    assert env.store.get_task(task.id).completed is False
    assert env.remote.summaries(completed=False) == ["Mop floors"]


async def test_edits_on_the_server_come_back(env: Env) -> None:
    """Edits made on the server are applied to the task."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    env.remote.change(
        env.remote.uid_of("Mop floors"),
        summary="Mop the floors",
        description="Upstairs",
        due="2026-05-05",
    )
    await env.poll()

    updated = env.store.get_task(task.id)
    assert (updated.title, updated.description, updated.due_date) == (
        "Mop the floors",
        "Upstairs",
        date(2026, 5, 5),
    )
    assert len(env.remote.items) == 1


async def test_new_item_on_the_server_is_imported(env: Env) -> None:
    """An item created in Tasks.org becomes a non-recurring Home Upkeep task."""
    await env.link()

    env.remote.add("From Tasks.org", due="2026-03-01", description="hi")
    await env.poll()

    imported = env.task("From Tasks.org")
    assert (imported.description, imported.due_date) == ("hi", DUE)
    assert imported.reschedule_period is None
    assert len(env.remote.items) == 1  # linked, not copied back as a twin
    assert env.titles() == ["From Tasks.org"]


async def test_item_deleted_on_the_server_deletes_the_task(env: Env) -> None:
    """Deleting the item deletes the task."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.remote.items.clear()
    await env.poll()

    assert env.store.list_tasks(env.src.id) == []


async def test_deletion_is_only_believed_after_the_grace_period(
    env: Env, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """A vanished item must stay missing for the grace period before it counts."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    monkeypatch.setattr(sync_module, "DELETION_GRACE", timedelta(seconds=60))

    env.remote.items.clear()
    await env.poll()
    assert env.titles() == ["Mop floors"]  # not believed yet

    # The re-check is a real timer: it must run on the event loop and finish
    # the job without waiting for the next poll.
    freezer.tick(timedelta(seconds=65))
    async_fire_time_changed(env.hass)
    await env.settle()
    assert env.store.list_tasks(env.src.id) == []


async def test_item_that_reappears_is_not_deleted(
    env: Env, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """A server that briefly returned a short list must not cost us tasks."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()
    monkeypatch.setattr(sync_module, "DELETION_GRACE", timedelta(seconds=60))
    saved = dict(env.remote.items)

    env.remote.items.clear()
    await env.poll()
    env.remote.items.update(saved)  # the glitch is over
    freezer.tick(timedelta(seconds=65))
    async_fire_time_changed(env.hass)
    await env.settle()

    # The very same task survives: it was not deleted and re-imported.
    assert [t.id for t in env.store.list_tasks(env.src.id)] == [task.id]


async def test_both_sides_changed_means_home_upkeep_wins(env: Env) -> None:
    """When both sides changed the same field, Home Upkeep wins."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.remote.change(env.remote.uid_of("Mop floors"), summary="Server title")
    env.store.update_task(task.id, title="Upkeep title")
    await env.poll()

    assert env.store.get_task(task.id).title == "Upkeep title"
    assert env.remote.summaries() == ["Upkeep title"]


async def test_changes_to_different_fields_are_both_kept(env: Env) -> None:
    """Edits to different fields on each side merge instead of clobbering."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None, due_date=DUE)
    await env.settle()

    env.remote.change(env.remote.uid_of("Mop floors"), description="from server")
    env.store.update_task(task.id, due_date=date(2026, 6, 1))
    await env.poll()

    merged = env.store.get_task(task.id)
    assert (merged.description, merged.due_date) == ("from server", date(2026, 6, 1))
    (fields,) = env.remote.items.values()
    assert (fields["description"], fields["due"]) == ("from server", "2026-06-01")


# ---- recurring tasks ----


async def test_completing_a_recurring_item_creates_followup_on_both_sides(
    env: Env,
) -> None:
    """Completing a recurring task's item creates its follow-up everywhere."""
    await env.link()
    task = env.store.create_task(
        env.src.id, "Mop floors", None, due_date=DUE, reschedule_period="1m"
    )
    await env.settle()

    env.remote.change(env.remote.uid_of("Mop floors"), completed=True)
    await env.poll()

    assert env.store.get_task(task.id).completed is True
    tasks = env.store.list_tasks(env.src.id)
    assert sorted(t.completed for t in tasks) == [False, True]
    followup = next(t for t in tasks if not t.completed)
    assert followup.due_date not in (None, DUE)
    open_items = [f for f in env.remote.items.values() if not f["completed"]]
    assert [f["due"] for f in open_items] == [followup.due_date.isoformat()]


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
    await env.poll()
    await env.poll()

    tasks = env.store.list_tasks(env.src.id)
    assert sorted(t.completed for t in tasks) == [False, True]
    assert sorted(f["completed"] for f in env.remote.items.values()) == [False, True]
    assert {t.due_date for t in tasks} == {due}


# ---- first link / safety ----


async def test_first_link_adopts_creates_and_imports(env: Env) -> None:
    """First link: adopt identical items, copy the rest, import the server's own."""
    env.store.create_task(env.src.id, "Same", None, due_date=DUE)
    env.remote.add("Same", due="2026-03-01")
    env.store.create_task(env.src.id, "Only here", None)
    env.remote.add("Only there")
    env.remote.add("Old news", completed=True)

    await env.link()

    assert env.titles() == ["Only here", "Only there", "Same"]
    assert env.remote.summaries() == ["Old news", "Only here", "Only there", "Same"]
    assert len(env.remote.items) == 4  # noqa: PLR2004 - "Same" adopted, not duplicated


async def test_unreachable_server_changes_nothing_and_recovers(env: Env) -> None:
    """While the server is down nothing is changed or deleted; afterwards it syncs."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.remote.reachable = False
    env.store.create_task(env.src.id, "While offline", None)
    await env.poll()
    assert env.titles() == ["Mop floors", "While offline"]
    assert env.remote.summaries() == ["Mop floors"]

    env.remote.reachable = True
    await env.poll()
    assert env.remote.summaries() == ["Mop floors", "While offline"]
    assert env.titles() == ["Mop floors", "While offline"]


async def test_failed_write_is_retried(env: Env) -> None:
    """A write the server rejects is not forgotten: the next pass retries it."""
    await env.link()
    task = env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.remote.writable = False
    env.store.update_task(task.id, completed=True)
    await env.settle()
    assert env.remote.summaries(completed=True) == []

    env.remote.writable = True
    await env.poll()
    assert env.remote.summaries(completed=True) == ["Mop floors"]
    assert env.store.get_task(task.id).completed is True  # and not reverted


async def test_relinking_with_the_same_options_duplicates_nothing(env: Env) -> None:
    """Re-reading the same options (e.g. after a restart) must not duplicate."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    await env.link()
    await env.poll()

    assert env.titles() == ["Mop floors"]
    assert env.remote.summaries() == ["Mop floors"]


async def test_deleting_the_linked_list_leaves_the_server_alone(env: Env) -> None:
    """Deleting the Home Upkeep list does not delete its items on the server."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.store.delete_list(env.src.id)
    await env.poll()

    assert env.remote.summaries() == ["Mop floors"]


async def test_unlinking_stops_syncing(env: Env) -> None:
    """With the link removed, changes no longer flow either way."""
    await env.link()
    env.store.create_task(env.src.id, "Mop floors", None)
    await env.settle()

    env.hass.config_entries.async_update_entry(
        env.entry, options={CONF_SERVER: SERVER, CONF_LINKS: {}}
    )
    env.store.create_task(env.src.id, "Local only", None)
    env.remote.add("Server only")
    await env.poll()

    assert env.remote.summaries() == ["Mop floors", "Server only"]
    assert env.titles() == ["Local only", "Mop floors"]


async def test_no_server_means_no_connection(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Syncing is optional: without a server nothing is ever created or polled."""
    created: list[Any] = []
    monkeypatch.setattr(
        sync_module, "create_remote", lambda *args: created.append(args)
    )
    store = async_get_store(hass)
    lst = store.create_list("Cleaning")
    store.create_task(lst.id, "Mop floors", None)

    # A stale link without a server is ignored too.
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_LINKS: {str(lst.id): COLLECTION}}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert created == []
    await hass.config_entries.async_unload(setup_integration.entry_id)
