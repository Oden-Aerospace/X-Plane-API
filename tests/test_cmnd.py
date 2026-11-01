import unittest

from xplane_api.protocols.cmnd import encode_command_request


class EncodeCommandRequestTests(unittest.TestCase):
    def test_encodes_header_path_and_terminator(self) -> None:
        packet = encode_command_request("sim/autopilot/heading")

        self.assertEqual(packet[:5], b"CMND\x00")
        self.assertEqual(packet[5:], b"sim/autopilot/heading\x00")

    def test_packet_length_tracks_path_length(self) -> None:
        packet = encode_command_request("sim/autopilot/NAV")
        self.assertEqual(len(packet), 5 + len("sim/autopilot/NAV") + 1)

    def test_rejects_empty_and_non_string_paths(self) -> None:
        for path in ("", None, 42):
            with self.subTest(path=path), self.assertRaises(ValueError):
                encode_command_request(path)  # type: ignore[arg-type]

    def test_rejects_embedded_nul(self) -> None:
        with self.assertRaises(ValueError):
            encode_command_request("sim/bad\x00path")

    def test_rejects_overlong_utf8_path(self) -> None:
        with self.assertRaises(ValueError):
            encode_command_request("x" * 400)

    def test_accepts_maximum_length_path(self) -> None:
        packet = encode_command_request("x" * 399)
        self.assertEqual(len(packet), 5 + 399 + 1)


if __name__ == "__main__":
    unittest.main()
