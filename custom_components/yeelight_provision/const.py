"""Constantes de l'appairage Yeelight."""

from __future__ import annotations

import re

DOMAIN = "yeelight_provision"

CONF_INTERFACE = "interface"
CONF_SSID = "ssid"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_NAME = "name"
CONF_BSSID = "bssid"

HUB_UNIQUE_ID = "hub"

# Minutes entre deux recherches d'ampoules en mode appairage ; 0 = jamais.
DEFAULT_SCAN_INTERVAL = 2

# Point d'acces diffuse par une ampoule en mode appairage,
# par exemple "yeelink-light-color4_mibtE339".
PAIRING_SSID_RE = re.compile(r"^(yeelink|yeelight)-", re.IGNORECASE)

# Passerelles connues des points d'acces Yeelight (selon le firmware), avec
# l'adresse que l'on prend sur ce sous-reseau. Pas de DHCP : un client DHCP
# poserait une route par defaut sur l'hote, qui est en network_mode host.
AP_SUBNETS: tuple[tuple[str, str, int], ...] = (
    ("10.10.1.1", "10.10.1.200", 24),
    ("192.168.31.1", "192.168.31.200", 24),
)

# Delai maximal pour voir l'ampoule sur le reseau domestique apres l'envoi des
# identifiants Wi-Fi.
LAN_DISCOVERY_TIMEOUT = 120
# SSDP unicast : au-dela d'un /22 (1022 adresses), on s'en tient au multicast.
MIN_UNICAST_PREFIX = 22
