import socket
import struct
import unittest

from xplane_api.sources.rref_client import RrefClient


class RrefClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.settimeout(0.5)
        self.client = RrefClient(
            "127.0.0.1",
            self.server.getsockname()[1],
            bind_address="127.0.0.1",
            socket_timeout=0.01,
        )

    def tearDown(self) -> None:
        self.client.close()
        self.server.close()

    def test_subscribe_receive_snapshot_and_unsubscribe(self) -> None:
        index = self.client.subscribe("sim/test/value", 4)
        request, client_address = self.server.recvfrom(1024)
        header, frequency, sent_index, path = struct.unpack("<4sxii400s", request)
        self.assertEqual(header, b"RREF")
        self.assertEqual(frequency, 4)
        self.assertEqual(sent_index, index)
        self.assertTrue(path.startswith(b"sim/test/value\x00"))

        self.server.sendto(
            b"RREF," + struct.pack("<if", index, 42.25), client_address
        )
        received = self.client.poll(timeout=0.2)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].path, "sim/test/value")
        self.assertEqual(received[0].value, 42.25)
        self.assertIn("sim/test/value", self.client.latest_values)

        self.assertTrue(self.client.unsubscribe("sim/test/value"))
        request, _ = self.server.recvfrom(1024)
        _, frequency, sent_index, _ = struct.unpack("<4sxii400s", request)
        self.assertEqual(frequency, 0)
        self.assertEqual(sent_index, index)
        self.assertNotIn("sim/test/value", self.client.latest_values)
        self.assertFalse(self.client.unsubscribe("sim/test/value"))

    def test_timeout_returns_no_samples(self) -> None:
        self.assertEqual(self.client.poll(timeout=0.01), ())

    def test_nonblocking_poll_without_data_does_not_stop_later_receives(self) -> None:
        index = self.client.subscribe("sim/test/value", 4)
        self.server.recvfrom(1024)
        self.assertEqual(self.client.poll(timeout=0), ())
        self.server.sendto(
            b"RREF," + struct.pack("<if", index, 42.25), self.client.local_address
        )
        self.assertEqual(self.client.poll(timeout=0.2)[0].value, 42.25)

    def test_ignores_unknown_indices(self) -> None:
        self.server.sendto(
            b"RREF," + struct.pack("<if", 99, 123.0), self.client.local_address
        )
        self.assertEqual(self.client.poll(timeout=0.2), ())

    def test_close_unsubscribes_and_rejects_more_operations(self) -> None:
        self.client.subscribe("sim/test/value", 1)
        self.server.recvfrom(1024)
        self.client.close()
        request, _ = self.server.recvfrom(1024)
        _, frequency, _, _ = struct.unpack("<4sxii400s", request)
        self.assertEqual(frequency, 0)
        with self.assertRaises(RuntimeError):
            self.client.poll()

    def test_rejects_duplicate_subscription(self) -> None:
        self.client.subscribe("sim/test/value", 1)
        self.server.recvfrom(1024)
        with self.assertRaises(ValueError):
            self.client.subscribe("sim/test/value", 1)

    def test_resubscribe_all_reuses_index_and_frequency(self) -> None:
        index = self.client.subscribe("sim/test/value", 7)
        self.server.recvfrom(1024)
        self.assertEqual(self.client.resubscribe_all(), 1)
        request, _ = self.server.recvfrom(1024)
        _, frequency, sent_index, _ = struct.unpack("<4sxii400s", request)
        self.assertEqual(frequency, 7)
        self.assertEqual(sent_index, index)


if __name__ == "__main__":
    unittest.main()
