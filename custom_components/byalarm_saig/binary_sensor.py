"""Entita' binary_sensor per zone e stato sistema del gateway Vimar By-alarm."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SystemStatus, ZoneStatus
from .const import CONF_DUID, DOMAIN
from .coordinator import ByAlarmCoordinator

_LOGGER = logging.getLogger(__name__)


def _device_info(duid: str, title: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, duid)},
        name=title,
        manufacturer="Vimar",
        model="By-alarm (SAIG)",
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Crea le entita' binary_sensor per zone e stato sistema."""
    coordinator: ByAlarmCoordinator = hass.data[DOMAIN][entry.entry_id]
    duid = entry.data[CONF_DUID]

    entities: list[BinarySensorEntity] = []

    for description in SYSTEM_SENSORS:
        entities.append(ByAlarmSystemSensor(coordinator, entry, duid, description))

    status: SystemStatus | None = coordinator.data
    if status:
        for zone in status.zones:
            entities.append(ByAlarmZoneOpenedSensor(coordinator, entry, duid, zone.idsf))
            entities.append(ByAlarmZoneTamperSensor(coordinator, entry, duid, zone.idsf))

    async_add_entities(entities)


@dataclass(frozen=True, kw_only=True)
class SystemSensorDescription(BinarySensorEntityDescription):
    """Descrive un sensore di stato generale, con come leggerlo da SystemStatus."""

    value_fn: Callable[[SystemStatus], bool] = lambda status: False


SYSTEM_SENSORS: tuple[SystemSensorDescription, ...] = (
    SystemSensorDescription(
        key="area_alarm",
        translation_key="area_alarm",
        name="Allarme in corso",
        device_class=BinarySensorDeviceClass.SAFETY,
        value_fn=lambda status: status.area_alarm,
    ),
    SystemSensorDescription(
        key="area_alarm_memory",
        translation_key="area_alarm_memory",
        name="Allarme in memoria",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.area_alarm_memory,
    ),
    SystemSensorDescription(
        key="zone_tamper",
        translation_key="zone_tamper",
        name="Tamper zona",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda status: status.zone_tamper,
    ),
    SystemSensorDescription(
        key="zone_mask",
        translation_key="zone_mask",
        name="Zona mascherata",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda status: status.zone_mask,
    ),
    SystemSensorDescription(
        key="device_tamper_alarm",
        translation_key="device_tamper_alarm",
        name="Tamper dispositivo",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda status: status.device_tamper_alarm,
    ),
    SystemSensorDescription(
        key="device_tamper_alarm_memory",
        translation_key="device_tamper_alarm_memory",
        name="Tamper dispositivo in memoria",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.device_tamper_alarm_memory,
    ),
    SystemSensorDescription(
        key="maintenance",
        translation_key="maintenance",
        name="Manutenzione",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.maintenance,
    ),
)


class ByAlarmSystemSensor(CoordinatorEntity[ByAlarmCoordinator], BinarySensorEntity):
    """Sensore di stato generale del sistema (non specifico di una zona)."""

    _attr_has_entity_name = True
    entity_description: SystemSensorDescription

    def __init__(
        self,
        coordinator: ByAlarmCoordinator,
        entry: ConfigEntry,
        duid: str,
        description: SystemSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{duid}_{description.key}"
        self._attr_device_info = _device_info(duid, entry.title)

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data
        if status is None:
            return None
        return self.entity_description.value_fn(status)


def _find_zone(status: SystemStatus | None, idsf: int) -> ZoneStatus | None:
    if status is None:
        return None
    return next((z for z in status.zones if z.idsf == idsf), None)


class ByAlarmZoneOpenedSensor(CoordinatorEntity[ByAlarmCoordinator], BinarySensorEntity):
    """Stato di apertura di una singola zona (porta/finestra/sensore)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, coordinator: ByAlarmCoordinator, entry: ConfigEntry, duid: str, zone_idsf: int) -> None:
        super().__init__(coordinator)
        self._zone_idsf = zone_idsf
        self._attr_unique_id = f"{duid}_zone_{zone_idsf}_opened"
        self._attr_device_info = _device_info(duid, entry.title)

    @property
    def _zone(self) -> ZoneStatus | None:
        return _find_zone(self.coordinator.data, self._zone_idsf)

    @property
    def name(self) -> str | None:
        zone = self._zone
        return zone.name if zone else f"Zona {self._zone_idsf}"

    @property
    def is_on(self) -> bool | None:
        zone = self._zone
        return zone.opened if zone else None

    @property
    def available(self) -> bool:
        return super().available and self._zone is not None

    @property
    def extra_state_attributes(self) -> dict:
        zone = self._zone
        if not zone:
            return {}
        return {
            "numero_zona": zone.zone_id,
            "esclusa": zone.excluded,
            "temporizzata": zone.timed,
            "stato_allarme": zone.alarm_info,
        }


class ByAlarmZoneTamperSensor(CoordinatorEntity[ByAlarmCoordinator], BinarySensorEntity):
    """Stato di tamper di una singola zona."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ByAlarmCoordinator, entry: ConfigEntry, duid: str, zone_idsf: int) -> None:
        super().__init__(coordinator)
        self._zone_idsf = zone_idsf
        self._attr_unique_id = f"{duid}_zone_{zone_idsf}_tamper"
        self._attr_device_info = _device_info(duid, entry.title)

    @property
    def _zone(self) -> ZoneStatus | None:
        return _find_zone(self.coordinator.data, self._zone_idsf)

    @property
    def name(self) -> str | None:
        zone = self._zone
        base = zone.name if zone else f"Zona {self._zone_idsf}"
        return f"{base} tamper"

    @property
    def is_on(self) -> bool | None:
        zone = self._zone
        return zone.tamper if zone else None

    @property
    def available(self) -> bool:
        return super().available and self._zone is not None
