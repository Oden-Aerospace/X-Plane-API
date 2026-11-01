import math
import unittest

from xplane_api.sources.control_client import XPlaneControlClient


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, packet: bytes, address: tuple[str, int]) -> int:
        self.sent.append((packet, address))
        return len(packet)

    def close(self) -> None:
        self.closed = True


class XPlaneControlClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.socket = FakeSocket()
        self.client = XPlaneControlClient(
            "127.0.0.1", 49000, sock=self.socket  # type: ignore[arg-type]
        )

    def test_send_command_transmits_cmnd_packet(self) -> None:
        self.client.send_command("sim/autopilot/heading")

        self.assertEqual(len(self.socket.sent), 1)
        packet, address = self.socket.sent[0]
        self.assertEqual(address, ("127.0.0.1", 49000))
        self.assertEqual(packet, b"CMND\x00sim/autopilot/heading\x00")

    def test_write_dataref_transmits_dref_packet(self) -> None:
        self.client.write_dataref(
            "sim/cockpit2/autopilot/altitude_dial_ft", 5500.0
        )

        self.assertEqual(len(self.socket.sent), 1)
        packet, _ = self.socket.sent[0]
        self.assertEqual(packet[:5], b"DREF\x00")
        self.assertEqual(len(packet), 509)

    def test_invalid_command_path_transmits_nothing(self) -> None:
        for path in ("", "bad\x00path", "x" * 400):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.client.send_command(path)
        self.assertEqual(self.socket.sent, [])

    def test_invalid_dataref_write_transmits_nothing(self) -> None:
        cases = [
            ("", 1.0),
            ("sim/ok/path", math.nan),
            ("sim/ok/path", math.inf),
            ("sim/ok/path", "5"),
            ("sim/ok/path", True),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                self.client.write_dataref(path, value)  # type: ignore[arg-type]
        self.assertEqual(self.socket.sent, [])

    def test_closed_client_refuses_sends(self) -> None:
        self.client.close()
        self.assertTrue(self.socket.closed)
        with self.assertRaises(RuntimeError):
            self.client.send_command("sim/autopilot/heading")
        with self.assertRaises(RuntimeError):
            self.client.write_dataref("sim/ok/path", 1.0)
        self.assertEqual(self.socket.sent, [])

    def test_context_manager_closes(self) -> None:
        fake = FakeSocket()
        with XPlaneControlClient("127.0.0.1", 49000, sock=fake) as client:  # type: ignore[arg-type]
            client.send_command("sim/autopilot/NAV")
        self.assertTrue(fake.closed)

    def test_rejects_invalid_endpoint(self) -> None:
        for host, port in (("", 49000), ("127.0.0.1", 0), ("127.0.0.1", True)):
            with self.subTest(host=host, port=port), self.assertRaises(ValueError):
                XPlaneControlClient(host, port)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
