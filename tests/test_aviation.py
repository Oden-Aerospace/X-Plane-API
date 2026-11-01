import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from xplane_api.aviation import AviationDataset, AviationManager, layer_metadata
from xplane_api.aviation.providers import create_manager, load_public, load_xplane


def point(identifier, longitude, latitude, layer="airports"):
    return {"type": "Feature", "id": identifier, "geometry": {
        "type": "Point", "coordinates": [longitude, latitude]},
        "properties": {"layer": layer, "ident": identifier, "name": identifier}}


class AviationDatasetTests(unittest.TestCase):
    def test_spatial_queries_nearest_and_dateline(self):
        dataset = AviationDataset("fixture", [
            point("near", -77, 38), point("far", -77, 38.5),
            point("west", 179.9, 0), point("east", -179.9, 0),
        ], {"airports": layer_metadata("synthetic", "test only")})
        result = dataset.query((-78, 37, -76, 39))
        self.assertEqual(len(result["features"]), 2)
        self.assertEqual(dataset.nearest(38.01, -77)[0]["id"], "near")
        self.assertEqual(len(dataset.query((179, -1, -179, 1))["features"]), 2)
        self.assertEqual(len(dataset.nearest(0, 180)), 2)
        self.assertEqual(dataset.detail("far")["properties"]["name"], "far")
        self.assertIsNone(dataset.detail("missing"))
        self.assertFalse(dataset.metadata()["layers"]["fixes"]["available"])

    def test_invalid_geometry_bounds_and_bounded_responses(self):
        with self.assertRaises(ValueError):
            AviationDataset("fixture", [point("bad", 181, 0)], {})
        dataset = AviationDataset("fixture", [point("one", 0, 0), point("two", 0, 0)], {})
        self.assertTrue(dataset.query((-1, -1, 1, 1), limit=1)["truncated"])
        with self.assertRaises(ValueError):
            dataset.query((0, 50, 0, -50))
        with self.assertRaises(ValueError):
            dataset.query((-1, -1, 1, 1), ("unknown",))

    def test_antimeridian_airspace_is_not_omitted_from_edge_queries(self):
        feature = {"type": "Feature", "id": "airspace:dateline", "geometry": {
            "type": "Polygon", "coordinates": [[[179,0],[-179,0],[-179,1],[179,0]]]},
            "properties": {"layer": "airspace"}}
        dataset = AviationDataset("fixture", [feature], {})
        self.assertEqual(len(dataset.query((179.5, 0, 180, 1))["features"]), 1)


