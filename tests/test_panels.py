"""Prove della logica dei pannelli multi-area, con controfigure minime delle classi di Home Assistant."""
import asyncio
import enum
import importlib.util
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "custom_components", "byalarm_saig")


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


# --- controfigure di homeassistant ---------------------------------------------------------
class AlarmControlPanelState(str, enum.Enum):
    DISARMED = "disarmed"; ARMED_AWAY = "armed_away"; ARMED_HOME = "armed_home"
    ARMED_NIGHT = "armed_night"; TRIGGERED = "triggered"; ARMING = "arming"; DISARMING = "disarming"


class HomeAssistantError(Exception):
    pass


class AlarmControlPanelEntityFeature(enum.IntFlag):
    ARM_HOME = 1; ARM_AWAY = 2; ARM_NIGHT = 4


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    @property
    def available(self):
        return True

    def async_write_ha_state(self):
        pass

    def _handle_coordinator_update(self):
        pass


class DataUpdateCoordinator:
    def __init__(self, *a, **k):
        pass

    def __class_getitem__(cls, item):
        return cls


for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
    _mod(name)
_mod("homeassistant.components.alarm_control_panel", AlarmControlPanelEntity=object,
     AlarmControlPanelEntityFeature=AlarmControlPanelEntityFeature, AlarmControlPanelState=AlarmControlPanelState)
_mod("homeassistant.config_entries", ConfigEntry=object)
_mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_mod("homeassistant.exceptions", ConfigEntryAuthFailed=Exception, ConfigEntryNotReady=Exception,
     HomeAssistantError=HomeAssistantError)
_mod("homeassistant.helpers.device_registry", DeviceInfo=dict)
_mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_mod("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity,
     DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=Exception)

# --- carico il pacchetto dell'integrazione ------------------------------------------------------
pkg = types.ModuleType("byalarm_saig"); pkg.__path__ = [PKG]; sys.modules["byalarm_saig"] = pkg


