"""Pilotage de l'interface Wi-Fi de l'hote par nl80211 (pyroute2).

L'interface est reservee a l'appairage : on l'allume, on scanne ou on rejoint
le point d'acces ouvert de l'ampoule, puis on remet tout en etat (adresses
retirees, deconnexion, interface eteinte).

Le conteneur Home Assistant doit avoir les capacites NET_ADMIN et NET_RAW.

Le module IW de pyroute2 est experimental : son scan() attend la notification
de fin de scan sans delai maximal et peut bloquer indefiniment. On declenche
donc le scan, on attend, puis on lit les resultats.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import errno
import inspect
import logging
import os
from typing import Any

from pyroute2 import AsyncIPRoute
from pyroute2.iwutil import AsyncIW
from pyroute2.netlink.exceptions import NetlinkError
from pyroute2.netlink.nl80211 import NL80211_NAMES, nl80211cmd

_LOGGER = logging.getLogger(__name__)

SCAN_WAIT = 8
CONNECT_TIMEOUT = 20


class WifiError(Exception):
    """Echec sur l'interface Wi-Fi ; reason est une cle de traduction."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class AccessPoint:
    """Point d'acces vu au scan."""

    ssid: str
    bssid: str
    frequency: int
    signal: float | None


def list_wireless_interfaces() -> list[str]:
    """Interfaces Wi-Fi de l'hote (bloquant : lit /sys)."""
    base = "/sys/class/net"
    try:
        names = os.listdir(base)
    except OSError:
        return []
    return sorted(n for n in names if os.path.exists(f"{base}/{n}/phy80211"))


async def _collect(result: Any) -> list[Any]:
    # Selon la methode, pyroute2 renvoie une liste ou un iterateur asynchrone.
    if inspect.isawaitable(result):
        result = await result
    if hasattr(result, "__aiter__"):
        return [item async for item in result]
    return list(result or [])


def _signal_dbm(mbm: Any) -> float | None:
    # pyroute2 decode NL80211_BSS_SIGNAL_MBM tantot en entier, tantot en dict.
    while isinstance(mbm, dict):
        mbm = next((v for v in mbm.values() if isinstance(v, (int, dict))), None)
    return mbm / 100 if isinstance(mbm, int) else None


def _netlink_reason(err: NetlinkError) -> str:
    if err.code == errno.EPERM:
        return "no_net_admin"
    return "wifi_error"


class WifiRadio:
    """Interface Wi-Fi reservee a l'appairage."""

    def __init__(self, ifname: str) -> None:
        self.ifname = ifname
        self.lock = asyncio.Lock()
        self._ifindex: int | None = None
        self._iw: AsyncIW | None = None
        self._addresses: list[tuple[str, int]] = []

    @asynccontextmanager
    async def powered(self) -> AsyncIterator[WifiRadio]:
        """Allume l'interface le temps du bloc, puis remet tout en etat."""
        async with self.lock:
            try:
                await self._power(True)
                self._iw = AsyncIW()
                await self._iw.setup_endpoint()
                yield self
            except NetlinkError as err:
                raise WifiError(_netlink_reason(err), str(err)) from err
            finally:
                await self._cleanup()

    async def _power(self, up: bool) -> None:
        async with AsyncIPRoute() as ipr:
            if self._ifindex is None:
                found = await _collect(ipr.link_lookup(ifname=self.ifname))
                if not found:
                    raise WifiError("interface_not_found", self.ifname)
                self._ifindex = found[0]
            await ipr.link("set", index=self._ifindex, state="up" if up else "down")

    async def _cleanup(self) -> None:
        if self._iw is not None:
            with suppress(Exception):
                await self._request("NL80211_CMD_DISCONNECT", [])
            with suppress(Exception):
                closed = self._iw.close()
                if inspect.isawaitable(closed):
                    await closed
            self._iw = None
        with suppress(Exception):
            async with AsyncIPRoute() as ipr:
                for address, prefix in self._addresses:
                    with suppress(NetlinkError):
                        await ipr.addr(
                            "del", index=self._ifindex, address=address, prefixlen=prefix
                        )
        self._addresses.clear()
        with suppress(Exception):
            await self._power(False)

    async def _request(self, cmd: str, attrs: list[list[Any]]) -> Any:
        assert self._iw is not None
        msg = nl80211cmd()
        msg["cmd"] = NL80211_NAMES[cmd]
        msg["attrs"] = [["NL80211_ATTR_IFINDEX", self._ifindex], *attrs]
        async with asyncio.timeout(10):
            return await _collect(self._iw._do_request(msg))  # noqa: SLF001

    async def scan(self) -> list[AccessPoint]:
        """Scanne les points d'acces (a appeler dans powered())."""
        assert self._iw is not None
        for attempt in range(3):
            try:
                await self._request("NL80211_CMD_TRIGGER_SCAN", [])
                break
            except NetlinkError as err:
                # EBUSY : l'interface vient de s'allumer ou un scan tourne deja.
                if err.code != errno.EBUSY or attempt == 2:
                    raise
                await asyncio.sleep(2)
        await asyncio.sleep(SCAN_WAIT)

        msg = nl80211cmd()
        msg["cmd"] = NL80211_NAMES["NL80211_CMD_GET_SCAN"]
        msg["attrs"] = [["NL80211_ATTR_IFINDEX", self._ifindex]]
        async with asyncio.timeout(10):
            dump = await _collect(self._iw._do_dump(msg))  # noqa: SLF001

        found: dict[str, AccessPoint] = {}
        for item in dump:
            bss = item.get_attr("NL80211_ATTR_BSS")
            if bss is None:
                continue
            ies = bss.get_attr("NL80211_BSS_INFORMATION_ELEMENTS") or {}
            raw = ies.get("SSID") if isinstance(ies, dict) else None
            if not raw:
                continue
            ssid = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            bssid = str(bss.get_attr("NL80211_BSS_BSSID")).lower()
            found[bssid] = AccessPoint(
                ssid=ssid,
                bssid=bssid,
                frequency=int(bss.get_attr("NL80211_BSS_FREQUENCY") or 0),
                signal=_signal_dbm(bss.get_attr("NL80211_BSS_SIGNAL_MBM")),
            )
        return list(found.values())

    async def connect(self, ssid: str, bssid: str | None = None) -> None:
        """Rejoint un point d'acces ouvert (a appeler dans powered())."""
        attrs: list[list[Any]] = [["NL80211_ATTR_SSID", ssid]]
        if bssid:
            attrs.append(["NL80211_ATTR_MAC", bssid])
        await self._request("NL80211_CMD_CONNECT", attrs)

        async with AsyncIPRoute() as ipr:
            for _ in range(CONNECT_TIMEOUT * 2):
                links = await _collect(ipr.get_links(self._ifindex))
                if links and links[0].get_attr("IFLA_OPERSTATE") == "UP":
                    return
                await asyncio.sleep(0.5)
        raise WifiError("connect_failed", ssid)

    async def add_address(self, address: str, prefix: int) -> None:
        """Ajoute une adresse, retiree a la sortie de powered()."""
        async with AsyncIPRoute() as ipr:
            try:
                await ipr.addr("add", index=self._ifindex, address=address, prefixlen=prefix)
            except NetlinkError as err:
                if err.code != errno.EEXIST:
                    raise
        self._addresses.append((address, prefix))
