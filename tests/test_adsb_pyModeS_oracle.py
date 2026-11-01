"""Optional third-party oracle tests; pyModeS is test-only and GPL-3.0."""

import unittest

from xplane_api.adsb.frame import ModeSFrame

try:
    from pyModeS import decode as decode_modes
except ImportError:  # optional adsb-test extra
    decode_modes = None


@unittest.skipIf(decode_modes is None, "install the optional adsb-test extra")
class PyModeSOracleTests(unittest.TestCase):
    def test_known_df17_vector_passes_external_crc_and_field_decode(self) -> None:
        prefix = int("8D406B902015A678D4D220", 16)
        frame = ModeSFrame(prefix)
        decoded = decode_modes(frame.hex)
        self.assertTrue(decoded["crc_valid"])
        self.assertEqual(decoded["df"], 17)
        self.assertEqual(decoded["icao"], "406B90")
        self.assertEqual(decoded["typecode"], 4)
        self.assertEqual(decoded["callsign"], "EZY85MH")


if __name__ == "__main__":
    unittest.main()