def _load(sub):
    spec = importlib.util.spec_from_file_location(f"byalarm_saig.{sub}", os.path.join(PKG, f"{sub}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"byalarm_saig.{sub}"] = m
    spec.loader.exec_module(m)
    return m


api = _load("api"); const = _load("const"); coord_mod = _load("coordinator"); acp = _load("alarm_control_panel")
S = AlarmControlPanelState


# modi ammessi come li manda il gateway a un codice utente con permessi di comando
MODES_DIS_TOT = ('[{"forcedAllowed":false,"inhibitZonesAllowed":false,"mode":"Dis","specialAllowed":false},'
                 '{"forcedAllowed":false,"inhibitZonesAllowed":false,"mode":"Tot","specialAllowed":true}]')


def area(idsf, name, insert, zones, zone_alarm="[]", modes="[]"):
    return {"sstype": "SS_Area_Saig2", "idsf": idsf, "name": name, "elements": [
        {"sfetype": "SFE_State_Insert", "value": insert}, {"sfetype": "SFE_State_Zones", "value": zones},
        {"sfetype": "SFE_State_ZoneAlarm", "value": zone_alarm},
        {"sfetype": "SFE_AvailableInsertMode", "value": modes}, {"sfetype": "SFE_Cmd_Insert", "value": ""}]}


def zone(idsf, zid, name, opened=False):
    return {"sstype": "SS_Zone", "idsf": idsf, "name": name, "elements": [
        {"sfetype": "SFE_State_ZoneId", "value": str(zid)}, {"sfetype": "SFE_State_Opened", "value": str(opened)}]}


def status(blocks):
    c = api.ByAlarmClient(host="x", username="x", useruid="x", duid="x", device_password="x", source_uid="x", pin="x")
    return c._parse_sai_discovery(blocks)


class FakeCoord:
    def __init__(self, data, area_idsf):
        self.data = data; self.area_idsf = area_idsf; self.calls = []

    async def async_arm(self, mode, area_idsf=None):
        self.calls.append(("arm", mode, area_idsf))

    async def async_disarm(self, area_idsf=None):
        self.calls.append(("disarm", area_idsf))


class FailingCoord(FakeCoord):
    async def async_arm(self, mode, area_idsf=None):
        raise api.ByAlarmError(11, "doaction")


class Entry:
    data = {const.CONF_DUID: "TESTDUID000001"}
    title = "Vimar By-alarm"
    entry_id = "e1"


def setup(data, first_idsf):
    coord = FakeCoord(data, first_idsf)
    hass = types.SimpleNamespace(data={const.DOMAIN: {"e1": coord}})
    out = []
    asyncio.run(acp.async_setup_entry(hass, Entry(), out.extend))
    return coord, out


PLUS = [area(21001, "Area A", "Tot", "[1,2]"), area(21002, "Area B", "Tot", "[3,4]"),
        area(21003, "Area C", "Dis", "[4]"),
        zone(22001, 1, "Zona 1", opened=True), zone(22003, 3, "Zona 3"), zone(22004, 4, "Zona 4", opened=True)]


class TestPanels(unittest.TestCase):
    def test_un_pannello_per_area_con_nomi_e_id(self):
        _, ents = setup(status(PLUS), 21001)
        self.assertEqual([e._attr_name for e in ents], ["Area A", "Area B", "Area C"])
        self.assertEqual(ents[0]._attr_unique_id, "TESTDUID000001_area_21001")  # uguale all'originale

    def test_stati(self):
        _, ents = setup(status(PLUS), 21001)
        self.assertEqual([e.alarm_state for e in ents], [S.ARMED_AWAY, S.ARMED_AWAY, S.DISARMED])

    def test_allarme_solo_nell_area_giusta(self):
        blocks = [area(21001, "Area A", "Tot", "[1,2]", zone_alarm="[1]"), area(21002, "Area B", "Tot", "[3,4]")]
        _, ents = setup(status(blocks), 21001)
        self.assertEqual([e.alarm_state for e in ents], [S.TRIGGERED, S.ARMED_AWAY])

    def test_zone_filtrate_per_area(self):
        _, ents = setup(status(PLUS), 21001)
        self.assertEqual(ents[0].extra_state_attributes["zone_aperte"], ["Zona 1"])
        self.assertEqual(ents[2].extra_state_attributes["zone_aperte"], ["Zona 4"])

    def test_plus_senza_permessi_sola_lettura(self):
        coord, ents = setup(status(PLUS), 21001)  # codice senza permessi: nessun modo ammesso
        self.assertEqual([int(e.supported_features) for e in ents], [0, 0, 0])
        with self.assertRaises(HomeAssistantError):
            asyncio.run(ents[1].async_alarm_arm_away())
        with self.assertRaises(HomeAssistantError):
            asyncio.run(ents[2].async_alarm_disarm())
        self.assertEqual(coord.calls, [])  # niente mandato al gateway

    def test_plus_con_permessi_comandi_all_area_giusta(self):
        blocks = [area(21001, "Area A", "Tot", "[1,2]", modes=MODES_DIS_TOT),
                  area(21002, "Area B", "Dis", "[3,4]", modes=MODES_DIS_TOT)]
        coord, ents = setup(status(blocks), 21001)
        F = AlarmControlPanelEntityFeature
        self.assertEqual([e.supported_features for e in ents], [F.ARM_AWAY, F.ARM_AWAY])
        asyncio.run(ents[1].async_alarm_arm_away())
        asyncio.run(ents[0].async_alarm_disarm())
        self.assertEqual(coord.calls, [("arm", "Tot", 21002), ("disarm", 21001)])
        with self.assertRaises(HomeAssistantError):  # nessun "Int" sulla Plus
            asyncio.run(ents[1].async_alarm_arm_home())
        self.assertEqual(len(coord.calls), 2)

    def test_stato_in_corso_fino_alla_conferma(self):
        blocks = [area(21001, "Area A", "Dis", "[1,2]", modes=MODES_DIS_TOT)]
        coord, ents = setup(status(blocks), 21001)
        asyncio.run(ents[0].async_alarm_arm_away())
        self.assertEqual(ents[0].alarm_state, S.ARMING)  # comando partito, centrale non ancora aggiornata
        ents[0]._handle_coordinator_update()  # lettura con lo stato vecchio: resta "in corso"
        self.assertEqual(ents[0].alarm_state, S.ARMING)
        coord.data = status([area(21001, "Area A", "Tot", "[1,2]", modes=MODES_DIS_TOT)])
        ents[0]._handle_coordinator_update()  # la centrale conferma
        self.assertEqual(ents[0].alarm_state, S.ARMED_AWAY)

    def test_in_corso_scade_se_la_centrale_non_conferma(self):
        blocks = [area(21001, "Area A", "Tot", "[1,2]", modes=MODES_DIS_TOT)]
        _, ents = setup(status(blocks), 21001)
        asyncio.run(ents[0].async_alarm_disarm())
        self.assertEqual(ents[0].alarm_state, S.DISARMING)
        ents[0]._pending = ("Dis", 0)  # scadenza passata
        ents[0]._handle_coordinator_update()
        self.assertEqual(ents[0].alarm_state, S.ARMED_AWAY)  # torna allo stato vero

    def test_comando_rifiutato_niente_in_corso(self):
        blocks = [area(21001, "Area A", "Dis", "[1,2]", modes=MODES_DIS_TOT)]
        data = status(blocks)
        coord = FailingCoord(data, 21001)
        hass = types.SimpleNamespace(data={const.DOMAIN: {"e1": coord}})
        ents = []
        asyncio.run(acp.async_setup_entry(hass, Entry(), ents.extend))
        with self.assertRaises(api.ByAlarmError):
            asyncio.run(ents[0].async_alarm_arm_away())
        self.assertEqual(ents[0].alarm_state, S.DISARMED)

    def test_area_unica_come_originale(self):
        blocks = [{"sstype": "SS_Area", "idsf": 5, "name": "Casa", "elements": [{"sfetype": "SFE_State_Insert", "value": "Int"}]},
                  {"sstype": "SS_Service_Saig", "elements": [{"sfetype": "SFE_State_AreaAlarm", "value": "False"}]}]
        _, ents = setup(status(blocks), 5)
        self.assertEqual(len(ents), 1)
        self.assertIsNone(ents[0]._attr_name)
        self.assertEqual(ents[0].alarm_state, S.ARMED_HOME)

    def test_saig_comandi_come_originale(self):
        blocks = [{"sstype": "SS_Area", "idsf": 5, "name": "Casa", "elements": [{"sfetype": "SFE_State_Insert", "value": "Dis"}]},
                  {"sstype": "SS_Service_Saig", "elements": [{"sfetype": "SFE_State_AreaAlarm", "value": "False"}]}]
        coord, ents = setup(status(blocks), 5)
        F = AlarmControlPanelEntityFeature
        self.assertEqual(ents[0].supported_features, F.ARM_HOME | F.ARM_AWAY | F.ARM_NIGHT)
        asyncio.run(ents[0].async_alarm_arm_away())
        asyncio.run(ents[0].async_alarm_disarm())
        self.assertEqual(coord.calls, [("arm", "Tot", 5), ("disarm", 5)])


class TestComandoSaig2(unittest.TestCase):
    def test_valore_come_app_view(self):
        c = api.ByAlarmClient(host="x", username="x", useruid="x", duid="x", device_password="x", source_uid="x", pin="x")
        sent = []

        async def fake(idsf, sfetype, value):
            sent.append((idsf, sfetype, value))
        c._do_area_action = fake
        asyncio.run(c.async_insert_saig2(21001, "Tot"))
        asyncio.run(c.async_insert_saig2(21001, "Dis"))
        self.assertEqual(sent, [
            (21001, "SFE_Cmd_Insert", '{"mode":"Tot","special":false,"forced":false,"inhibitZones":false}'),
            (21001, "SFE_Cmd_Insert", '{"mode":"Dis","special":false,"forced":false,"inhibitZones":false}')])

    def test_coordinatore_sceglie_il_comando(self):
        sent = []

        class Client:
            async def async_insert_saig2(self, idsf, mode):
                sent.append(("saig2", idsf, mode))

            async def async_arm(self, mode, idsf):
                sent.append(("arm", idsf, mode))

            async def async_disarm(self, idsf):
                sent.append(("disarm", idsf))

        started = []
        hass = types.SimpleNamespace(async_create_background_task=lambda coro, name: (started.append(name), coro.close()))
        for data in (status(PLUS), status([{"sstype": "SS_Area", "idsf": 5, "name": "Casa", "elements": []}])):
            co = coord_mod.ByAlarmCoordinator.__new__(coord_mod.ByAlarmCoordinator)
            co.client, co.area_idsf, co.data, co.hass = Client(), 5, data, hass
            asyncio.run(co.async_arm("Tot", 21001 if data.saig2 else None))
            asyncio.run(co.async_disarm(21001 if data.saig2 else None))
        self.assertEqual(sent, [("saig2", 21001, "Tot"), ("saig2", 21001, "Dis"), ("arm", 5, "Tot"), ("disarm", 5)])
        self.assertEqual(len(started), 4)  # dopo ogni comando parte la conferma con letture ravvicinate

    def test_conferma_si_ferma_appena_la_centrale_cambia(self):
        co = coord_mod.ByAlarmCoordinator.__new__(coord_mod.ByAlarmCoordinator)
        co.data = status([area(21001, "Area A", "Dis", "[1]")])
        letture = []

        async def refresh():
            letture.append(1)
            if len(letture) == 3:  # alla terza lettura la centrale e' inserita
                co.data = status([area(21001, "Area A", "Tot", "[1]")])
        co.async_refresh = refresh
        sleep = coord_mod.asyncio.sleep
        coord_mod.asyncio.sleep = lambda s: sleep(0)
        try:
            asyncio.run(co._async_confirm(21001, "Tot"))
        finally:
            coord_mod.asyncio.sleep = sleep
        self.assertEqual(len(letture), 3)


class TestRicollegamento(unittest.TestCase):
    """Dopo una disconnessione, un cambio di stato di un'area vale solo se una nuova lettura lo conferma."""

    def run_update(self, prima, letture):
        co = coord_mod.ByAlarmCoordinator.__new__(coord_mod.ByAlarmCoordinator)
        co.data = status([area(21001, "Area A", prima, "[1]")])
        co.last_update_success = False  # la lettura precedente era fallita
        fatte = []

        class Client:
            async def async_get_status(self):
                fatte.append(1)
                return status([area(21001, "Area A", letture[len(fatte) - 1], "[1]")])
        co.client = Client()
        sleep = coord_mod.asyncio.sleep
        coord_mod.asyncio.sleep = lambda s: sleep(0)
        try:
            res = asyncio.run(co._async_update_data())
        finally:
            coord_mod.asyncio.sleep = sleep
        return res.areas[0].insert, len(fatte)

    def test_lettura_sbagliata_scartata(self):
        self.assertEqual(self.run_update("Tot", ["Dis", "Tot", "Tot"]), ("Tot", 3))

    def test_cambio_vero_confermato(self):
        self.assertEqual(self.run_update("Tot", ["Dis", "Dis"]), ("Dis", 2))

    def test_nessun_cambio_nessuna_lettura_in_piu(self):
        self.assertEqual(self.run_update("Tot", ["Tot"]), ("Tot", 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
