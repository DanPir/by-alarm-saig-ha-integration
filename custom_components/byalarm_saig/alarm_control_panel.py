"""Entita alarm_control_panel per il gateway Vimar By-alarm (SAIG).

Mappatura dei modi di inserimento (scelta ragionevole, personalizzabile in
futuro): Totale->Away, Interno->Home (esclude i sensori di movimento interni,
adatto a restare in casa), Parziale->Night.
"""
from __future__ import annotations

import logging

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ByAlarmError
from .const import CONF_DUID, DOMAIN
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
    """Crea l'entita alarm_control_panel per questa config entry."""
    coordinator: ByAlarmCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ByAlarmPanel(coordinator, entry)])


class ByAlarmPanel(CoordinatorEntity[ByAlarmCoordinator], AlarmControlPanelEntity):
    """Rappresenta l'area antifurto del gateway By-alarm."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "vimar_alarm"
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )
    _attr_code_arm_required = False  # il PIN e' gia' configurato nell'integrazione

    def __init__(self, coordinator: ByAlarmCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        duid = entry.data[CONF_DUID]
        self._attr_unique_id = f"{duid}_area_{coordinator.area_idsf}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, duid)},
            name=entry.title,
            manufacturer="Vimar",
            model="By-alarm (SAIG)",
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        status = self.coordinator.data
        if status is None or not status.areas:
            return None
        area = next((a for a in status.areas if a.idsf == self.coordinator.area_idsf), status.areas[0])
        if status.area_alarm:
            return AlarmControlPanelState.TRIGGERED
        return _STATE_MAP.get(area.insert)

    @property
    def extra_state_attributes(self) -> dict:
        status = self.coordinator.data
        if status is None:
            return {}
        area = next(
            (a for a in status.areas if a.idsf == self.coordinator.area_idsf),
            status.areas[0] if status.areas else None,
        )
        attrs = {
            "manutenzione": status.maintenance,
            "allarme_in_memoria": status.area_alarm_memory,
            "tamper_zona": status.zone_tamper,
            "tamper_dispositivo": status.device_tamper_alarm,
        }
        if area:
            attrs["zone_aperte"] = [z.name for z in status.zones if z.opened]
            attrs["zone_tamper"] = [z.name for z in status.zones if z.tamper]
            attrs["zone_escluse"] = [z.name for z in status.zones if z.excluded]
        return attrs

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._run_command(self.coordinator.async_disarm())

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._run_command(self.coordinator.async_arm("Tot"))

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._run_command(self.coordinator.async_arm("Int"))

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._run_command(self.coordinator.async_arm("Par"))

    async def _run_command(self, coro) -> None:
        try:
            await coro
        except ByAlarmError as err:
            _LOGGER.error("Comando rifiutato dal gateway By-alarm: %s", err)
            raise
