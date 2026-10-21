"""Config flow per l'integrazione Vimar By-alarm (SAIG)."""
from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PIN, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api import ByAlarmAuthError, ByAlarmClient, ByAlarmConnectionError
from .const import (
    CONF_AREA_IDSF,
    CONF_DEVICE_PASSWORD,
    CONF_DUID,
    CONF_SOURCE_UID,
    CONF_USERUID,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_USERUID): str,
        vol.Required(CONF_DUID): str,
        vol.Required(CONF_DEVICE_PASSWORD): str,
        vol.Required(CONF_PIN): str,
    }
)


async def _validate_and_discover(data: dict[str, Any]) -> dict[str, Any]:
    """Si collega davvero al gateway per validare le credenziali e trova l'area."""
    source_uid = str(uuid.uuid4())
    client = ByAlarmClient(
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        useruid=data[CONF_USERUID],
        duid=data[CONF_DUID],
        device_password=data[CONF_DEVICE_PASSWORD],
        source_uid=source_uid,
        pin=data[CONF_PIN],
        port=DEFAULT_PORT,
    )
    try:
        await client.connect()
        status = await client.async_get_status()
    finally:
        await client.disconnect()

    if not status.areas:
        raise ByAlarmNoAreaError

    return {
        "source_uid": source_uid,
        "area_idsf": status.areas[0].idsf,
        "area_name": status.areas[0].name,
    }


class ByAlarmNoAreaError(Exception):
    """Nessuna area trovata sul gateway."""


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow per Vimar By-alarm."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                discovered = await _validate_and_discover(user_input)
            except ByAlarmAuthError:
                errors["base"] = "invalid_auth"
            except ByAlarmNoAreaError:
                errors["base"] = "no_area"
            except ByAlarmConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Errore imprevisto durante la validazione")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_DUID])
                self._abort_if_unique_id_configured()

                entry_data = {
                    **user_input,
                    CONF_SOURCE_UID: discovered["source_uid"],
                    CONF_AREA_IDSF: discovered["area_idsf"],
                }
                return self.async_create_entry(
                    title=f"By-alarm {discovered['area_name'] or user_input[CONF_HOST]}",
                    data=entry_data,
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Avviato da ConfigEntryAuthFailed quando il gateway rifiuta il login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Chiede di nuovo le credenziali, le ri-valida e aggiorna la config entry."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = {**entry.data, **user_input}
            try:
                await _validate_and_discover(candidate)
            except ByAlarmAuthError:
                errors["base"] = "invalid_auth"
            except ByAlarmNoAreaError:
                errors["base"] = "no_area"
            except ByAlarmConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Errore imprevisto durante la ri-autenticazione")
                errors["base"] = "unknown"
            else:
                # source_uid e area_idsf restano quelli originali: il test di
                # validazione ne genera di nuovi solo per la prova di login.
                changed = self.hass.config_entries.async_update_entry(entry, data=candidate)
                if not changed:
                    await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                vol.Required(CONF_USERUID, default=entry.data[CONF_USERUID]): str,
                vol.Required(CONF_DEVICE_PASSWORD): str,
                vol.Required(CONF_PIN): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )
