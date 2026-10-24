"""Client miIO minimal (UDP 54321), suffisant pour l'appairage.

Un appareil non appaire renvoie son token en clair dans la reponse au
handshake : c'est ce qui permet de lui transmettre les identifiants Wi-Fi
sans compte Xiaomi.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MIIO_PORT = 54321

# 0x2131, longueur 32, puis 28 octets a 0xFF : le paquet "hello".
_HELLO = bytes.fromhex("21310020") + b"\xff" * 28


@dataclass
class MiioSession:
    """Resultat du handshake."""

    host: str
    device_id: int
    stamp: int
    received_at: float
    token: bytes

    @property
    def token_revealed(self) -> bool:
        """Un token tout a 0x00 ou 0xFF signifie : deja appaire, token cache."""
        return self.token not in (b"\x00" * 16, b"\xff" * 16)


class _Receiver(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.queue.put_nowait(data)


async def _exchange(host: str, packet: bytes, timeout: float) -> bytes | None:
    loop = asyncio.get_running_loop()
    try:
        transport, proto = await loop.create_datagram_endpoint(
            _Receiver, remote_addr=(host, MIIO_PORT)
        )
    except OSError:
        return None
    try:
        transport.sendto(packet)
        async with asyncio.timeout(timeout):
            return await proto.queue.get()
    except (TimeoutError, OSError):
        return None
    finally:
        transport.close()


def _cipher(token: bytes) -> Cipher:
    # AES-128-CBC, cle et IV derives du token par MD5.
    key = hashlib.md5(token).digest()
    iv = hashlib.md5(key + token).digest()
    return Cipher(algorithms.AES(key), modes.CBC(iv))


def _encrypt(token: bytes, plain: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain) + padder.finalize()
    enc = _cipher(token).encryptor()
    return enc.update(data) + enc.finalize()


def _decrypt(token: bytes, data: bytes) -> bytes:
    dec = _cipher(token).decryptor()
    plain = dec.update(data) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(plain) + unpadder.finalize()


async def async_handshake(host: str, timeout: float = 3) -> MiioSession | None:
    """Envoie le hello miIO ; None si rien ne repond."""
    resp = await _exchange(host, _HELLO, timeout)
    if not resp or len(resp) < 32:
        return None
    return MiioSession(
        host=host,
        device_id=int.from_bytes(resp[8:12], "big"),
        stamp=int.from_bytes(resp[12:16], "big"),
        received_at=time.monotonic(),
        token=resp[16:32],
    )


async def async_command(
    session: MiioSession,
    method: str,
    params: Any = None,
    request_id: int = 1,
    timeout: float = 3,
) -> dict[str, Any] | None:
    """Envoie une commande chiffree ; None si pas de reponse."""
    body: dict[str, Any] = {"id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    payload = _encrypt(session.token, json.dumps(body, separators=(",", ":")).encode())

    # L'horodatage doit suivre celui de l'appareil, decale du temps ecoule.
    stamp = (session.stamp + int(time.monotonic() - session.received_at)) & 0xFFFFFFFF
    header = (
        b"\x21\x31"
        + (32 + len(payload)).to_bytes(2, "big")
        + b"\x00" * 4
        + session.device_id.to_bytes(4, "big")
        + stamp.to_bytes(4, "big")
    )
    # La signature couvre l'en-tete, le token, puis la charge chiffree.
    checksum = hashlib.md5(header + session.token + payload).digest()

    resp = await _exchange(session.host, header + checksum + payload, timeout)
    if resp is None:
        return None
    if len(resp) <= 32:
        return {}
    try:
        plain = _decrypt(session.token, resp[32:]).rstrip(b"\x00")
        return json.loads(plain)
    except ValueError:
        return {}
