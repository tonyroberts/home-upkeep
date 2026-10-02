"""
One-time importer for existing add-on data.

Imports from copies of the add-on's `list_<id>.json` files
(`{version, list, tasks[]}`) — the only path that actually works for a real
install: the add-on only exposes itself via Home Assistant's ingress proxy,
so there's no plain HTTP endpoint this integration (or an external script)
could call instead. Since the storage models are the ones being ported, the
transform is essentially identity and original int IDs are preserved (see
`store.HomeUpkeepStore.async_import`).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from aiohasupervisor import SupervisorError
from aiohasupervisor.models import AddonState
from homeassistant.components.hassio.handler import get_supervisor_client
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .legacy_api import LegacyAddonApiClient, LegacyAddonApiError
from .models import StoredList, StoredTask
from .store import ImportConflictError, async_get_store

# ruff (TC002) wants type-only imports under TYPE_CHECKING to avoid an
# unnecessary runtime import, since `from __future__ import annotations`
# means annotations are never evaluated at runtime anyway.
if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

    from .store import HomeUpkeepStore

_LOGGER = logging.getLogger(__name__)

SERVICE_IMPORT_FROM_JSON = "import_from_json"

_IMPORT_FROM_JSON_SCHEMA = vol.Schema({vol.Required("path"): str})

# The add-on's own slug (from its config.yaml) is "home-upkeep"; Supervisor
# prefixes it with the installing repository's slug, so match on the suffix
# rather than the exact slug (see git history commit 18752e6, which first
# discovered the add-on this way for host/port lookup).
_ADDON_SLUG_SUFFIX = "_home-upkeep"


def _parse_docs(
    docs: list[dict[str, Any]],
) -> tuple[list[StoredList], list[StoredTask]]:
    """Parse `{version, list, tasks}` docs into StoredList/StoredTask objects."""
    lists: list[StoredList] = []
    tasks: list[StoredTask] = []
    for doc in docs:
        lists.append(StoredList.from_storage(doc["list"]))
        tasks.extend(StoredTask.from_storage(t) for t in doc.get("tasks", []))
    return lists, tasks


def _read_json_files(directory: str) -> list[dict[str, Any]]:
    """
    Read add-on `list_<id>.json` files from a directory.

    Blocking (filesystem I/O) — call via `hass.async_add_executor_job`.
    """
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(directory).glob("list_*.json"))
    ]


async def async_import_from_json(
    hass: HomeAssistant, store: HomeUpkeepStore, directory: str
) -> tuple[int, int]:
    """
    Import lists/tasks from copied add-on `list_<id>.json` files.

    Args:
        hass: The Home Assistant instance.
        store: The store to import into.
        directory: Path to a directory containing `list_<id>.json` files.

    Returns:
        The number of (lists, tasks) imported.

    """
    docs = await hass.async_add_executor_job(_read_json_files, directory)
    lists, tasks = _parse_docs(docs)
    return await store.async_import(lists, tasks)


async def async_import_from_docs(
    store: HomeUpkeepStore,
    docs: list[dict[str, Any]],
    *,
    overwrite_list_ids: set[int] | None = None,
    remap_conflicting_list_ids: bool = False,
) -> tuple[int, int]:
    """
    Import lists/tasks from already-parsed `{version, list, tasks}` docs.

    Used by `async_migrate_addon_docs` for the one-time upgrade migration
    from the retired add-on companion.

    Args:
        store: The store to import into.
        docs: Parsed `{version, list, tasks}` docs, one per list.
        overwrite_list_ids: IDs of conflicting lists the user has confirmed
            overwriting (see `HomeUpkeepStore.async_import`).
        remap_conflicting_list_ids: give any other conflicting list a
            fresh ID instead of raising `ImportConflictError` (see
            `HomeUpkeepStore.async_import`).

    Returns:
        The number of (lists, tasks) imported.

    """
    lists, tasks = _parse_docs(docs)
    return await store.async_import(
        lists,
        tasks,
        overwrite_list_ids=overwrite_list_ids,
        remap_conflicting_list_ids=remap_conflicting_list_ids,
    )


async def _async_handle_import_from_json(call: ServiceCall) -> None:
    """Handle the `import_from_json` service call."""
    store = async_get_store(call.hass)
    directory = call.data["path"]
    try:
        list_count, task_count = await async_import_from_json(
            call.hass, store, directory
        )
    except ImportConflictError as err:
        raise HomeAssistantError(str(err)) from err
    except OSError as err:
        msg = f"Could not read add-on export files at {directory}: {err}"
        raise HomeAssistantError(msg) from err
    except (KeyError, TypeError, ValueError) as err:
        # ValueError also covers json.JSONDecodeError from a corrupt file.
        msg = f"Malformed export data in {directory}: {err}"
        raise HomeAssistantError(msg) from err
    _LOGGER.info(
        "Imported %d lists and %d tasks from %s", list_count, task_count, directory
    )


async def async_migrate_addon_docs(
    store: HomeUpkeepStore, docs: list[dict[str, Any]]
) -> tuple[int, int]:
    """
    Import add-on docs into the store and mark `migrated_from_addon`.

    Shared by the one-time upgrade migration (`async_migrate_legacy_addon`,
    called directly from `__init__.py::async_setup_entry` — this repo used
    to be a separate integration that reached this via an HA service call;
    now it's the same integration, so it's just a function call). A list ID
    colliding with an existing panel list is remapped to a fresh ID rather
    than rejected: this is an unattended, one-shot migration with no user
    available to confirm an overwrite, and list IDs are independently
    sequential in both the old add-on-backed data and the panel, so a
    collision (e.g. both starting at ID 1) is the common case, not a rare
    one.
    """
    list_count, task_count = await async_import_from_docs(
        store, docs, remap_conflicting_list_ids=True
    )
    await store.async_mark_migrated_from_addon()
    return list_count, task_count


async def _async_build_legacy_addon_docs(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[dict[str, Any]]:
    """Pull all lists/tasks from the retired add-on's REST API as import docs."""
    client = LegacyAddonApiClient(
        host=entry.data[CONF_HOST],
        port=int(entry.data[CONF_PORT]),
        session=async_get_clientsession(hass),
    )
    docs = []
    for task_list in await client.async_get_lists():
        list_id = task_list["id"]
        tasks = await client.async_get_tasks(list_id)
        docs.append({"version": 1, "list": task_list, "tasks": tasks})
    return docs


