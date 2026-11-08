"""The Home Upkeep integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.frontend import async_remove_panel
from homeassistant.config_entries import ConfigEntry

from . import migration, panel, websocket_api
from .const import PANEL_URL_PATH, PLATFORMS
from .store import HomeUpkeepStore
from .sync import CalDavSyncManager

# ruff (TC002) wants type-only imports under TYPE_CHECKING to avoid an
# unnecessary runtime import, since `from __future__ import annotations`
# means annotations are never evaluated at runtime anyway.
if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

type HomeUpkeepConfigEntry = ConfigEntry[HomeUpkeepStore]


async def async_setup_entry(hass: HomeAssistant, entry: HomeUpkeepConfigEntry) -> bool:
    """Set up Home Upkeep from a config entry."""
    store = HomeUpkeepStore(hass)
    await store.async_load()
    entry.runtime_data = store

    await migration.async_migrate_legacy_addon(hass, entry, store)

    websocket_api.async_register(hass)
    migration.async_register_services(hass)
    await panel.async_register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    sync_manager = CalDavSyncManager(hass, entry, store)
    await sync_manager.async_start()
    entry.async_on_unload(sync_manager.async_stop)

    async def _async_options_updated(
        _hass: HomeAssistant, _entry: HomeUpkeepConfigEntry
    ) -> None:
        sync_manager.async_configure()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeUpkeepConfigEntry) -> bool:
    """Unload a Home Upkeep config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    migration.async_unregister_services(hass)
    async_remove_panel(hass, PANEL_URL_PATH)
    return unloaded
