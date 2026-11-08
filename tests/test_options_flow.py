"""Tests for the options flow that links Home Upkeep lists to other to-do lists."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_upkeep.const import CONF_LINKS, DOMAIN
from custom_components.home_upkeep.models import SyncLink
from custom_components.home_upkeep.store import async_get_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_options_flow_links_and_unlinks_lists(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Each list gets an optional picker; filled ones become links."""
    entry = setup_integration
    store = async_get_store(hass)
    cleaning = store.create_list("Cleaning")
    garden = store.create_list("Garden")
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    fields = {str(key) for key in result["data_schema"].schema}
    assert fields == {f"Cleaning (#{cleaning.id})", f"Garden (#{garden.id})"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {f"Cleaning (#{cleaning.id})": "todo.caldav_list"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_LINKS: {str(cleaning.id): "todo.caldav_list"}}

    # Submitting an empty form removes the link again.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert entry.options == {CONF_LINKS: {}}

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_sync_links_are_persisted_in_the_store_document(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Links (with their snapshot) are part of what the store saves."""
    store = async_get_store(hass)
    store.set_sync_link(7, SyncLink("todo.caldav", "uid-1", {"summary": "Mop"}))

    saved = store._data_to_save()  # noqa: SLF001

    assert saved["sync_links"] == [
        {
            "task_id": 7,
            "entity_id": "todo.caldav",
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
                    "entity_id": "todo.caldav",
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
    assert store.get_sync_link(3) == SyncLink(
        "todo.caldav", "uid-3", {"summary": "Mop"}
    )
    assert store.sync_links("todo.caldav").keys() == {3}
    await hass.config_entries.async_unload(entry.entry_id)
