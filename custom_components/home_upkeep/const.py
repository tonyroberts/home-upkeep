"""Constants for the Home Upkeep integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "home_upkeep"

PLATFORMS = [Platform.CALENDAR, Platform.TODO]

SIGNAL_UPKEEP_CHANGED = "home_upkeep_changed"

# Config entry options. Syncing is optional: with no "server" (or no links)
# Home Upkeep does not talk to any CalDAV server at all.
CONF_SERVER = "server"  # {"url", "username", "password", "verify_ssl"}
CONF_LINKS = "links"  # {"<list id>": "<CalDAV collection URL>"}

STORAGE_KEY = "home_upkeep"
STORAGE_VERSION = 1

PANEL_URL_PATH = "home-upkeep"
PANEL_WEBCOMPONENT_NAME = "home-upkeep-panel"
PANEL_STATIC_PATH = "/home_upkeep_static"
PANEL_TITLE = "Home Upkeep"
PANEL_ICON = "mdi:duck"
