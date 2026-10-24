"""Appairage des ampoules Yeelight depuis Home Assistant, sans application.

Le hub scanne periodiquement les points d'acces "yeelink-*" avec l'interface
Wi-Fi de l'hote et propose chaque ampoule en mode appairage dans les
integrations decouvertes. L'appairage lui transmet le reseau domestique, puis
l'ampoule est ajoutee a l'integration Yeelight native.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    CONF_INTERFACE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PAIRING_SSID_RE,
)
from .wifi import AccessPoint, WifiError, WifiRadio

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON]

# Premier scan differe, pour ne pas charger le demarrage de Home Assistant.
FIRST_SCAN_DELAY = 30


@dataclass
class ProvisionData:
    """Donnees d'execution du hub."""

    radio: WifiRadio
    pairing: list[AccessPoint] = field(default_factory=list)


type ProvisionConfigEntry = ConfigEntry[ProvisionData]


async def async_scan_for_bulbs(hass: HomeAssistant, entry: ProvisionConfigEntry) -> None:
    """Scanne et propose chaque ampoule en mode appairage."""
    data = entry.runtime_data
    if data.radio.lock.locked():
        # Un appairage ou un autre scan utilise l'interface.
        return
    try:
        async with data.radio.powered() as radio:
            aps = await radio.scan()
    except (WifiError, TimeoutError) as err:
        _LOGGER.warning("Recherche des ampoules impossible : %s", err)
        return

    data.pairing = [ap for ap in aps if PAIRING_SSID_RE.match(ap.ssid)]
    for ap in data.pairing:
        _LOGGER.debug("Ampoule en mode appairage : %s (%s)", ap.ssid, ap.bssid)
        discovery_flow.async_create_flow(
            hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={
                "ssid": ap.ssid,
                "bssid": ap.bssid,
                "frequency": ap.frequency,
                "signal": ap.signal,
            },
        )


async def async_setup_entry(hass: HomeAssistant, entry: ProvisionConfigEntry) -> bool:
    """Met en place le hub."""
    entry.runtime_data = ProvisionData(radio=WifiRadio(entry.data[CONF_INTERFACE]))

    @callback
    def _scheduled_scan(_now: object = None) -> None:
        entry.async_create_background_task(
            hass, async_scan_for_bulbs(hass, entry), f"{DOMAIN} scan"
        )

    minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    if minutes:
        entry.async_on_unload(async_call_later(hass, FIRST_SCAN_DELAY, _scheduled_scan))
        entry.async_on_unload(
            async_track_time_interval(hass, _scheduled_scan, timedelta(minutes=minutes))
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ProvisionConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ProvisionConfigEntry) -> bool:
    """Decharge le hub."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
