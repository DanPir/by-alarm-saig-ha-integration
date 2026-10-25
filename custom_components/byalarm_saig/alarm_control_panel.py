"""Entita alarm_control_panel per il gateway Vimar By-alarm (SAIG).

Mappatura dei modi di inserimento (scelta ragionevole, personalizzabile in
futuro): Totale->Away, Interno->Home (esclude i sensori di movimento interni,
adatto a restare in casa), Parziale->Night.

Variante "plus": un pannello per ogni area letta dal gateway (By-alarm Plus
con piu' aree). Il pannello della prima area mantiene lo stesso unique_id
dell'integrazione originale. Sulla By-alarm Plus (blocchi Saig2) l'area ha un solo
comando, SFE_Cmd_Insert, e i modi ammessi per il codice utente in SFE_AvailableInsertMode:
il pannello offre solo quelli (Totale -> Away; i parziali A-D non sono ancora gestiti).
Senza permessi di comando l'elenco e' vuoto e il pannello resta in sola lettura.
"""
from __future__ import annotations

import logging
import time

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import AreaStatus, ByAlarmError
from .const import CONF_DUID, CONFIRM_TIMEOUT, DOMAIN
from .coordinator import ByAlarmCoordinator

_LOGGER = logging.getLogger(__name__)

# stato SAI ("Dis"/"Tot"/"Int"/"Par") -> stato Home Assistant
_STATE_MAP = {
    "Dis": AlarmControlPanelState.DISARMED,
    "Tot": AlarmControlPanelState.ARMED_AWAY,
    "Int": AlarmControlPanelState.ARMED_HOME,
    "Par": AlarmControlPanelState.ARMED_NIGHT,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Crea un'entita alarm_control_panel per ogni area del gateway."""
    coordinator: ByAlarmCoordinator = hass.data[DOMAIN][entry.entry_id]
    status = coordinator.data
    areas = status.areas if status and status.areas else []
    if areas:
        entities = [ByAlarmPanel(coordinator, entry, a.idsf, a.name, multi=len(areas) > 1) for a in areas]
    else:
        entities = [ByAlarmPanel(coordinator, entry, coordinator.area_idsf, None, multi=False)]
    async_add_entities(entities)


class ByAlarmPanel(CoordinatorEntity[ByAlarmCoordinator], AlarmControlPanelEntity):
    """Rappresenta un'area antifurto del gateway By-alarm."""

    _attr_has_entity_name = True
    _attr_translation_key = "vimar_alarm"
    _attr_code_arm_required = False  # il PIN e' gia' configurato nell'integrazione

    def __init__(
        self,
        coordinator: ByAlarmCoordinator,
        entry: ConfigEntry,
        area_idsf: int,
        area_name: str | None,
        multi: bool,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._area_idsf = area_idsf
        self._multi = multi
        # comando appena mandato: (stato atteso "Dis"/"Tot"/..., scadenza) finche' la centrale non conferma
        self._pending: tuple[str, float] | None = None
        duid = entry.data[CONF_DUID]
        self._attr_unique_id = f"{duid}_area_{area_idsf}"
        # con una sola area il nome resta quello del dispositivo (come l'originale)
        self._attr_name = area_name if multi and area_name else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, duid)},
            name=entry.title,
            manufacturer="Vimar",
            model="By-alarm (SAIG)",
        )

    def _saig2_modes(self) -> dict | None:
        """By-alarm Plus: modi ammessi per quest'area (None sulla SAIG originale)."""
        status = self.coordinator.data
        if not (status and status.saig2):
            return None
        area = self._area()
        return area.insert_modes if area else {}

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        modes = self._saig2_modes()
        if modes is not None:
            if "Tot" in modes:
                return AlarmControlPanelEntityFeature.ARM_AWAY
            return AlarmControlPanelEntityFeature(0)
        return (
            AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_NIGHT
        )

    def _area(self) -> AreaStatus | None:
        status = self.coordinator.data
        if status is None or not status.areas:
            return None
        return next((a for a in status.areas if a.idsf == self._area_idsf), None if self._multi else status.areas[0])

    @property
    def available(self) -> bool:
        return super().available and self._area() is not None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        area = self._area()
        if area is None:
            return None
        status = self.coordinator.data
        # stato di allarme: per area (zone in allarme); con una sola area vale anche quello generale
        if area.alarm or (not self._multi and status.area_alarm):
            return AlarmControlPanelState.TRIGGERED
        if self._pending:
            return AlarmControlPanelState.DISARMING if self._pending[0] == "Dis" else AlarmControlPanelState.ARMING
        return _STATE_MAP.get(area.insert)

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending:
            area = self._area()
            if (area and area.insert == self._pending[0]) or time.monotonic() >= self._pending[1]:
                self._pending = None
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        status = self.coordinator.data
        area = self._area()
        if status is None or area is None:
            return {}
        attrs = {"id_area": area.idsf}
        if not status.saig2:  # sulla By-alarm Plus questi campi non esistono: sarebbero sempre falsi
            attrs.update({
                "manutenzione": status.maintenance,
                "allarme_in_memoria": status.area_alarm_memory,
                "tamper_zona": status.zone_tamper,
                "tamper_dispositivo": status.device_tamper_alarm,
            })
        zones = [z for z in status.zones if not area.zone_ids or z.zone_id in area.zone_ids]
        attrs["zone_aperte"] = [z.name for z in zones if z.opened]
        attrs["zone_tamper"] = [z.name for z in zones if z.tamper]
        attrs["zone_escluse"] = [z.name for z in zones if z.excluded]
        attrs["zone_in_allarme"] = [z.name for z in zones if z.zone_id in area.zone_alarms]
        return attrs

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self._check_saig2_mode("Dis")
        await self._run_command("Dis", self.coordinator.async_disarm(self._area_idsf))

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._check_saig2_mode("Tot")
        await self._run_command("Tot", self.coordinator.async_arm("Tot", self._area_idsf))

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        self._check_saig2_mode("Int")
        await self._run_command("Int", self.coordinator.async_arm("Int", self._area_idsf))

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        self._check_saig2_mode("Par")
        await self._run_command("Par", self.coordinator.async_arm("Par", self._area_idsf))

    def _check_saig2_mode(self, mode: str) -> None:
        """By-alarm Plus: niente comando al gateway se il modo non e' ammesso per il codice utente."""
        modes = self._saig2_modes()
        if modes is not None and mode not in modes:
            raise HomeAssistantError(
                f"Mode '{mode}' is not available for this area with the configured user code"
            )

    async def _run_command(self, target: str, coro) -> None:
        # subito "in corso" sul pannello: la conferma della centrale arriva dopo qualche secondo
        self._pending = (target, time.monotonic() + CONFIRM_TIMEOUT)
        self.async_write_ha_state()
        try:
            await coro
        except Exception as err:
            self._pending = None
            self.async_write_ha_state()
            if isinstance(err, ByAlarmError):
                _LOGGER.error("Comando rifiutato dal gateway By-alarm: %s", err)
            raise
