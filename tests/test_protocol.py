"""miIO contre un faux appareil local, et decodage SSDP / nl80211."""

import asyncio
import hashlib
import json

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.yeelight_provision import miio
from custom_components.yeelight_provision.const import PAIRING_SSID_RE
from custom_components.yeelight_provision.provision import _parse_ssdp
from custom_components.yeelight_provision.wifi import _signal_dbm

TOKEN = bytes.fromhex("00112233445566778899aabbccddeeff")
DID = 0x11770F83


def _aes(token):
    key = hashlib.md5(token).digest()
    return Cipher(algorithms.AES(key), modes.CBC(hashlib.md5(key + token).digest()))


class FakeBulb(asyncio.DatagramProtocol):
    """Repond au hello avec son token, puis aux commandes chiffrees."""

    def __init__(self, token=TOKEN):
        self.token = token
        self.received = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data == bytes.fromhex("21310020") + b"\xff" * 28:
            reply = bytes.fromhex("21310020") + b"\0" * 4 + DID.to_bytes(4, "big")
            self.transport.sendto(reply + (1000).to_bytes(4, "big") + self.token, addr)
            return
        header, checksum, payload = data[:16], data[16:32], data[32:]
        assert int.from_bytes(header[2:4], "big") == len(data)
        assert int.from_bytes(header[8:12], "big") == DID
        assert hashlib.md5(header + TOKEN + payload).digest() == checksum
        dec = _aes(TOKEN).decryptor()
        unpadder = padding.PKCS7(128).unpadder()
        plain = dec.update(payload) + dec.finalize()
        req = json.loads(unpadder.update(plain) + unpadder.finalize())
        self.received.append(req)
        body = json.dumps({"id": req["id"], "result": ["ok"]}).encode()
        padder = padding.PKCS7(128).padder()
        enc = _aes(TOKEN).encryptor()
        out = enc.update(padder.update(body) + padder.finalize()) + enc.finalize()
        head = b"\x21\x31" + (32 + len(out)).to_bytes(2, "big") + header[4:16]
        self.transport.sendto(head + hashlib.md5(head + TOKEN + out).digest() + out, addr)


async def _with_bulb(bulb):
    return await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: bulb, local_addr=("127.0.0.1", miio.MIIO_PORT)
    )


async def test_handshake_and_commands():
    bulb = FakeBulb()
    transport, _ = await _with_bulb(bulb)
    try:
        session = await miio.async_handshake("127.0.0.1")
        assert session is not None
        assert session.device_id == DID
        assert session.token == TOKEN
        assert session.token_revealed

        reply = await miio.async_command(session, "set_ps", ["cfg_lan_ctrl", "1"], 2)
        assert reply == {"id": 2, "result": ["ok"]}

        params = {"ssid": "net", "passwd": "pw", "uid": 0, "tz": "Europe/Paris"}
        reply = await miio.async_command(session, "miIO.config_router", params, 3)
        assert reply["result"] == ["ok"]
        assert bulb.received[1] == {"id": 3, "method": "miIO.config_router", "params": params}
    finally:
        transport.close()


async def test_hidden_token():
    transport, _ = await _with_bulb(FakeBulb(token=b"\xff" * 16))
    try:
        session = await miio.async_handshake("127.0.0.1")
        assert session is not None
        assert not session.token_revealed
    finally:
        transport.close()


async def test_no_answer():
    assert await miio.async_handshake("127.0.0.1", timeout=0.5) is None


def test_parse_ssdp():
    headers = _parse_ssdp(
        b"HTTP/1.1 200 OK\r\nLocation: yeelight://192.0.2.7:55443\r\n"
        b"id: 0x0000000011770f83\r\nmodel: color4\r\n\r\n"
    )
    assert headers["location"] == "yeelight://192.0.2.7:55443"
    assert int(headers["id"], 16) == DID


def test_signal_dbm():
    assert _signal_dbm(-6400) == -64.0
    assert _signal_dbm({"SIGNAL_STRENGTH": {"VALUE": -6400}}) == -64.0
    assert _signal_dbm(None) is None


def test_pairing_ssid():
    assert PAIRING_SSID_RE.match("yeelink-light-color4_mibtE339")
    assert PAIRING_SSID_RE.match("yeelight-ceiling_miap1234")
    assert not PAIRING_SSID_RE.match("Lan Solo")
