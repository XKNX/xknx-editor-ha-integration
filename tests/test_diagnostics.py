"""Diagnostics for issue reports."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components.xknx_editor.const import CONF_EDITOR_PASSWORD, CONF_REQUIREMENTS, DOMAIN
from custom_components.xknx_editor.diagnostics import async_get_config_entry_diagnostics
from custom_components.xknx_editor.manager import EditorManager


async def test_diagnostics_without_secrets(hass: HomeAssistant) -> None:
    async def run(self: EditorManager) -> None:
        self.status, self.detail = "Failed to start", "no wheel"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EDITOR_PASSWORD: "very-secret"},
        options={CONF_REQUIREMENTS: "/config/requirements.txt"},
    )
    entry.add_to_hass(hass)
    with patch.object(EditorManager, "async_run", run):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    entry.runtime_data.sessions.create()
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["status"] == "Failed to start"
    assert result["detail"] == "no wheel"
    assert result["requirements"] == "/config/requirements.txt"
    assert result["environment"]["exists"] is False
    assert result["process"] == {"running": False, "runs": 0, "output": []}
    assert "very-secret" not in str(result)
    await entry.runtime_data.client.close()
