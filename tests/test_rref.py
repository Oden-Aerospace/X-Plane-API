import struct
import unittest

from xplane_api.protocols.rref import (
    DatarefValue,
    decode_dataref_values,
    encode_subscription_request,
)


class EncodeSubscriptionRequestTests(unittest.TestCase):
    def test_encodes_subscription_fields_and_fixed_width_path(self) -> None:
        path = b"sim/flightmodel/position/latitude"
        packet = encode_subscription_request(
            path.decode("ascii"), 20, 7
        )

        header, frequency, index, dataref = struct.unpack(
            "<4sxii400s", packet
        )
        self.assertEqual(header, b"RREF")
        self.assertEqual(frequency, 20)
        self.assertEqual(index, 7)
        self.assertEqual(dataref, path + bytes(400 - len(path)))
        self.assertEqual(len(packet), 413)

    def test_zero_frequency_encodes_unsubscription(self) -> None:
        packet = encode_subscription_request("sim/test/value", 0, 1)
        _, frequency, _, _ = struct.unpack("<4sxii400s", packet)
        self.assertEqual(frequency, 0)

    def test_rejects_invalid_path(self) -> None:
        for path in ("", "bad\x00path"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                encode_subscription_request(path, 1, 0)

    def test_rejects_overlong_utf8_path(self) -> None:
        with self.assertRaises(ValueError):
            encode_subscription_request("x" * 400, 1, 0)

    def test_rejects_invalid_frequency_and_index(self) -> None:
        invalid_requests = (
            (True, 0),
            (-1, 0),
            (2**31, 0),
            (1.5, 0),
            (1, True),
            (1, 2**31),
            (1, -(2**31) - 1),
        )
        for frequency, index in invalid_requests:
            with self.subTest(frequency=frequency, index=index), self.assertRaises(
                ValueError
            ):
                encode_subscription_request("sim/test/value", frequency, index)


class DecodeDatarefValuesTests(unittest.TestCase):
    def test_decodes_one_value(self) -> None:
        packet = b"RREF\x00" + struct.pack("<if", 4, 12.5)
        self.assertEqual(
            decode_dataref_values(packet), (DatarefValue(index=4, value=12.5),)
        )

    def test_decodes_comma_terminated_xplane_12_header(self) -> None:
        packet = b"RREF," + struct.pack("<if", 4, 12.5)
        self.assertEqual(
            decode_dataref_values(packet), (DatarefValue(index=4, value=12.5),)
        )

    def test_decodes_multiple_values_in_packet_order(self) -> None:
        packet = b"RREF\x00" + struct.pack(
            "<ifif", 4, 12.5, 8, -3.0
        )
        self.assertEqual(
            decode_dataref_values(packet),
            (
                DatarefValue(index=4, value=12.5),
                DatarefValue(index=8, value=-3.0),
            ),
        )

    def test_rejects_malformed_packets(self) -> None:
        malformed_packets = (
            b"",
            b"RREF",
            b"NOPE\x00" + struct.pack("<if", 0, 1.0),
            b"RREF\x01" + struct.pack("<if", 0, 1.0),
            b"RREF;" + struct.pack("<if", 0, 1.0),
            b"RREF\x00",
            b"RREF\x00\x00",
        )
        for packet in malformed_packets:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                decode_dataref_values(packet)


if __name__ == "__main__":
    unittest.main()
