"""Config flow for the Home Upkeep integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import CONF_LINKS, DOMAIN
from .store import async_get_store

if TYPE_CHECKING:
    from .models import StoredList


class HomeUpkeepConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Home Upkeep."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,  # noqa: ARG004 - signature fixed by HA
    ) -> HomeUpkeepOptionsFlow:
        """Create the options flow (sync links)."""
        return HomeUpkeepOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial (and only) step: single-instance setup."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Home Upkeep", data={})

        return self.async_show_form(step_id="user")


def _field_name(lst: StoredList) -> str:
    """Form field label for a list (the id disambiguates duplicate names)."""
    return f"{lst.name} (#{lst.id})"


class HomeUpkeepOptionsFlow(OptionsFlow):
    """Choose which to-do list (e.g. a CalDAV list) each Home Upkeep list syncs with."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show one optional to-do entity picker per Home Upkeep list."""
        store = async_get_store(self.hass)
        fields = {_field_name(lst): lst for lst in store.list_lists()}

        if user_input is not None:
            links = {
                str(lst.id): user_input[name]
                for name, lst in fields.items()
                if user_input.get(name)
            }
            return self.async_create_entry(data={CONF_LINKS: links})

        # Syncing a Home Upkeep list with another Home Upkeep list is never
        # what is meant, so those entities are not offered.
        own = [
            entry.entity_id
            for entry in er.async_entries_for_config_entry(
                er.async_get(self.hass), self.config_entry.entry_id
            )
            if entry.domain == "todo"
        ]
        current = self.config_entry.options.get(CONF_LINKS, {})
        schema = vol.Schema(
            {
                vol.Optional(
                    name, description={"suggested_value": current.get(str(lst.id))}
                ): EntitySelector(
                    EntitySelectorConfig(domain="todo", exclude_entities=own)
                )
                for name, lst in fields.items()
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
