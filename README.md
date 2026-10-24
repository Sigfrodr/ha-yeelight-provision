# Yeelight Provision

Intégration Home Assistant (installable par HACS) qui appaire les ampoules Yeelight / Xiaomi au réseau Wi-Fi **sans l'application Yeelight ni Xiaomi Home**, puis les ajoute à l'intégration Yeelight native.

Elle reprend `D:\Projects\Apps\yeelight\Provision-Yeelight.ps1` :

1. Une ampoule réinitialisée diffuse un point d'accès ouvert `yeelink-light-*`.
2. Home Assistant le détecte avec l'interface Wi-Fi de l'hôte (scan nl80211), et propose l'ampoule dans **Paramètres > Appareils et services > Découvert**.
3. Après confirmation, il rejoint ce point d'accès et dialogue en miIO (UDP 54321) : handshake (token en clair tant que l'ampoule n'est pas appairée), `miIO.info`, `set_ps cfg_lan_ctrl 1` (contrôle LAN, port 55443), puis `miIO.config_router` (SSID et mot de passe).
4. Il relâche l'interface, attend l'ampoule sur le réseau domestique (SSDP Yeelight, port 1982, identifiée par son id miIO), et l'ajoute à l'intégration `yeelight` sans clic supplémentaire.

Le pilotage reste celui de l'intégration Yeelight native : cette extension ne fait que l'appairage.

## Prérequis

- Une interface Wi-Fi sur l'hôte de Home Assistant, **dédiée** à cet usage. L'intégration l'allume, la connecte et l'éteint elle-même. Sur le NAS OMV, c'est la puce de la carte mère : `wlp4s0`.
- Home Assistant en `network_mode: host`, avec les capacités `NET_ADMIN` et `NET_RAW` :

  ```yaml
  cap_add:
    - NET_ADMIN
    - NET_RAW
  ```

  Avec l'image linuxserver, HA tourne sous l'utilisateur `abc`. Au démarrage, son script d'init donne `cap_net_admin` et `cap_net_raw` au binaire Python, mais seulement si le conteneur a ces deux capacités. Effet de bord : le Bluetooth devient aussi utilisable par HA.
- `pyroute2` (déjà présent dans Home Assistant).

L'interface reçoit les adresses fixes `10.10.1.200/24` et `192.168.31.200/24` le temps de l'appairage, sans DHCP. Un client DHCP poserait en effet une route par défaut sur l'hôte.

## Installation

- HACS : *Dépôts personnalisés*, type *Intégration*, avec l'URL du dépôt. HACS exige un dépôt GitHub public.
- Ou à la main : copier `custom_components/yeelight_provision` dans le `custom_components/` de la config, puis redémarrer.

Ensuite : **Ajouter une intégration > Yeelight Provision**, choisir l'interface, puis saisir le réseau 2,4 GHz et son mot de passe. Une fois le hub en place, relancer *Ajouter une intégration > Yeelight Provision* lance une recherche immédiate avec choix de l'ampoule. Le bouton « Rechercher les ampoules en mode appairage » fait la même recherche.

## Réinitialiser une ampoule

5 cycles allumé 2 s / éteint 2 s, jusqu'au cycle de couleurs. L'ampoule doit être à portée de la puce Wi-Fi de l'hôte.

## Limites

- Le module nl80211 de pyroute2 est expérimental. Son `scan()` peut bloquer indéfiniment, d'où un scan maison : déclenchement, attente de 8 s, lecture des résultats.
- Le token relevé à l'appairage n'apparaît dans le journal qu'au niveau debug. Il change souvent après l'association : il ne sert pas au pilotage, qui passe par le LAN Control.
