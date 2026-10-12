"""Diagnostics support for the Home Upkeep integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import HomeUpkeepConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001
    entry: HomeUpkeepConfigEntry,
) -> dict[str, Any]:
    """
    Return diagnostics for a config entry.

    Only counts and flags are included; task titles and descriptions are
    user content and are deliberately left out.
    """
    store = entry.runtime_data
    lists = store.list_lists()
    return {
        "domain": DOMAIN,
        "migrated_from_addon": store.migrated_from_addon,
        "list_count": len(lists),
        "lists": [
            {
                "id": lst.id,
                "task_count": len(tasks := store.list_tasks(lst.id)),
                "completed_count": sum(t.completed for t in tasks),
                "recurring_count": sum(bool(t.reschedule_period) for t in tasks),
                "with_due_date_count": sum(t.due_date is not None for t in tasks),
            }
            for lst in lists
        ],
    }
