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

from .api import ActivationLine, DeviceStatus, SystemStatus, ZoneStatus
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
    status: SystemStatus | None = coordinator.data
    saig2 = bool(status and status.saig2)

    # Sulla By-alarm Plus i campi generali originali non esistono: restano "Allarme in corso" (dalle
    # aree) e "Allarme in memoria" (dalle zone); al loro posto anomalie centrale e tamper di tastiere/espansioni.
    for description in SYSTEM_SENSORS:
        if not saig2 or description.key in ("area_alarm", "area_alarm_memory"):
            entities.append(ByAlarmSystemSensor(coordinator, entry, duid, description))

    if status:
        for zone in status.zones:
            entities.append(ByAlarmZoneOpenedSensor(coordinator, entry, duid, zone.idsf))
            entities.append(ByAlarmZoneTamperSensor(coordinator, entry, duid, zone.idsf))
            if saig2:
                entities.append(ByAlarmZoneMaskSensor(coordinator, entry, duid, zone.idsf))
        if saig2:
            for key in status.anomalies:
                entities.append(ByAlarmAnomalySensor(coordinator, entry, duid, key))
            for device in status.devices:
                entities.append(ByAlarmDeviceTamperSensor(coordinator, entry, duid, device.idsf))
            for line in status.lines:
                entities.append(ByAlarmActivationLineSensor(coordinator, entry, duid, line.idsf))

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


class ByAlarmZoneMaskSensor(CoordinatorEntity[ByAlarmCoordinator], BinarySensorEntity):
    """Mascheramento di una zona (By-alarm Plus). Disattivato di default: ha senso solo sui
    rilevatori con antimascheramento cablato (es. rilevatori da esterno a triplo bilanciamento)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "zone_masked"

    def __init__(self, coordinator: ByAlarmCoordinator, entry: ConfigEntry, duid: str, zone_idsf: int) -> None:
        super().__init__(coordinator)
        self._zone_idsf = zone_idsf
        self._attr_unique_id = f"{duid}_zone_{zone_idsf}_mask"
        self._attr_device_info = _device_info(duid, entry.title)
        zone = self._zone
        self._attr_translation_placeholders = {"zone": zone.name if zone else f"Zona {zone_idsf}"}

    @property
    def _zone(self) -> ZoneStatus | None:
        return _find_zone(self.coordinator.data, self._zone_idsf)

    @property
    def is_on(self) -> bool | None:
        zone = self._zone
        return zone.mask if zone else None

    @property
    def available(self) -> bool:
        return super().available and self._zone is not None


# Anomalie della centrale (SS_SaiPlant_ControlUnitAnomalies): chiave di traduzione, classe, attivo di default.
# A riposo valgono "Idle"; qualunque altro valore conta come anomalia (il valore di guasto non e' noto).
ANOMALIES: dict[str, tuple[str, BinarySensorDeviceClass, bool]] = {
    "SFE_State_DeviceTamper": ("anomaly_device_tamper", BinarySensorDeviceClass.TAMPER, True),
    "SFE_State_ControlUnitNoLink": ("anomaly_control_unit_no_link", BinarySensorDeviceClass.PROBLEM, True),
    "SFE_State_MainsACAbsent": ("anomaly_mains_ac_absent", BinarySensorDeviceClass.PROBLEM, True),
    "SFE_State_LowBattery": ("anomaly_low_battery", BinarySensorDeviceClass.BATTERY, True),
    "SFE_State_BatteryDisconnected": ("anomaly_battery_disconnected", BinarySensorDeviceClass.PROBLEM, True),
    "SFE_State_BatteryShortCircuit": ("anomaly_battery_short_circuit", BinarySensorDeviceClass.PROBLEM, True),
    "SFE_State_BatteryInefficiency": ("anomaly_battery_inefficiency", BinarySensorDeviceClass.PROBLEM, True),
    "SFE_State_PSTNNoLine": ("anomaly_pstn_no_line", BinarySensorDeviceClass.PROBLEM, False),
    "SFE_State_GSMNoLine": ("anomaly_gsm_no_line", BinarySensorDeviceClass.PROBLEM, True),
    "SFE_State_GSMJamming": ("anomaly_gsm_jamming", BinarySensorDeviceClass.PROBLEM, True),
    "SFE_State_SomeDeviceBatteryLow": ("anomaly_some_device_battery_low", BinarySensorDeviceClass.BATTERY, True),
}


class ByAlarmAnomalySensor(CoordinatorEntity[ByAlarmCoordinator], BinarySensorEntity):
    """Un'anomalia della centrale (By-alarm Plus)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ByAlarmCoordinator, entry: ConfigEntry, duid: str, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        known = ANOMALIES.get(key)
        if known:
            translation_key, device_class, enabled = known
            self._attr_translation_key = translation_key
        else:  # campo non previsto: nome grezzo, disattivato di default
            device_class, enabled = BinarySensorDeviceClass.PROBLEM, False
            self._attr_name = key.removeprefix("SFE_State_")
        self._attr_device_class = device_class
        self._attr_entity_registry_enabled_default = enabled
        self._attr_unique_id = f"{duid}_anomaly_{key.removeprefix('SFE_State_')}"
        self._attr_device_info = _device_info(duid, entry.title)

    @property
    def _value(self) -> str | None:
        status = self.coordinator.data
        return status.anomalies.get(self._key) if status else None

    @property
    def is_on(self) -> bool | None:
        value = self._value
        return None if value is None else value != "Idle"

    @property
    def available(self) -> bool:
        return super().available and self._value is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"valore_centrale": self._value}


