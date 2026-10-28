"""Setting up XKNX Editor: one confirmation; an advanced option for another editor build."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import CONF_EDITOR_PASSWORD, CONF_REQUIREMENTS, DOMAIN, PANEL_TITLE


class XknxEditorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add XKNX Editor (once)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=PANEL_TITLE,
                data={CONF_EDITOR_PASSWORD: secrets.token_urlsafe(32)},
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return XknxEditorOptionsFlow()


class XknxEditorOptionsFlow(OptionsFlow):
    """Another requirements file than the release's, e.g. to try an editor build."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_REQUIREMENTS: user_input.get(CONF_REQUIREMENTS, "").strip()}
            )
        current = self.config_entry.options.get(CONF_REQUIREMENTS, "")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Optional(CONF_REQUIREMENTS, description={"suggested_value": current}): str}
            ),
        )
