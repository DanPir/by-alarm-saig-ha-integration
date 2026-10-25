"""Prove del parser della discovery Sai: blocchi Saig2 (By-alarm Plus) e compatibilita' con i blocchi originali.

I blocchi Saig2 riproducono quelli letti da una centrale By-alarm Plus reale (dati anonimizzati) (solo i campi di stato)."""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
API = os.path.join(HERE, "..", "custom_components", "byalarm_saig", "api.py")
spec = importlib.util.spec_from_file_location("byalarm_api", API)
api = importlib.util.module_from_spec(spec)
sys.modules["byalarm_api"] = api
spec.loader.exec_module(api)


def area(sstype, idsf, name, insert, zones, zone_alarm="[]"):
    return {"sstype": sstype, "idsf": idsf, "name": name, "elements": [
        {"sfetype": "SFE_State_AreaId", "value": str(idsf - 21000)},
        {"sfetype": "SFE_State_Insert", "value": insert},
        {"sfetype": "SFE_State_ZoneAlarm", "value": zone_alarm},
        {"sfetype": "SFE_State_ExitTimeBuzzer", "value": "False"},
        {"sfetype": "SFE_State_Zones", "value": zones},
        {"sfetype": "SFE_State_AreaStatus", "value": "[]"},
    ]}


PLUS = [
    {"sstype": "SS_SaiPlant_All_Saig2", "idsf": 20001, "name": "Impianto", "elements": []},
    area("SS_Area_Saig2", 21001, "Area A", "Tot", "[1,2,3,4,5]"),
    area("SS_Area_Saig2", 21002, "Area B", "Tot", "[6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24]"),
    area("SS_Area_Saig2", 21003, "Area C", "Dis", "[7,8,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24]"),
    {"sstype": "SS_Zone", "idsf": 22001, "name": "Zona 1", "elements": [{"sfetype": "SFE_State_ZoneId", "value": "1"}]},
]


def client():
    return api.ByAlarmClient(host="x", username="x", useruid="x", duid="x", device_password="x", source_uid="x", pin="x")


class TestParseSaig2(unittest.TestCase):
    def test_tre_aree_plus(self):
        s = client()._parse_sai_discovery(PLUS)
        self.assertEqual([a.name for a in s.areas], ["Area A", "Area B", "Area C"])
        self.assertEqual([a.idsf for a in s.areas], [21001, 21002, 21003])
        self.assertEqual([a.insert for a in s.areas], ["Tot", "Tot", "Dis"])
        self.assertEqual([a.armed for a in s.areas], [True, True, False])
        self.assertEqual(s.areas[0].zone_ids, [1, 2, 3, 4, 5])

    def test_area_in_allarme_se_ha_zone_in_allarme(self):
        blocks = [area("SS_Area_Saig2", 21001, "Area A", "Tot", "[1,2,3,4,5]", zone_alarm="[3]"),
                  area("SS_Area_Saig2", 21002, "Area B", "Tot", "[6,7]")]
        s = client()._parse_sai_discovery(blocks)
        self.assertTrue(s.areas[0].alarm)
        self.assertFalse(s.areas[1].alarm)
        self.assertEqual(s.areas[0].zone_alarms, [3])

    def test_zone_ancora_lette(self):
        s = client()._parse_sai_discovery(PLUS)
        self.assertEqual([z.zone_id for z in s.zones], [1])

    def test_compatibilita_blocchi_originali(self):
        blocks = [
            {"sstype": "SS_Service_Saig", "elements": [{"sfetype": "SFE_State_AreaAlarm", "value": "True"},
                                                        {"sfetype": "SFE_State_Maintenance", "value": "On"}]},
            {"sstype": "SS_Area", "idsf": 5, "name": "Casa", "elements": [
                {"sfetype": "SFE_State_Insert", "value": "Int"}, {"sfetype": "SFE_State_Status", "value": "Ok"}]},
        ]
        s = client()._parse_sai_discovery(blocks)
        self.assertTrue(s.area_alarm)
        self.assertTrue(s.maintenance)
        self.assertEqual(len(s.areas), 1)
        self.assertEqual((s.areas[0].name, s.areas[0].insert, s.areas[0].status), ("Casa", "Int", "Ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