class ByAlarmDeviceTamperSensor(CoordinatorEntity[ByAlarmCoordinator], BinarySensorEntity):
    """Tamper di una tastiera o di un'espansione (By-alarm Plus)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def __init__(self, coordinator: ByAlarmCoordinator, entry: ConfigEntry, duid: str, device_idsf: int) -> None:
        super().__init__(coordinator)
        self._device_idsf = device_idsf
        self._attr_unique_id = f"{duid}_device_{device_idsf}_tamper"
        self._attr_device_info = _device_info(duid, entry.title)
        device = self._device
        if device:
            self._attr_translation_key = "keyboard_tamper" if device.kind == "keyboard" else "expansion_tamper"
            self._attr_translation_placeholders = {"device": device.name}
        else:
            self._attr_name = f"Dispositivo {device_idsf} tamper"

    @property
    def _device(self) -> DeviceStatus | None:
        status = self.coordinator.data
        if status is None:
            return None
        return next((d for d in status.devices if d.idsf == self._device_idsf), None)

    @property
    def is_on(self) -> bool | None:
        device = self._device
        return None if device is None or not device.tamper else device.tamper != "Idle"

    @property
    def available(self) -> bool:
        return super().available and self._device is not None

    @property
    def extra_state_attributes(self) -> dict:
        device = self._device
        return {"valore_centrale": device.tamper} if device else {}


class ByAlarmActivationLineSensor(CoordinatorEntity[ByAlarmCoordinator], BinarySensorEntity):
    """Linea di attivazione (By-alarm Plus): un'uscita della centrale, acceso = attiva.
    A cosa serve dipende dall'impianto, quindi resta tra le informazioni diagnostiche."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ByAlarmCoordinator, entry: ConfigEntry, duid: str, line_idsf: int) -> None:
        super().__init__(coordinator)
        self._line_idsf = line_idsf
        self._attr_unique_id = f"{duid}_line_{line_idsf}"
        self._attr_device_info = _device_info(duid, entry.title)
        line = self._line
        self._attr_name = line.name if line and line.name else f"Linea di attivazione {line_idsf}"

    @property
    def _line(self) -> ActivationLine | None:
        status = self.coordinator.data
        if status is None:
            return None
        return next((ln for ln in status.lines if ln.idsf == self._line_idsf), None)

    @property
    def is_on(self) -> bool | None:
        line = self._line
        return None if line is None or not line.on_off else line.on_off == "On"

    @property
    def available(self) -> bool:
        return super().available and self._line is not None

    @property
    def extra_state_attributes(self) -> dict:
        line = self._line
        return {"valore_centrale": line.on_off, "trigger": line.trigger} if line else {}
