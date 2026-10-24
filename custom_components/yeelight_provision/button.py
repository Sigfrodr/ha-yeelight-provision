"""Bouton de recherche immediate des ampoules en mode appairage."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProvisionConfigEntry, async_scan_for_bulbs
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Ajoute le bouton."""
    async_add_entities([ScanButton(entry)])


class ScanButton(ButtonEntity):
    """Lance une recherche des ampoules en mode appairage."""

    _attr_has_entity_name = True
    _attr_translation_key = "scan"

    def __init__(self, entry: ProvisionConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_scan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Yeelight",
            model=f"Appairage ({entry.runtime_data.radio.ifname})",
        )

    async def async_press(self) -> None:
        """Scanne maintenant."""
        await async_scan_for_bulbs(self.hass, self._entry)
