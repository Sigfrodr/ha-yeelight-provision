"""Appairage d'une ampoule : miIO sur son point d'acces, puis retour sur le LAN."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import urlparse

from . import miio
from .const import AP_SUBNETS
from .wifi import AccessPoint, WifiRadio

_LOGGER = logging.getLogger(__name__)

SSDP_ADDR = ("239.255.255.250", 1982)
SSDP_SEARCH = (
    b"M-SEARCH * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1982\r\n"
    b'MAN: "ssdp:discover"\r\n'
    b"ST: wifi_bulb\r\n\r\n"
)


class ProvisionError(Exception):
    """Echec de l'appairage ; reason est une cle de traduction."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ProvisionResult:
    """Ce que l'ampoule a renvoye pendant l'appairage."""

    device_id: int
    token: str
    model: str | None
    firmware: str | None
    mac: str | None
    lan_control: bool


async def async_provision(
    radio: WifiRadio, ap: AccessPoint, ssid: str, password: str, timezone: str
) -> ProvisionResult:
    """Transmet les identifiants Wi-Fi a l'ampoule en mode appairage.

    Deroulement, comme Provision-Yeelight.ps1 :
      1. handshake miIO      -> identifiant de l'appareil + token
      2. miIO.info           -> modele et firmware
      3. set_ps              -> active le controle par reseau local (55443)
      4. miIO.config_router  -> SSID et mot de passe
    Apres l'etape 4 l'ampoule coupe son point d'acces : une absence de
    reponse n'est donc pas un echec.
    """
    async with radio.powered():
        await radio.connect(ap.ssid, ap.bssid)
        for _gateway, address, prefix in AP_SUBNETS:
            await radio.add_address(address, prefix)

        session: miio.MiioSession | None = None
        for _ in range(3):
            for gateway, _address, _prefix in AP_SUBNETS:
                if session := await miio.async_handshake(gateway):
                    break
            if session:
                break
            await asyncio.sleep(2)
        if session is None:
            raise ProvisionError("no_response")
        if not session.token_revealed:
            raise ProvisionError("token_hidden")
        _LOGGER.debug("Ampoule %s trouvee sur %s", session.device_id, session.host)

        info = (await miio.async_command(session, "miIO.info", None, 1) or {}).get(
            "result"
        ) or {}

        # A faire avant config_router : ensuite l'ampoule quitte son point d'acces.
        lan = await miio.async_command(session, "set_ps", ["cfg_lan_ctrl", "1"], 2)

        conf = await miio.async_command(
            session,
            "miIO.config_router",
            {"ssid": ssid, "passwd": password, "uid": 0, "tz": timezone},
            3,
        )
        if conf and "error" in conf:
            _LOGGER.warning("config_router refuse : %s", conf["error"])
            raise ProvisionError("router_refused")

    return ProvisionResult(
        device_id=session.device_id,
        token=session.token.hex(),
        model=info.get("model"),
        firmware=info.get("fw_ver"),
        mac=info.get("mac"),
        lan_control=bool(lan and lan.get("result")),
    )


class _SsdpReceiver(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.queue.put_nowait(data)


def _parse_ssdp(data: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in data.decode(errors="replace").split("\r\n")[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    return headers


async def async_find_on_lan(device_id: int, timeout: float) -> str | None:
    """Cherche l'ampoule en SSDP Yeelight ; renvoie son adresse IP.

    L'en-tete id (0x0000000011770f83) est l'identifiant miIO de l'appareil.
    """
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        _SsdpReceiver, local_addr=("0.0.0.0", 0)
    )
    try:
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            transport.sendto(SSDP_SEARCH, SSDP_ADDR)
            listen_until = min(deadline, loop.time() + 5)
            while (remaining := listen_until - loop.time()) > 0:
                try:
                    async with asyncio.timeout(remaining):
                        data = await proto.queue.get()
                except TimeoutError:
                    break
                headers = _parse_ssdp(data)
                try:
                    found_id = int(headers.get("id", ""), 16)
                except ValueError:
                    continue
                if found_id == device_id:
                    return urlparse(headers.get("location", "")).hostname
    finally:
        transport.close()
    return None
