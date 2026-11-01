import math
import struct
import unittest

from xplane_api.protocols.dref import encode_dataref_write


class EncodeDatarefWriteTests(unittest.TestCase):
    def test_encodes_header_value_and_padded_path(self) -> None:
        path = "sim/cockpit2/autopilot/altitude_dial_ft"
        packet = encode_dataref_write(path, 5500.0)

        header, value, path_field = struct.unpack("<4sxf500s", packet)
        self.assertEqual(header, b"DREF")
        self.assertEqual(value, 5500.0)
        encoded = path.encode("ascii")
        self.assertEqual(path_field[: len(encoded)], encoded)
        self.assertEqual(path_field[len(encoded)], 0)
        self.assertEqual(
            path_field[len(encoded) + 1 :],
            b" " * (500 - len(encoded) - 1),
        )

    def test_packet_is_exactly_509_bytes(self) -> None:
        packet = encode_dataref_write("sim/test/value", 1.0)
        self.assertEqual(len(packet), 509)

    def test_value_round_trips_as_float32(self) -> None:
        packet = encode_dataref_write("sim/test/value", 123.25)
        _, value, _ = struct.unpack("<4sxf500s", packet)
        self.assertEqual(value, 123.25)

    def test_accepts_integer_values(self) -> None:
        packet = encode_dataref_write("sim/test/value", 2000)
        _, value, _ = struct.unpack("<4sxf500s", packet)
        self.assertEqual(value, 2000.0)

    def test_rejects_empty_and_non_string_paths(self) -> None:
        for path in ("", None, 42):
            with self.subTest(path=path), self.assertRaises(ValueError):
                encode_dataref_write(path, 1.0)  # type: ignore[arg-type]

    def test_rejects_embedded_nul(self) -> None:
        with self.assertRaises(ValueError):
            encode_dataref_write("sim/bad\x00path", 1.0)

    def test_rejects_overlong_utf8_path(self) -> None:
        with self.assertRaises(ValueError):
            encode_dataref_write("x" * 500, 1.0)

    def test_accepts_maximum_length_path(self) -> None:
        packet = encode_dataref_write("x" * 499, 1.0)
        self.assertEqual(len(packet), 509)

    def test_rejects_non_finite_and_non_numeric_values(self) -> None:
        for value in (math.nan, math.inf, -math.inf, "5", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                encode_dataref_write("sim/test/value", value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
