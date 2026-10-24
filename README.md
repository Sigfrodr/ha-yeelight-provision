# Yeelight Provision

*[English version](README.en.md)*

Intégration Home Assistant (HACS) qui appaire les ampoules Yeelight / Xiaomi au réseau Wi-Fi **sans l'application Yeelight ni Xiaomi Home, ni compte cloud**, puis les ajoute à l'intégration Yeelight native.

## Fonctionnement

1. Une ampoule réinitialisée diffuse un point d'accès ouvert `yeelink-light-*`.
2. Home Assistant le détecte avec une interface Wi-Fi de l'hôte (scan nl80211), et propose l'ampoule dans **Paramètres > Appareils et services > Découvert**.
3. Après confirmation, il rejoint ce point d'accès et dialogue avec l'ampoule en miIO (UDP 54321) :
   - handshake : l'ampoule révèle son token tant qu'elle n'est pas appairée ;
   - `miIO.info` ;
   - `set_ps cfg_lan_ctrl 1`, qui active le contrôle LAN (port 55443) ;
   - `miIO.config_router`, qui transmet le SSID et le mot de passe.
4. Il relâche l'interface, puis attend l'ampoule sur le réseau domestique. Il la reconnaît à son id miIO dans le SSDP Yeelight (port 1982), et l'ajoute à l'intégration `yeelight` sans clic supplémentaire.

Le pilotage reste celui de l'intégration Yeelight native : cette extension ne fait que l'appairage.

## Prérequis

- **Une interface Wi-Fi sur l'hôte de Home Assistant, dédiée à cet usage.** L'intégration l'allume, la connecte et l'éteint elle-même. Ne pas choisir l'interface qui relie l'hôte au réseau.
- Home Assistant en conteneur avec `network_mode: host`, et les capacités `NET_ADMIN` et `NET_RAW` :

  ```yaml
  cap_add:
    - NET_ADMIN
    - NET_RAW
  ```

  Avec l'image linuxserver, HA tourne sous un utilisateur non root. Au démarrage, son script d'init donne `cap_net_admin` et `cap_net_raw` au binaire Python, mais seulement si le conteneur a ces deux capacités. Effet de bord : le Bluetooth de l'hôte devient aussi utilisable par HA.
- `pyroute2`, déjà présent dans Home Assistant.

Pendant l'appairage, l'interface prend les adresses fixes `10.10.1.200/24` et `192.168.31.200/24`, sans DHCP. Un client DHCP poserait en effet une route par défaut sur l'hôte. Ces adresses sont retirées ensuite.

Non testé sous Home Assistant OS, où l'accès à une interface Wi-Fi depuis le conteneur Core n'est pas garanti.

## Installation

- HACS : *Dépôts personnalisés*, ajouter `https://github.com/Sigfrodr/ha-yeelight-provision` avec le type *Intégration*.
- Ou à la main : copier `custom_components/yeelight_provision` dans le `custom_components/` de la configuration.

Redémarrer Home Assistant, puis **Ajouter une intégration > Yeelight Provision** :

1. choisir l'interface ;
2. saisir le réseau 2,4 GHz et son mot de passe ;
3. régler la fréquence de recherche automatique.

Une fois le hub en place, relancer *Ajouter une intégration > Yeelight Provision* lance une recherche immédiate, avec choix de l'ampoule. Le bouton « Rechercher les ampoules en mode appairage » fait la même recherche.

## Réinitialiser une ampoule

5 cycles allumé 2 s / éteint 2 s, jusqu'au cycle de couleurs. L'ampoule doit être à portée de l'interface Wi-Fi de l'hôte.

## Limites

- Le module nl80211 de pyroute2 est expérimental. Son `scan()` peut bloquer indéfiniment, d'où un scan maison : déclenchement, attente de 8 s, lecture des résultats.
- Le token relevé à l'appairage n'apparaît dans le journal qu'au niveau debug. Il change souvent après l'association. Le pilotage n'en a pas besoin : il passe par le contrôle LAN.

## Développement

- `pytest` : protocole miIO contre un faux appareil local, décodage SSDP et nl80211.
- `tools/smoke_test.py` : essai sur le matériel, dans l'image Home Assistant, avec `WIFI_IF=<interface>` (scan réel et remise en état de l'interface).

## Licence

MIT
