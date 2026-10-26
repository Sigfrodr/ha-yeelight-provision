"""Flux de configuration : le hub, puis un flux par ampoule a appairer."""

from __future__ import annotations

import asyncio
from ipaddress import ip_network
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BSSID,
    CONF_INTERFACE,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SSID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HUB_UNIQUE_ID,
    LAN_DISCOVERY_TIMEOUT,
    MIN_UNICAST_PREFIX,
    PAIRING_SSID_RE,
)
from .provision import ProvisionError, async_find_on_lan, async_provision
from .wifi import AccessPoint, WifiError, WifiRadio, list_wireless_interfaces

_LOGGER = logging.getLogger(__name__)

YEELIGHT_DOMAIN = "yeelight"

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_INTERVAL_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=0, max=60, step=1, mode=NumberSelectorMode.BOX,
                         unit_of_measurement="min")
)


async def _async_lan_hosts(hass: HomeAssistant) -> list[str]:
    """Adresses des reseaux IPv4 de HA (jusqu'a /22), pour le SSDP unicast."""
    hosts: list[str] = []
    for adapter in await network.async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for ipv4 in adapter["ipv4"]:
            net = ip_network(f"{ipv4['address']}/{ipv4['network_prefix']}", strict=False)
            if net.prefixlen >= MIN_UNICAST_PREFIX:
                hosts.extend(str(h) for h in net.hosts() if str(h) != ipv4["address"])
    return hosts


class YeelightProvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Hub (une seule entree) et appairage des ampoules (sans entree)."""

    VERSION = 1

    def __init__(self) -> None:
        self._ap: AccessPoint | None = None
        self._name: str | None = None
        self._choices: dict[str, AccessPoint] = {}
        self._task: asyncio.Task[dict[str, str]] | None = None
        self._outcome: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Reglages du hub."""
        return YeelightProvisionOptionsFlow()

    def _hub(self) -> ConfigEntry | None:
        for entry in self._async_current_entries(include_ignore=False):
            if entry.unique_id == HUB_UNIQUE_ID:
                return entry
        return None

    # --- Hub ---------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Premier ajout : le hub. Ensuite : appairage manuel."""
        if self._hub() is not None:
            return await self.async_step_pick()

        interfaces = await self.hass.async_add_executor_job(list_wireless_interfaces)
        if not interfaces:
            return self.async_abort(reason="no_wireless_interface")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                async with WifiRadio(user_input[CONF_INTERFACE]).powered() as radio:
                    await radio.scan()
            except WifiError as err:
                errors["base"] = err.reason
            except TimeoutError:
                errors["base"] = "wifi_error"
            else:
                await self.async_set_unique_id(HUB_UNIQUE_ID)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Appairage Yeelight",
                    data={
                        CONF_INTERFACE: user_input[CONF_INTERFACE],
                        CONF_SSID: user_input[CONF_SSID],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                    options={
                        CONF_SCAN_INTERVAL: int(
                            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                        )
                    },
                )

        user_input = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INTERFACE, default=user_input.get(CONF_INTERFACE, interfaces[0])
                ): SelectSelector(SelectSelectorConfig(options=interfaces)),
                vol.Required(CONF_SSID, default=user_input.get(CONF_SSID, "")): str,
                vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _INTERVAL_SELECTOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # --- Ampoule ------------------------------------------------------------

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Appairage manuel : scan, puis choix du point d'acces."""
        hub = self._hub()
        assert hub is not None
        if user_input is not None:
            return await self._async_start_bulb(self._choices[user_input[CONF_BSSID]])

        if hub.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="hub_not_loaded")
        try:
            async with hub.runtime_data.radio.powered() as radio:
                aps = await radio.scan()
        except (WifiError, TimeoutError):
            return self.async_abort(reason="wifi_error")

        self._choices = {ap.bssid: ap for ap in aps if PAIRING_SSID_RE.match(ap.ssid)}
        if not self._choices:
            return self.async_abort(reason="no_bulbs_found")
        options = {
            bssid: f"{ap.ssid} ({ap.signal:.0f} dBm)" if ap.signal is not None else ap.ssid
            for bssid, ap in self._choices.items()
        }
        return self.async_show_form(
            step_id="pick", data_schema=vol.Schema({vol.Required(CONF_BSSID): vol.In(options)})
        )

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Ampoule en mode appairage vue par le scan periodique."""
        if self._hub() is None:
            return self.async_abort(reason="hub_missing")
        ap = AccessPoint(
            ssid=discovery_info["ssid"],
            bssid=discovery_info["bssid"],
            frequency=discovery_info.get("frequency", 0),
            signal=discovery_info.get("signal"),
        )
        return await self._async_start_bulb(ap)

    async def _async_start_bulb(self, ap: AccessPoint) -> ConfigFlowResult:
        await self.async_set_unique_id(ap.bssid)
        self._abort_if_unique_id_configured()
        self._ap = ap
        self.context["title_placeholders"] = {"name": ap.ssid}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirmation, avec un nom facultatif pour l'ampoule."""
        assert self._ap is not None
        hub = self._hub()
        if hub is None:
            return self.async_abort(reason="hub_missing")
        if user_input is not None:
            self._name = (user_input.get(CONF_NAME) or "").strip() or None
            return await self.async_step_provision()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional(CONF_NAME): str}),
            description_placeholders={
                "ap": self._ap.ssid,
                "ssid": hub.data[CONF_SSID],
            },
        )

    async def async_step_provision(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Appairage en tache de fond, affiche comme progression."""
        assert self._ap is not None
        hub = self._hub()
        if hub is None or hub.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="hub_not_loaded")
        if self._task is None:
            self._task = self.hass.async_create_task(self._async_run(hub))
        if not self._task.done():
            return self.async_show_progress(
                step_id="provision",
                progress_action="provision",
                progress_task=self._task,
                description_placeholders={"ap": self._ap.ssid, "ssid": hub.data[CONF_SSID]},
            )
        try:
            self._outcome = self._task.result()
        except ProvisionError as err:
            self._outcome = {"reason": err.reason}
        except WifiError as err:
            self._outcome = {"reason": err.reason}
        except Exception:
            _LOGGER.exception("Appairage de %s en echec", self._ap.ssid)
            self._outcome = {"reason": "unknown"}
        return self.async_show_progress_done(next_step_id="finish")

    async def _async_run(self, hub: ConfigEntry) -> dict[str, str]:
        assert self._ap is not None
        result = await async_provision(
            hub.runtime_data.radio,
            self._ap,
            hub.data[CONF_SSID],
            hub.data[CONF_PASSWORD],
            str(self.hass.config.time_zone),
        )
        _LOGGER.info(
            "Ampoule %s (%s, %s) appairee sur %s",
            result.device_id, result.model, result.mac, hub.data[CONF_SSID],
        )
        _LOGGER.debug("Token releve a l'appairage : %s", result.token)
        outcome = {
            "model": result.model or "?",
            "device_id": str(result.device_id),
            "ssid": hub.data[CONF_SSID],
        }

        host = await async_find_on_lan(
            result.device_id, LAN_DISCOVERY_TIMEOUT, await _async_lan_hosts(self.hass)
        )
        if host is None:
            return {**outcome, "reason": "provisioned_not_found"}
        outcome["host"] = host

        flow = await self.hass.config_entries.flow.async_init(
            YEELIGHT_DOMAIN, context={"source": SOURCE_USER}, data={CONF_HOST: host}
        )
        if flow["type"] is FlowResultType.CREATE_ENTRY:
            if self._name:
                self.hass.config_entries.async_update_entry(flow["result"], title=self._name)
            return {**outcome, "reason": "provisioned"}
        if flow["type"] is FlowResultType.ABORT and flow.get("reason") == "already_configured":
            return {**outcome, "reason": "provisioned_already_configured"}
        return {**outcome, "reason": "provisioned_not_added"}

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fin : pas d'entree pour l'ampoule, elle vit dans l'integration Yeelight."""
        outcome = dict(self._outcome)
        reason = outcome.pop("reason", "unknown")
        return self.async_abort(reason=reason, description_placeholders=outcome)


class YeelightProvisionOptionsFlow(OptionsFlow):
    """Reseau cible, mot de passe et frequence de recherche."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Formulaire unique."""
        entry = self.config_entry
        if user_input is not None:
            data = {**entry.data, CONF_SSID: user_input[CONF_SSID]}
            if user_input.get(CONF_PASSWORD):
                data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            self.hass.config_entries.async_update_entry(entry, data=data)
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SSID, default=entry.data[CONF_SSID]): str,
                vol.Optional(CONF_PASSWORD): _PASSWORD_SELECTOR,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _INTERVAL_SELECTOR,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
