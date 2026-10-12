"""Tests for the home_upkeep diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.home_upkeep.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.home_upkeep.store import async_get_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

EXPECTED_TASKS = 2


async def test_diagnostics_counts_without_user_content(
    setup_integration: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Diagnostics report counts and exclude titles/descriptions."""
    store = async_get_store(hass)
    lst = store.create_list("Secret list name")
    store.create_task(lst.id, "Secret task", "Secret description")
    store.create_task(lst.id, "Mop", None, reschedule_period="1w")

    result = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert result["list_count"] == 1
    assert result["lists"][0]["task_count"] == EXPECTED_TASKS
    assert result["lists"][0]["recurring_count"] == 1
    assert "Secret" not in str(result)
