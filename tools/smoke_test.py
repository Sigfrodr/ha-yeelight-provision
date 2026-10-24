"""Essai de fumee sur le materiel, a lancer dans l'image Home Assistant (voir README).

- import des modules de l'integration ;
- echange miIO complet contre un faux appareil sur 127.0.0.1 ;
- scan Wi-Fi et remise en etat de l'interface (si WIFI_IF est defini) ;
- recherche SSDP d'une ampoule deja sur le reseau (si BULB_ID est defini).
"""

import asyncio
import hashlib
import json
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

from cryptography.hazmat.primitives import padding  # noqa: E402
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

import yeelight_provision  # noqa: E402,F401
from yeelight_provision import button, config_flow, miio  # noqa: E402,F401
from yeelight_provision.provision import async_find_on_lan  # noqa: E402
from yeelight_provision.wifi import WifiRadio, list_wireless_interfaces  # noqa: E402

TOKEN = bytes.fromhex("00112233445566778899aabbccddeeff")
DID = 0x11770F83


def aes(token):
    key = hashlib.md5(token).digest()
    return Cipher(algorithms.AES(key), modes.CBC(hashlib.md5(key + token).digest()))


class FakeBulb(asyncio.DatagramProtocol):
    def __init__(self):
        self.received = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data[:4] == bytes.fromhex("21310020") and data[4:] == b"\xff" * 28:
            reply = bytes.fromhex("21310020") + b"\0" * 4 + DID.to_bytes(4, "big")
            reply += (1000).to_bytes(4, "big") + TOKEN
            self.transport.sendto(reply, addr)
            return
        header, checksum, payload = data[:16], data[16:32], data[32:]
        assert int.from_bytes(header[2:4], "big") == len(data), "longueur"
        assert int.from_bytes(header[8:12], "big") == DID, "did"
        assert hashlib.md5(header + TOKEN + payload).digest() == checksum, "checksum"
        dec = aes(TOKEN).decryptor()
        plain = dec.update(payload) + dec.finalize()
        unp = padding.PKCS7(128).unpadder()
        req = json.loads(unp.update(plain) + unp.finalize())
        self.received.append(req)
        body = json.dumps({"id": req["id"], "result": ["ok"]}).encode()
        pad = padding.PKCS7(128).padder()
        enc = aes(TOKEN).encryptor()
        out = enc.update(pad.update(body) + pad.finalize()) + enc.finalize()
        rh = b"\x21\x31" + (32 + len(out)).to_bytes(2, "big") + header[4:16]
        self.transport.sendto(rh + hashlib.md5(rh + TOKEN + out).digest() + out, addr)


async def test_miio():
    loop = asyncio.get_running_loop()
    transport, bulb = await loop.create_datagram_endpoint(
        FakeBulb, local_addr=("127.0.0.1", miio.MIIO_PORT)
    )
    try:
        session = await miio.async_handshake("127.0.0.1")
        assert session and session.device_id == DID and session.token == TOKEN
        assert session.token_revealed
        r = await miio.async_command(session, "set_ps", ["cfg_lan_ctrl", "1"], 2)
        assert r == {"id": 2, "result": ["ok"]}, r
        r = await miio.async_command(
            session, "miIO.config_router", {"ssid": "x", "passwd": "y", "uid": 0, "tz": "Europe/Paris"}, 3
        )
        assert r["result"] == ["ok"]
        assert bulb.received[1]["params"]["tz"] == "Europe/Paris"
        assert await miio.async_handshake("127.0.0.2", timeout=1) is None
        print("miio OK", flush=True)
    finally:
        transport.close()


def iface_state(name):
    with open(f"/sys/class/net/{name}/operstate") as f:
        return f.read().strip()


async def test_wifi(ifname):
    print("interfaces", list_wireless_interfaces(), flush=True)
    radio = WifiRadio(ifname)
    async with radio.powered() as r:
        await r.add_address("10.10.1.200", 24)
        aps = await r.scan()
    for ap in aps:
        print(" ", ap, flush=True)
    os.system(f"ip -4 addr show dev {ifname}")
    print("operstate apres :", iface_state(ifname), flush=True)
    print("wifi OK", flush=True)


class FakeSsdp(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if b"wifi_bulb" in data:
            self.transport.sendto(
                b"HTTP/1.1 200 OK\r\nLocation: yeelight://192.0.2.7:55443\r\n"
                + f"id: 0x{DID:016x}\r\nmodel: color4\r\n\r\n".encode(),
                addr,
            )


async def test_ssdp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 1982))
    mreq = struct.pack("4sl", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        FakeSsdp, sock=sock
    )
    try:
        assert await async_find_on_lan(DID, 10) == "192.0.2.7"
        assert await async_find_on_lan(DID + 1, 6) is None
        print("ssdp OK", flush=True)
    finally:
        transport.close()


async def main():
    await test_miio()
    await test_ssdp()
    if ifname := os.environ.get("WIFI_IF"):
        await test_wifi(ifname)
    if bulb := os.environ.get("BULB_ID"):
        print("ssdp", await async_find_on_lan(int(bulb), 15), flush=True)


asyncio.run(main())