class AviationManagerTests(unittest.TestCase):
    def test_registration_default_and_atomic_replacement(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "aviation.json"
            manager = AviationManager(config)
            manager.register("xplane", lambda options: AviationDataset("xplane", [point("local", 0, 0)], {}))
            manager.register("public", lambda options: AviationDataset("public", [point("open", 1, 1)], {}))
            config.write_text('{}')
            self.assertEqual(manager.reload()["active"]["provider"], "xplane")
            config.write_text('{"provider":"public"}')
            self.assertEqual(manager.reload()["active"]["provider"], "public")
            config.write_text('{"provider":"missing"}')
            status = manager.reload()
            self.assertEqual(status["requested_provider"], "missing")
            self.assertEqual(status["active"]["provider"], "public")
            self.assertIn("unknown", status["error"])
            config.write_text('broken')
            self.assertIsNotNone(manager.reload()["error"])
            self.assertEqual(manager.active().detail("open")["id"], "open")
            def failing_provider(options):
                raise RuntimeError("synthetic provider failure")
            manager.register("failing", failing_provider)
            config.write_text('{"provider":"failing"}')
            self.assertIn("synthetic", manager.reload()["error"])
            self.assertEqual(manager.active().provider, "public")
            with self.assertRaises(ValueError):
                manager.register("public", lambda options: None)

    def test_metadata_is_detached_from_callers(self):
        dataset = AviationDataset("fixture", [], {"airports": layer_metadata("synthetic")})
        metadata = dataset.metadata()
        metadata["layers"]["airports"]["available"] = False
        self.assertTrue(dataset.metadata()["layers"]["airports"]["available"])


class AviationProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def local_options(self):
        return {"xplane_path": str(self.root), "acknowledge_data_terms": True}

    def test_installed_legacy_airport_format_preserves_runway_geometry(self):
        for version in ("1100", "1130", "1200"):
            with self.subTest(version=version):
                self.write("Global Scenery/Global Airports/Earth nav data/apt.dat",
                    f'A\n{version} synthetic\n1 100 0 0 TEST Synthetic Airport\n'
                    '100 30 1 0 0.25 1 1 0 01 38 -77 0 0 0 0 0 0 19 38.01 -77 0 0 0 0 0 0\n')
                feature = load_xplane(self.local_options()).detail("airports:TEST")
                self.assertEqual(feature["properties"]["runways"][0]["identifiers"], ["01", "19"])
                self.assertAlmostEqual(feature["geometry"]["coordinates"][1], 38.005, places=5)
                self.assertIn("runway midpoint", feature["properties"]["position_source"])

    def test_xplane_airports_priority_cycles_and_airspace(self):
        airport = 'A\n1200 synthetic\n1 100 0 0 TEST Synthetic Airport\n1302 datum_lat 38\n1302 datum_lon -77\n1050 122800 CTAF\n'
        self.write("Global Scenery/Global Airports/Earth nav data/apt.dat", airport)
        self.write("Custom Scenery/Override/Earth nav data/apt.dat", airport.replace("Synthetic Airport", "Priority Airport"))
        self.write("Custom Scenery/scenery_packs.ini", 'SCENERY_PACK Custom Scenery/Override/\nSCENERY_PACK *GLOBAL_AIRPORTS*\n')
        header = 'I\n1200 Version - data cycle 2601 synthetic\n'
        self.write("Resources/default data/earth_nav.dat", header + '3 38 -77 100 11300 40 0 TST ENRT US Test VOR\n')
        self.write("Resources/default data/earth_fix.dat", header + '38 -77 FIX ENRT US 0\n')
        self.write("Resources/default data/airspaces/airspace.txt", 'AC D\nAN TEST CONTROL\nAL GND\nAH 2500 MSL\nDP 38:00:00 N 077:00:00 W\nDP 38:01:00 N 077:00:00 W\nDP 38:00:00 N 077:01:00 W\n')
        dataset = load_xplane(self.local_options())
        self.assertEqual(dataset.detail("airports:TEST")["properties"]["name"], "Priority Airport")
        self.assertEqual(dataset.detail("airports:TEST")["properties"]["frequencies"][0]["frequency_mhz"], 122.8)
        self.assertEqual(dataset.metadata()["layers"]["fixes"]["cycle"], "2601")
        self.assertEqual(dataset.detail("airspace:1")["properties"]["floor"], "GND")
        self.write("Resources/default data/earth_fix.dat", header.replace("2601", "2602"))
        with self.assertRaisesRegex(ValueError, "cycles"):
            load_xplane(self.local_options())

    def test_public_csv_geojson_and_failed_reload_retains_active(self):
        self.write("airports.csv", 'ident,name,latitude_deg,longitude_deg\nTEST,Synthetic,38,-77\n')
        self.write("runways.csv", 'airport_ident,le_ident,he_ident,length_ft,width_ft,surface\nTEST,01,19,1000,50,ASP\n')
        self.write("airspace.geojson", json.dumps({"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-77,38],[-77,39],[-78,38],[-77,38]]]},
            "properties": {"NAME": "Synthetic", "LOWER_DESC": "SFC", "UPPER_VAL": 2500, "UPPER_CODE": "MSL"}}]}))
        config = {"provider": "public", "acknowledge_data_terms": True, "files": {
            "airports": "airports.csv", "runways": "runways.csv", "airspace": "airspace.geojson"}}
        path = self.write("config.json", json.dumps(config))
        manager = create_manager(path)
        self.assertIsNone(manager.status()["error"])
        self.assertAlmostEqual(manager.active().detail("airports:TEST")["properties"]["runways"][0]["length_m"], 304.8)
        self.assertFalse(manager.active().metadata()["layers"]["fixes"]["available"])
        config["files"]["airports"] = "missing.csv"
        path.write_text(json.dumps(config))
        self.assertIsNotNone(manager.reload()["error"])
        self.assertEqual(manager.active().detail("airports:TEST")["properties"]["name"], "Synthetic")

    def test_defaults_terms_unknown_options_and_unsupported_overrides(self):
        manager = create_manager()
        self.assertEqual(manager.status()["requested_provider"], "xplane")
        self.assertIn("xplane_path", manager.status()["error"])
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            load_xplane({"xplane_path": str(self.root)})
        with self.assertRaisesRegex(ValueError, "unknown"):
            load_public({"acknowledge_data_terms": True, "typo": True})
        self.write("Custom Data/earth_424.dat", "synthetic override")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            load_xplane(self.local_options())

    def test_openair_circle_and_arc_and_expired_metadata(self):
        from xplane_api.aviation.providers import _airspace

        path = self.write("circle.txt", 'AC D\nAN Circle\nAL SFC\nAH 2500 MSL\nV X=38:00:00 N 077:00:00 W\nDC 3\n')
        features = list(_airspace(path))
        self.assertGreater(len(features[0]["geometry"]["coordinates"][0]), 60)
        path.write_text('AC D\nAN Arc\nV X=38:00:00 N 077:00:00 W\nDA 3,0,180\nDP 38:00:00 N 077:00:00 W\n')
        self.assertGreater(len(list(_airspace(path))[0]["geometry"]["coordinates"][0]), 30)
        dataset = AviationDataset("fixture", [], {"airspace": layer_metadata("synthetic", valid_until="2000-01-01")})
        self.assertIn("expired", dataset.metadata()["layers"]["airspace"]["currency"])

    def test_provider_parity_and_queries_do_not_reread_import_files(self):
        self.write("Global Scenery/Global Airports/Earth nav data/apt.dat", 'A\n1200 synthetic\n1 100 0 0 TEST Synthetic\n1302 datum_lat 38\n1302 datum_lon -77\n')
        local = load_xplane(self.local_options())
        path = self.write("airports.csv", 'ident,name,latitude_deg,longitude_deg,elevation_ft\nTEST,Synthetic,38,-77,100\n')
        public = load_public({"acknowledge_data_terms": True, "files": {"airports": str(path)}})
        path.unlink()
        local_feature = local.query((-78, 37, -76, 39))["features"][0]
        public_feature = public.query((-78, 37, -76, 39))["features"][0]
        self.assertEqual(local_feature["geometry"], public_feature["geometry"])
        self.assertEqual(local.nearest(38, -77)[0]["distance_nm"], public.nearest(38, -77)[0]["distance_nm"])

    def test_public_navaids_fixes_and_malformed_geojson(self):
        self.write("airports.csv", 'ident,name,latitude_deg,longitude_deg\nTEST,Synthetic,38,-77\n')
        self.write("navaids.csv", 'id,ident,latitude_deg,longitude_deg,name,type,frequency_khz\n1,TST,38,-77,Synthetic,VOR,113000\n')
        path = self.write("fixes.geojson", json.dumps({"type":"FeatureCollection","features":[{
            "type":"Feature","geometry":{"type":"Point","coordinates":[-77,38]},"properties":{"IDENT":"TESTFIX"}}]}))
        options = {"base_path": self.root, "acknowledge_data_terms": True,
                   "files": {"airports":"airports.csv","navaids":"navaids.csv","fixes":"fixes.geojson"}}
        dataset = load_public(options)
        self.assertEqual(dataset.metadata()["layers"]["navaids"]["count"], 1)
        self.assertEqual(dataset.metadata()["layers"]["fixes"]["count"], 1)
        path.write_text('[]')
        with self.assertRaisesRegex(ValueError, "FeatureCollection"):
            load_public(options)


if __name__ == "__main__":
    unittest.main()