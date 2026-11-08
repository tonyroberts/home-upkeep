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
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_LINKS, CONF_SERVER, DOMAIN
from .remote import (
    CannotConnectError,
    Collection,
    InvalidAuthError,
    ServerSettings,
    async_create_collection,
    async_discover_collections,
)
from .store import async_get_store
from .sync import server_settings

if TYPE_CHECKING:
    from .models import StoredList

NOT_SYNCED = ""
CREATE_NEW = "__create__"


class HomeUpkeepConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Home Upkeep."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,  # noqa: ARG004 - signature fixed by HA
    ) -> HomeUpkeepOptionsFlow:
        """Create the options flow (optional CalDAV sync)."""
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


def _settings_to_options(settings: ServerSettings) -> dict[str, Any]:
    return {
        "url": settings.url,
        "username": settings.username,
        "password": settings.password,
        "verify_ssl": settings.verify_ssl,
    }


class HomeUpkeepOptionsFlow(OptionsFlow):
    """Optional CalDAV sync: connect a server, then choose a collection per list."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._settings: ServerSettings | None = None
        self._collections: list[Collection] = []

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - HA step signature
    ) -> ConfigFlowResult:
        """Offer what can be done."""
        configured = server_settings(self.config_entry.options) is not None
        menu = ["server", "lists", "disable"] if configured else ["server"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_server(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the CalDAV server and check that it can be reached."""
        errors: dict[str, str] = {}
        current = self.config_entry.options.get(CONF_SERVER, {})
        if user_input is not None:
            settings = ServerSettings(
                url=user_input["url"].strip(),
                username=user_input.get("username", ""),
                password=user_input.get("password", ""),
                verify_ssl=user_input.get("verify_ssl", True),
            )
            try:
                self._collections = await async_discover_collections(
                    self.hass, settings
                )
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            else:
                self._settings = settings
                return await self.async_step_lists()
            current = user_input

        schema = vol.Schema(
            {
                vol.Required(
                    "url", description={"suggested_value": current.get("url")}
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Optional(
                    "username", description={"suggested_value": current.get("username")}
                ): TextSelector(),
                vol.Optional(
                    "password", description={"suggested_value": current.get("password")}
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(
                    "verify_ssl", default=current.get("verify_ssl", True)
                ): bool,
            }
        )
        return self.async_show_form(step_id="server", data_schema=schema, errors=errors)

    async def async_step_lists(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose, per Home Upkeep list, which CalDAV collection it syncs with."""
        if self._settings is None:
            self._settings = server_settings(self.config_entry.options)
            if self._settings is None:  # not reachable from the menu; be safe
                return await self.async_step_server()
            try:
                self._collections = await async_discover_collections(
                    self.hass, self._settings
                )
            except (InvalidAuthError, CannotConnectError):
                return await self.async_step_server()

        store = async_get_store(self.hass)
        fields = {_field_name(lst): lst for lst in store.list_lists()}

        if user_input is not None:
            links: dict[str, str] = {}
            for name, lst in fields.items():
                choice = user_input.get(name, NOT_SYNCED)
                if choice == CREATE_NEW:
                    try:
                        created = await async_create_collection(
                            self.hass, self._settings, lst.name
                        )
                    except (InvalidAuthError, CannotConnectError):
                        return self.async_abort(reason="cannot_connect")
                    choice = created.url
                if choice:
                    links[str(lst.id)] = choice
            return self.async_create_entry(
                data={
                    CONF_SERVER: _settings_to_options(self._settings),
                    CONF_LINKS: links,
                }
            )

        current = self.config_entry.options.get(CONF_LINKS, {})
        options = [
            SelectOptionDict(value=NOT_SYNCED, label="Not synced"),
            SelectOptionDict(value=CREATE_NEW, label="Create a new collection"),
            *(SelectOptionDict(value=c.url, label=c.name) for c in self._collections),
        ]
        schema = vol.Schema(
            {
                vol.Optional(
                    name, default=current.get(str(lst.id), NOT_SYNCED)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options, mode=SelectSelectorMode.DROPDOWN
                    )
                )
                for name, lst in fields.items()
            }
        )
        return self.async_show_form(step_id="lists", data_schema=schema)

    async def async_step_disable(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - HA step signature
    ) -> ConfigFlowResult:
        """Turn syncing off (the server settings are kept)."""
        return self.async_create_entry(
            data={
                CONF_SERVER: self.config_entry.options.get(CONF_SERVER, {}),
                CONF_LINKS: {},
            }
        )
