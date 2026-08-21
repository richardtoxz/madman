from __future__ import annotations

import unittest
import uuid

from ipc_protocol import (
    IPCProtocolError,
    attach_request_id,
    decode_ready,
    encode_ready,
    split_request_id,
)


class IPCProtocolTests(unittest.TestCase):
    def test_request_id_round_trip(self):
        request_id = uuid.uuid4().hex
        message = attach_request_id("PING", request_id)
        self.assertEqual(split_request_id(message), ("PING", request_id))

    def test_invalid_metadata_is_preserved_as_payload(self):
        message = "NICKNAME:abc||REQ:not-valid"
        self.assertEqual(split_request_id(message), (message, None))

    def test_attach_rejects_invalid_request_id(self):
        with self.assertRaises(IPCProtocolError):
            attach_request_id("PING", "invalid")

    def test_ready_handshake_supports_colons_in_username(self):
        payload = encode_ready("fake:user", 123)
        self.assertEqual(decode_ready(payload), ("fake:user", 123))

    def test_ready_handshake_rejects_invalid_payload(self):
        with self.assertRaises(IPCProtocolError):
            decode_ready("not-json")
