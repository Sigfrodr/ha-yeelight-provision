# Yeelight Provision

*[Version française](README.md)*

Home Assistant integration (HACS) that joins Yeelight / Xiaomi bulbs to your Wi-Fi **without the Yeelight or Xiaomi Home app and without a cloud account**, then adds them to the native Yeelight integration.

## How it works

1. A reset bulb broadcasts an open `yeelink-light-*` access point.
2. Home Assistant spots it with a Wi-Fi interface of the host (nl80211 scan) and offers the bulb under **Settings > Devices & services > Discovered**.
3. Once confirmed, it joins that access point and talks miIO (UDP 54321):
   - handshake: the bulb reveals its token while unpaired;
   - `miIO.info`;
   - `set_ps cfg_lan_ctrl 1`, which enables LAN control (port 55443);
   - `miIO.config_router`, which sends the SSID and password.
4. It releases the interface and waits for the bulb on the home network. It recognises the bulb by its miIO id in the Yeelight SSDP reply (port 1982), and adds it to the `yeelight` integration without any further click.

Control stays with the native Yeelight integration: this one only does the provisioning.

## Requirements

- **A Wi-Fi interface on the Home Assistant host, dedicated to this.** The integration brings it up, connects it and shuts it down itself. Do not pick the interface that connects the host to your network.
- Home Assistant in a container with `network_mode: host` and the `NET_ADMIN` and `NET_RAW` capabilities:

  ```yaml
  cap_add:
    - NET_ADMIN
    - NET_RAW
  ```

  In the linuxserver image, HA runs as a non-root user. At startup, its init script grants `cap_net_admin` and `cap_net_raw` to the Python binary, but only when the container has both capabilities. Side effect: the host Bluetooth becomes usable by HA as well.
- `pyroute2`, already shipped with Home Assistant.

During provisioning, the interface takes the static addresses `10.10.1.200/24` and `192.168.31.200/24`, without DHCP. A DHCP client would add a default route on the host. The addresses are removed afterwards.

Not tested on Home Assistant OS, where access to a Wi-Fi interface from the Core container is not guaranteed.

## Installation

- HACS: *Custom repositories*, add `https://github.com/Sigfrodr/ha-yeelight-provision` with the *Integration* type.
- Or manually: copy `custom_components/yeelight_provision` into your configuration's `custom_components/`.

Restart Home Assistant, then go to **Add integration > Yeelight Provision**:

1. pick the interface;
2. enter the 2.4 GHz network and its password;
3. set how often to search automatically.

Once the hub exists, running *Add integration > Yeelight Provision* again starts an immediate search and lets you pick the bulb. The "Search for bulbs in pairing mode" button runs the same search.

## Resetting a bulb

5 cycles of on 2 s / off 2 s, until it cycles colours. The bulb must be within range of the host's Wi-Fi interface.

## Limitations

- pyroute2's nl80211 module is experimental. Its `scan()` can hang forever, so the integration runs its own scan: trigger, wait 8 s, read the results.
- The token captured during provisioning is only logged at debug level. It often changes after association. Control does not need it: it goes through LAN control.

## Development

- `pytest`: miIO protocol against a local fake device, SSDP and nl80211 decoding.
- `tools/smoke_test.py`: hardware check inside the Home Assistant image, with `WIFI_IF=<interface>` (real scan and interface cleanup).

## License

MIT
