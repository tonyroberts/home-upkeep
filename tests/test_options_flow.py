"""Tests for the optional CalDAV options flow and for persisted sync links."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_upkeep import config_flow
from custom_components.home_upkeep.const import CONF_LINKS, CONF_SERVER, DOMAIN
from custom_components.home_upkeep.models import SyncLink
from custom_components.home_upkeep.remote import (
    CannotConnectError,
    Collection,
    InvalidAuthError,
)
from custom_components.home_upkeep.store import async_get_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SERVER_INPUT = {
    "url": "http://radicale.test",
    "username": "hugo",
    "password": "secret",
    "verify_ssl": False,
}
SERVER_OPTIONS = dict(SERVER_INPUT)
TASKS = Collection("Tasks", "http://radicale.test/hugo/tasks/")
CHORES = Collection("Chores", "http://radicale.test/hugo/chores/")


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Provide a fake server behind the flow, recording what it is asked."""
    state: dict[str, Any] = {
        "collections": [TASKS, CHORES],
        "error": None,
        "created": [],
    }

    async def discover(hass: HomeAssistant, settings: Any) -> list[Collection]:
        if state["error"] is not None:
            raise state["error"]
        return state["collections"]

    async def create(hass: HomeAssistant, settings: Any, name: str) -> Collection:
        state["created"].append(name)
        return Collection(name, f"http://radicale.test/hugo/{name.lower()}/")

    monkeypatch.setattr(config_flow, "async_discover_collections", discover)
    monkeypatch.setattr(config_flow, "async_create_collection", create)
    return state


async def _menu(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> Any:
    """Open the options flow and pick a menu entry."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_setup_needs_no_caldav(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Syncing is optional: the integration is fully set up with no options."""
    assert setup_integration.options == {}
    await hass.config_entries.async_unload(setup_integration.entry_id)


async def test_menu_without_a_server_only_offers_to_connect(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Until a server is configured, the only choice is to connect one."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["server"]
    await hass.config_entries.async_unload(setup_integration.entry_id)


@pytest.mark.parametrize(
    ("error", "expected"),
    [(InvalidAuthError(), "invalid_auth"), (CannotConnectError(), "cannot_connect")],
)
async def test_server_step_reports_connection_problems(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    server: dict[str, Any],
    error: Exception,
    expected: str,
) -> None:
    """A server that rejects us or can't be reached keeps the form open."""
    server["error"] = error
    result = await _menu(hass, setup_integration, "server")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SERVER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "server"
    assert result["errors"] == {"base": expected}
    await hass.config_entries.async_unload(setup_integration.entry_id)


async def test_connecting_then_linking_and_creating_collections(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    server: dict[str, Any],
) -> None:
    """Connect, then per list: use a collection, create one, or leave it local."""
    entry = setup_integration
    store = async_get_store(hass)
    cleaning = store.create_list("Cleaning")
    garden = store.create_list("Garden")
    local = store.create_list("Local only")
    await hass.async_block_till_done()

    result = await _menu(hass, entry, "server")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SERVER_INPUT
    )
    assert result["step_id"] == "lists"
    assert {str(key) for key in result["data_schema"].schema} == {
        f"Cleaning (#{cleaning.id})",
        f"Garden (#{garden.id})",
        f"Local only (#{local.id})",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            f"Cleaning (#{cleaning.id})": CHORES.url,
            f"Garden (#{garden.id})": config_flow.CREATE_NEW,
            f"Local only (#{local.id})": config_flow.NOT_SYNCED,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert server["created"] == ["Garden"]  # only the list that asked for one
    assert entry.options == {
        CONF_SERVER: SERVER_OPTIONS,
        CONF_LINKS: {
            str(cleaning.id): CHORES.url,
            str(garden.id): "http://radicale.test/hugo/garden/",
        },
    }
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfiguring_lists_with_a_saved_server_and_turning_sync_off(
    setup_integration: MockConfigEntry,
    hass: HomeAssistant,
    server: dict[str, Any],
) -> None:
    """With a server saved, lists can be edited without retyping credentials."""
    entry = setup_integration
    store = async_get_store(hass)
    cleaning = store.create_list("Cleaning")
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_SERVER: SERVER_OPTIONS,
            CONF_LINKS: {str(cleaning.id): TASKS.url},
        },
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["server", "lists", "disable"]

    result = await _menu(hass, entry, "lists")
    assert result["step_id"] == "lists"
    await hass.config_entries.options.async_configure(
        result["flow_id"], {f"Cleaning (#{cleaning.id})": CHORES.url}
    )
    assert entry.options[CONF_LINKS] == {str(cleaning.id): CHORES.url}

    result = await _menu(hass, entry, "disable")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SERVER: SERVER_OPTIONS, CONF_LINKS: {}}
    await hass.config_entries.async_unload(entry.entry_id)


# ---- links in the store document ----


async def test_sync_links_are_persisted_in_the_store_document(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Links (with their snapshot) are part of what the store saves."""
    store = async_get_store(hass)
    store.set_sync_link(7, SyncLink(TASKS.url, "uid-1", {"summary": "Mop"}))

    saved = store._data_to_save()  # noqa: SLF001

    assert saved["sync_links"] == [
        {
            "task_id": 7,
            "remote": TASKS.url,
            "uid": "uid-1",
            "snapshot": {"summary": "Mop"},
        }
    ]
    store.remove_sync_link(7)
    assert store.get_sync_link(7) is None


async def test_sync_links_are_loaded_from_storage(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Saved links come back after a restart (or every copy would be re-created)."""
    hass_storage["home_upkeep"] = {
        "version": 1,
        "minor_version": 1,
        "key": "home_upkeep",
        "data": {
            "lists": [],
            "tasks": [],
            "sync_links": [
                {
                    "task_id": 3,
                    "remote": TASKS.url,
                    "uid": "uid-3",
                    "snapshot": {"summary": "Mop"},
                },
                {"task_id": "oops"},  # malformed entries are skipped, not fatal
            ],
        },
    }
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    store = async_get_store(hass)
    assert store.get_sync_link(3) == SyncLink(TASKS.url, "uid-3", {"summary": "Mop"})
    assert store.sync_links(TASKS.url).keys() == {3}
    assert store.sync_links("http://elsewhere/").keys() == set()
    await hass.config_entries.async_unload(entry.entry_id)
