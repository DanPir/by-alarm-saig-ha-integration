"""Prove dei sensori binari sulla By-alarm Plus (plus.2): anomalie centrale, tamper tastiere/espansioni,
mascheramento zone, niente sensori generali finti. Blocchi come letti dalla centrale il 28/09/2026."""
import asyncio
import enum
import importlib.util
import os
import sys
import types
import unittest
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "custom_components", "byalarm_saig")


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


# --- controfigure di homeassistant ---------------------------------------------------------
class BinarySensorDeviceClass(str, enum.Enum):
    OPENING = "opening"; TAMPER = "tamper"; PROBLEM = "problem"; SAFETY = "safety"; BATTERY = "battery"


class EntityCategory(str, enum.Enum):
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True, kw_only=True)
class BinarySensorEntityDescription:
    key: str
    translation_key: str | None = None
    name: str | None = None
    device_class: object = None
    entity_category: object = None


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    @property
    def available(self):
        return True


class DataUpdateCoordinator:
    def __init__(self, *a, **k):
        pass

    def __class_getitem__(cls, item):
        return cls


for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
    _mod(name)
_mod("homeassistant.components.binary_sensor", BinarySensorDeviceClass=BinarySensorDeviceClass,
     BinarySensorEntity=object, BinarySensorEntityDescription=BinarySensorEntityDescription)
_mod("homeassistant.const", EntityCategory=EntityCategory)
_mod("homeassistant.config_entries", ConfigEntry=object)
_mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_mod("homeassistant.exceptions", ConfigEntryAuthFailed=Exception, ConfigEntryNotReady=Exception)
_mod("homeassistant.helpers.device_registry", DeviceInfo=dict)
_mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_mod("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity,
     DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=Exception)

pkg = types.ModuleType("byalarm_saig"); pkg.__path__ = [PKG]; sys.modules["byalarm_saig"] = pkg


