import unittest

from xplane_api.adsb.frame import ModeSFrame, build_df17_frame, mode_s_crc, validate_icao_address
from xplane_api.adsb.identification import build_identification_frame, encode_callsign


class ModeSCrcTests(unittest.TestCase):
    def test_known_adsb_crc_vector(self) -> None:
        # The 88-bit prefix for the published DF17 example; parity is AA4BDA.
        prefix = int("8D406B902015A678D4D220", 16)
        self.assertEqual(mode_s_crc(prefix), int("AA4BDA", 16))

    def test_rejects_wrong_width(self) -> None:
        for invalid in (-1, 1 << 88, True, "12"):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                mode_s_crc(invalid)


class IcaoAddressTests(unittest.TestCase):
    def test_parses_explicit_address_forms(self) -> None:
        self.assertEqual(validate_icao_address("406B90"), 0x406B90)
        self.assertEqual(validate_icao_address("0x406b90"), 0x406B90)
        self.assertEqual(validate_icao_address(0x406B90), 0x406B90)

    def test_rejects_invalid_address(self) -> None:
        for invalid in ("C172", "12345", "GGGGGG", -1, 1 << 24, True, 1.0):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                validate_icao_address(invalid)


class FrameTests(unittest.TestCase):
    def test_builds_112_bit_frame_and_avr_text(self) -> None:
        prefix = int("8D406B902015A678D4D220", 16)
        frame = ModeSFrame(prefix)
        self.assertEqual(frame.hex, "8D406B902015A678D4D220AA4BDA")
        self.assertEqual(frame.avr, "*8D406B902015A678D4D220AA4BDA;")
        self.assertEqual(len(frame.raw), 14)

    def test_build_df17_preserves_explicit_address_and_payload(self) -> None:
        frame = build_df17_frame(icao_address="406B90", me_payload=0, capability=5)
        self.assertEqual(frame.hex[:2], "8D")
        self.assertEqual(frame.hex[2:8], "406B90")

    def test_requires_semantic_inputs_without_defaults(self) -> None:
        with self.assertRaises(TypeError):
            build_df17_frame(icao_address="406B90", me_payload=0)  # type: ignore[call-arg]
        with self.assertRaises(ValueError):
            build_df17_frame(icao_address="406B90", me_payload=1 << 56, capability=0)
        with self.assertRaises(ValueError):
            build_df17_frame(icao_address="406B90", me_payload=0, capability=8)


class IdentificationTests(unittest.TestCase):
    def test_callsign_validation_and_six_bit_encoding(self) -> None:
        encoded = encode_callsign("KLM1023")
        decoded = "".join(
            _decode_callsign_character((encoded >> (6 * shift)) & 0x3F)
            for shift in reversed(range(8))
        )
        self.assertEqual(decoded, "KLM1023 ")
        with self.assertRaises(ValueError):
            encode_callsign("N172SP-TEST")
        with self.assertRaises(ValueError):
            encode_callsign("N172SP-")

    def test_builds_known_identification_message(self) -> None:
        frame = build_identification_frame("KLM1023", "4840D6", capability=5)
        self.assertEqual(frame.hex, "8D4840D6202CC371C32CE0576098")

    def test_callsign_must_be_explicit(self) -> None:
        with self.assertRaises(ValueError):
            build_identification_frame("", "4840D6", capability=5)
        with self.assertRaises(TypeError):
            build_identification_frame(123, "4840D6", capability=5)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            build_identification_frame("KLM1023", "4840D6")  # type: ignore[call-arg]


def _decode_callsign_character(value: int) -> str:
    if 1 <= value <= 26:
        return chr(ord("A") + value - 1)
    if 48 <= value <= 57:
        return chr(value)
    if value == 32:
        return " "
    return "#"


if __name__ == "__main__":
    unittest.main()
