"""Setting up the integration and its options."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.xknx_editor.const import (
    CONF_EDITOR_PASSWORD,
    CONF_REQUIREMENTS,
    DOMAIN,
)


async def test_user_step_creates_entry_with_password(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.xknx_editor.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "XKNX Editor"
    # A random password of its own for every installation.
    assert len(result["data"][CONF_EDITOR_PASSWORD]) >= 32


async def test_only_one_entry(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data={CONF_EDITOR_PASSWORD: "x"}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_set_and_clear_requirements(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_EDITOR_PASSWORD: "x"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_REQUIREMENTS: " /config/test-requirements.txt "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_REQUIREMENTS] == "/config/test-requirements.txt"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert entry.options[CONF_REQUIREMENTS] == ""