def _load(sub):
    spec = importlib.util.spec_from_file_location(f"byalarm_saig.{sub}", os.path.join(PKG, f"{sub}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"byalarm_saig.{sub}"] = m
    spec.loader.exec_module(m)
    return m


api = _load("api"); const = _load("const"); _load("coordinator"); bs = _load("binary_sensor")

ANOM = ["DeviceTamper", "ControlUnitNoLink", "MainsACAbsent", "LowBattery", "BatteryDisconnected",
        "BatteryShortCircuit", "BatteryInefficiency", "PSTNNoLine", "GSMNoLine", "GSMJamming", "SomeDeviceBatteryLow"]


def plus(mains="Idle", kb_tamper="Idle", zone_alarm="[]", mask="False"):
    return [
        {"sstype": "SS_SaiPlant_All_Saig2", "idsf": 21000, "name": "All areas", "elements": [
            {"sfetype": "SFE_Cmd_MemResetAll", "value": ""}, {"sfetype": "SFE_Cmd_MuteAll", "value": ""}]},
        {"sstype": "SS_SaiPlant_ControlUnitAnomalies", "idsf": 34000, "name": "Control Unit Anomalies", "elements":
            [{"sfetype": f"SFE_State_{a}", "value": mains if a == "MainsACAbsent" else "Idle"} for a in ANOM]},
        {"sstype": "SS_SaiPlant_Keyboard", "idsf": 31501, "name": "Tastiera 1", "elements": [
            {"sfetype": "SFE_State_DeviceId", "value": "1"}, {"sfetype": "SFE_State_DeviceTamper", "value": kb_tamper}]},
        {"sstype": "SS_SaiPlant_Expansion", "idsf": 33004, "name": "Espansione 4", "elements": [
            {"sfetype": "SFE_State_DeviceId", "value": "4"}, {"sfetype": "SFE_State_DeviceTamper", "value": "Idle"}]},
        {"sstype": "SS_Area_Saig2", "idsf": 21001, "name": "Area A", "elements": [
            {"sfetype": "SFE_State_Insert", "value": "Tot"}, {"sfetype": "SFE_State_Zones", "value": "[1]"},
            {"sfetype": "SFE_State_ZoneAlarm", "value": zone_alarm}]},
        {"sstype": "SS_Zone", "idsf": 30001, "name": "Zona 5", "elements": [
            {"sfetype": "SFE_State_ZoneId", "value": "1"}, {"sfetype": "SFE_State_Opened", "value": "False"},
            {"sfetype": "SFE_State_Tamper", "value": "False"}, {"sfetype": "SFE_State_Mask", "value": mask}]},
    ]


def status(blocks):
    c = api.ByAlarmClient(host="x", username="x", useruid="x", duid="x", device_password="x", source_uid="x", pin="x")
    return c._parse_sai_discovery(blocks)


class Entry:
    data = {const.CONF_DUID: "D"}
    title = "Allarme"
    entry_id = "e1"


def setup(data):
    coord = types.SimpleNamespace(data=data)
    hass = types.SimpleNamespace(data={const.DOMAIN: {"e1": coord}})
    out = []
    asyncio.run(bs.async_setup_entry(hass, Entry(), out.extend))
    return {e._attr_unique_id: e for e in out}


class TestSensorsSaig2(unittest.TestCase):
    def test_niente_sensori_generali_finti(self):
        ids = setup(status(plus()))
        for finto in ("zone_tamper", "zone_mask", "device_tamper_alarm",
                      "device_tamper_alarm_memory", "maintenance"):
            self.assertNotIn(f"D_{finto}", ids)
        self.assertIn("D_area_alarm", ids)

    def test_tutto_a_riposo(self):
        ids = setup(status(plus()))
        self.assertEqual(len([k for k in ids if "_anomaly_" in k]), 11)
        self.assertEqual(len([k for k in ids if "_device_" in k]), 2)
        self.assertTrue(all(ids[k].is_on is False for k in ids if "_anomaly_" in k or "_device_" in k or k.endswith("_mask")))
        self.assertFalse(ids["D_area_alarm"].is_on)

    def test_anomalia_con_valore_sconosciuto_si_accende(self):
        ids = setup(status(plus(mains="Alarm")))
        self.assertTrue(ids["D_anomaly_MainsACAbsent"].is_on)
        self.assertEqual(ids["D_anomaly_MainsACAbsent"]._attr_translation_key, "anomaly_mains_ac_absent")
        self.assertEqual(ids["D_anomaly_MainsACAbsent"].extra_state_attributes["valore_centrale"], "Alarm")
        self.assertFalse(ids["D_anomaly_LowBattery"].is_on)

    def test_tamper_tastiera(self):
        ids = setup(status(plus(kb_tamper="Active")))
        t = ids["D_device_31501_tamper"]
        self.assertTrue(t.is_on)
        self.assertEqual((t._attr_translation_key, t._attr_translation_placeholders),
                         ("keyboard_tamper", {"device": "Tastiera 1"}))
        e = ids["D_device_33004_tamper"]
        self.assertEqual((e._attr_translation_key, e._attr_translation_placeholders),
                         ("expansion_tamper", {"device": "Espansione 4"}))

    def test_mascheramento_zona_disattivato_di_default(self):
        ids = setup(status(plus(mask="True")))
        m = ids["D_zone_30001_mask"]
        self.assertTrue(m.is_on)
        self.assertEqual((m._attr_translation_key, m._attr_translation_placeholders), ("zone_masked", {"zone": "Zona 5"}))
        self.assertFalse(m._attr_entity_registry_enabled_default)

    def test_allarme_in_memoria_dalle_zone(self):
        blocks = plus()
        for b in blocks:
            if b["sstype"] == "SS_Zone":
                b["elements"] = [e for e in b["elements"] if e["sfetype"] != "SFE_State_AlarmInfo"] + [
                    {"sfetype": "SFE_State_AlarmInfo", "value": "Memory"}]
        ids = setup(status(blocks))
        self.assertTrue(ids["D_area_alarm_memory"].is_on)
        self.assertFalse(setup(status(plus()))["D_area_alarm_memory"].is_on)

    def test_linee_di_attivazione(self):
        blocks = plus() + [
            {"sstype": "SS_ByalarmCommand_Saig2", "idsf": 30401, "name": "Linea di attivazione 1", "elements": [
                {"sfetype": "SFE_State_Trigger", "value": "On"}, {"sfetype": "SFE_State_OnOff", "value": "On"}]},
            {"sstype": "SS_ByalarmCommand_Saig2", "idsf": 30402, "name": "Linea di attivazione 2", "elements": [
                {"sfetype": "SFE_State_Trigger", "value": "On"}, {"sfetype": "SFE_State_OnOff", "value": "Off"}]}]
        ids = setup(status(blocks))
        l1, l2 = ids["D_line_30401"], ids["D_line_30402"]
        self.assertEqual((l1.is_on, l2.is_on), (True, False))
        self.assertEqual(l1._attr_name, "Linea di attivazione 1")
        self.assertEqual(l1._attr_entity_category, EntityCategory.DIAGNOSTIC)  # non in evidenza
        self.assertFalse(l1._attr_entity_registry_enabled_default)  # utile solo a chi usa le linee
        self.assertEqual(l1.extra_state_attributes, {"valore_centrale": "On", "trigger": "On"})

    def test_allarme_in_corso_dalle_aree(self):
        ids = setup(status(plus(zone_alarm="[1]")))
        self.assertTrue(ids["D_area_alarm"].is_on)

    def test_linea_telefonica_disattivata_di_default(self):
        ids = setup(status(plus()))
        self.assertFalse(ids["D_anomaly_PSTNNoLine"]._attr_entity_registry_enabled_default)
        self.assertTrue(ids["D_anomaly_MainsACAbsent"]._attr_entity_registry_enabled_default)

    def test_originale_invariato(self):
        blocks = [{"sstype": "SS_Service_Saig", "elements": [{"sfetype": "SFE_State_Maintenance", "value": "On"}]},
                  {"sstype": "SS_Area", "idsf": 5, "name": "Casa", "elements": [{"sfetype": "SFE_State_Insert", "value": "Dis"}]}]
        ids = setup(status(blocks))
        self.assertEqual(sorted(ids), sorted(f"D_{d.key}" for d in bs.SYSTEM_SENSORS))
        self.assertTrue(ids["D_maintenance"].is_on)


class TestTraduzioni(unittest.TestCase):
    """Ogni chiave di traduzione usata dai sensori nuovi esiste in strings.json, en.json e it.json,
    con gli stessi segnaposto."""

    def test_chiavi_e_segnaposto(self):
        import json
        import re
        keys = {k: set() for k, _, _ in bs.ANOMALIES.values()}
        keys.update({"zone_masked": {"zone"}, "keyboard_tamper": {"device"}, "expansion_tamper": {"device"}})
        for f in ("strings.json", "translations/en.json", "translations/it.json"):
            d = json.load(open(os.path.join(PKG, f), encoding="utf-8"))["entity"]["binary_sensor"]
            for k, ph in keys.items():
                self.assertIn(k, d, f"{k} manca in {f}")
                self.assertEqual(set(re.findall(r"{(\w+)}", d[k]["name"])), ph, f"{k} in {f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