async def async_migrate_legacy_addon(
    hass: HomeAssistant, entry: ConfigEntry, store: HomeUpkeepStore
) -> None:
    """
    One-time upgrade migration for pre-merge installs of this integration.

    Before this integration became the Home Upkeep panel, its config entry
    held the add-on's `host`/`port` (discovered via the `hassio` supervisor
    or entered manually) so it could poll the add-on's REST/WebSocket API
    for `todo` entities. A fresh install of the merged integration never
    writes those keys, so their presence in `entry.data` is exactly the
    signal that this is an upgrade with real add-on data to pull in. See
    `docs/superpowers/specs/2026-08-21-merge-panel-integration-design.md`.

    Any failure (add-on unreachable, malformed response) is caught and
    logged; `migrated_from_addon` is left unset so this is retried on the
    next start. On success, `CONF_HOST`/`CONF_PORT` are removed from the
    entry — they have no further purpose once migration has run.
    """
    if store.migrated_from_addon or CONF_HOST not in entry.data:
        return

    try:
        docs = await _async_build_legacy_addon_docs(hass, entry)
        list_count, task_count = await async_migrate_addon_docs(store, docs)
    except (LegacyAddonApiError, KeyError, TypeError, ValueError):
        _LOGGER.exception(
            "Automatic upgrade migration from the add-on failed; will retry "
            "on next start"
        )
        return

    _LOGGER.info(
        "Migrated %d list(s) and %d task(s) from the retired add-on",
        list_count,
        task_count,
    )
    new_data = {k: v for k, v in entry.data.items() if k not in (CONF_HOST, CONF_PORT)}
    hass.config_entries.async_update_entry(entry, data=new_data)


async def async_is_addon_running(hass: HomeAssistant) -> bool:
    """
    Return whether the retired Home Upkeep add-on is currently running.

    Backs the panel's "add-on detected, you can uninstall it now" banner so
    it clears itself once the add-on is actually gone, instead of relying on
    the store's `migrated_from_addon` flag, which is a one-time historical
    marker set at migration time and never reflects the add-on's current
    state (see `HomeUpkeepStore.async_mark_migrated_from_addon`).

    Calls the Supervisor client directly rather than going through hassio's
    own cached add-on list (`get_addons_list`): that cache is only refreshed
    every 15 minutes (`HASSIO_ADDON_UPDATE_INTERVAL`), with no event-based
    trigger on install/uninstall/start/stop, so it can be stale exactly when
    this check matters most — right after the user (un)installs the add-on.
    `"hassio" not in hass.config.components` is the only guard needed:
    on any install type without a Supervisor there's nothing further to ask.
    """
    if "hassio" not in hass.config.components:
        return False

    try:
        supervisor = get_supervisor_client(hass)
        addons = await supervisor.addons.list()
    except SupervisorError:
        _LOGGER.debug(
            "Could not reach Supervisor to check add-on status", exc_info=True
        )
        return False

    addon = next((a for a in addons if a.slug.endswith(_ADDON_SLUG_SUFFIX)), None)
    return addon is not None and addon.state == AddonState.STARTED


def async_register_services(hass: HomeAssistant) -> None:
    """Register the `import_from_json` service."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_FROM_JSON,
        _async_handle_import_from_json,
        schema=_IMPORT_FROM_JSON_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister the `import_from_json` service."""
    hass.services.async_remove(DOMAIN, SERVICE_IMPORT_FROM_JSON)
