import struct
import unittest

from xplane_api.sources.beacon import parse_beacon


class ParseBeaconTests(unittest.TestCase):
    def make_packet(
        self,
        *,
        major: int = 1,
        minor: int = 2,
        application_host_id: int = 1,
        version: int = 124311,
        role: int = 1,
        port: int = 49000,
        hostname: bytes = b"sim-host\x00",
        raknet_port: int = 49700,
    ) -> bytes:
        return (
            b"BECN\x00"
            + struct.pack(
                "<BBiiIH",
                major,
                minor,
                application_host_id,
                version,
                role,
                port,
            )
            + hostname
            + struct.pack("<H", raknet_port)
        )

    def test_parses_xplane_beacon_fields(self) -> None:
        beacon = parse_beacon(self.make_packet(), "192.168.0.104")
        self.assertEqual(beacon.host, "192.168.0.104")
        self.assertEqual(beacon.port, 49000)
        self.assertEqual(beacon.version, 124311)
        self.assertEqual(beacon.role, 1)
        self.assertEqual(beacon.hostname, "sim-host")
        self.assertEqual(beacon.raknet_port, 49700)

    def test_rejects_invalid_header_version_application_and_port(self) -> None:
        invalid_packets = (
            b"NOPE\x00" + self.make_packet()[5:],
            self.make_packet(major=2),
            self.make_packet(minor=1),
            self.make_packet(application_host_id=2),
            self.make_packet(port=0),
            b"BECN\x00",
        )
        for packet in invalid_packets:
            with self.subTest(packet=packet[:25]), self.assertRaises(ValueError):
                parse_beacon(packet, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
