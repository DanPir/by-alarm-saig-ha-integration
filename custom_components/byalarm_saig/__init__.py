"""Integrazione Home Assistant per il gateway Vimar By-alarm (SAIG)."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PIN, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

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
from .coordinator import ByAlarmCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR]

CARD_URL_PATH = "/byalarm_saig/byalarm-card.js"
_card_registered = False


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve la card Lovelace custom e la registra come risorsa frontend.

    Fatto una sola volta per l'intera istanza (non per ogni config entry),
    cosi' funziona correttamente anche con piu' gateway configurati.
    """
    global _card_registered
    if _card_registered:
        return

    www_dir = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(www_dir / "byalarm-card.js"), True)]
    )
    add_extra_js_url(hass, CARD_URL_PATH)
    _card_registered = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura l'integrazione a partire da una config entry."""
    await _async_register_card(hass)

    data = entry.data

    client = ByAlarmClient(
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        useruid=data[CONF_USERUID],
        duid=data[CONF_DUID],
        device_password=data[CONF_DEVICE_PASSWORD],
        source_uid=data[CONF_SOURCE_UID],
        pin=data[CONF_PIN],
        port=DEFAULT_PORT,
    )

    try:
        await client.connect()
    except ByAlarmAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ByAlarmConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = ByAlarmCoordinator(hass, client, data[CONF_AREA_IDSF])
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Scarica l'integrazione."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: ByAlarmCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.disconnect()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ricarica l'integrazione se la configurazione cambia."""
    await hass.config_entries.async_reload(entry.entry_id)
