"""Integrazione Home Assistant per il gateway Vimar By-alarm (SAIG)."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PIN, CONF_USERNAME, EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.loader import async_get_integration

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
    # Il file viene servito con cache lunga (31 giorni): appendere la
    # versione dell'integrazione come query string forza i browser a
    # scaricare la nuova card ad ogni aggiornamento invece di continuare a
    # servire una copia in cache obsoleta.
    integration = await async_get_integration(hass, DOMAIN)
    resource_url = f"{CARD_URL_PATH}?v={integration.version}"

    # Durante l'avvio di Home Assistant lo storage delle risorse Lovelace
    # potrebbe non essere ancora completamente caricato quando questa
    # funzione gira (dipendenza dichiarata su "lovelace", ma il caricamento
    # dei dati persistiti puo' completarsi dopo il semplice async_setup del
    # componente): se HA e' ancora in fase di avvio, rimandiamo a dopo
    # l'evento "started", quando tutti i componenti hanno finito. In caso
    # di reload della sola integrazione (HA gia' avviato) eseguiamo subito.
    if hass.is_running:
        await _async_ensure_lovelace_resource(hass, resource_url)
    else:

        async def _on_started(_event: Event) -> None:
            await _async_ensure_lovelace_resource(hass, resource_url)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)

    _card_registered = True


async def _async_ensure_lovelace_resource(hass: HomeAssistant, resource_url: str) -> None:
    """Crea o aggiorna la risorsa Lovelace persistente per la card.

    Inizialmente si usava add_extra_js_url (stato solo in-memory, ricreato
    ad ogni riavvio): risultato inaffidabile su alcuni client - in
    particolare l'app companion Android non vedeva sempre la card
    ("Custom element doesn't exist"), pur funzionando su browser desktop.
    Le risorse Lovelace persistenti (stesso meccanismo usato da HACS per
    le sue card, vedi Impostazioni -> Dashboard -> Risorse) vengono invece
    rilette da ogni client ad ogni connessione, in modo uniforme.
    """
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        _LOGGER.warning(
            "Impossibile registrare la risorsa Lovelace della card: "
            "hass.data['lovelace'].resources non disponibile"
        )
        return

    try:
        existing = next(
            (item for item in resources.async_items() if item["url"].startswith(CARD_URL_PATH)),
            None,
        )
        if existing is None:
            await resources.async_create_item({"res_type": "module", "url": resource_url})
        elif existing["url"] != resource_url:
            await resources.async_update_item(existing["id"], {"url": resource_url})
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Impossibile aggiornare la risorsa Lovelace della card: %s", err)


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
