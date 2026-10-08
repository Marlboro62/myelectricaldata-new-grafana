# Dashboards Grafana pour MyElectricalData v2

[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-d32f2f?logo=buymeacoffee&logoColor=white&style=flat)](https://buymeacoffee.com/marlboro62) [![Ko-fi](https://img.shields.io/badge/Ko--fi-ff5e5b?logo=kofi&logoColor=white&style=flat)](https://ko-fi.com/nothing_one)

Trois tableaux de bord Grafana qui lisent directement la base **PostgreSQL** de l'add-on Home Assistant [MyElectricalData v2](https://github.com/Marlboro62/hassio-addons/tree/master/myelectricaldata_v2) (consommation Linky journalière et à la demi-heure, couleurs Tempo, puissance max, grilles tarifaires).

## 🧩 Fait partie de l'écosystème MyElectricalData v2

Ces projets sont **non officiels**, maintenus par Marlboro62, sans lien avec l'équipe MyElectricalData. Ils s'appuient sur le [mode client de MyElectricalData v2](https://github.com/MyElectricalData/myelectricaldata_new).

| Projet | Rôle |
| --- | --- |
| [Add-on Home Assistant](https://github.com/Marlboro62/hassio-addons) | Installe le mode client v2 dans Home Assistant (interface web, synchro Linky/Tempo, PostgreSQL intégré) |
| [Script Proxmox (LXC)](https://github.com/Marlboro62/myelectricaldata-proxmox) | Déploie le mode client v2 dans un conteneur LXC Proxmox, sans Docker |
| [Carte Lovelace](https://github.com/Marlboro62/content-card-linky-v2) | Affiche conso, Tempo, coût et puissance max dans un tableau de bord Home Assistant |
| **Dashboards Grafana (ce dépôt)** | Analyse la base PostgreSQL de l'add-on (Linky, Tempo, coûts) |


| Fichier | Contenu | Origine |
|---|---|---|
| `dashboards/linky-tempo.json` | Tempo du jour et du lendemain, jours rouges/blancs restants, consommation par couleur, courbe de charge, puissance max, coût réel Tempo (année de facturation et période) | Création originale |
| `dashboards/my-electrical-data-v2.json` | Consommation et coût HC/HP, classe énergétique, comparaison Tempo / offre Base, bilans annuels et mensuels | Adapté du dashboard de **geobar78** |
| `dashboards/myelectricaldata-enedis-v2.json` | Consommation HC/HP, classe énergétique en énergie primaire, bilans sur 4 années, évolution à période égale | Adapté du dashboard de **HermesHonshappo** |

## Remerciements

Deux de ces dashboards sont des adaptations du travail d'autres membres de la communauté MyElectricalData.
Merci à eux, sans qui ces tableaux de bord n'existeraient pas :

- **geobar78** – [Myelectricaldata-Graphana-Dashboard](https://github.com/geobar78/Myelectricaldata-Graphana-Dashboard)
  (version d'origine pour InfluxDB 1.x / InfluxQL)
- **HermesHonshappo** – [MyElectricalData-dashboard](https://github.com/HermesHonshappo/MyElectricalData-dashboard)
  (version d'origine pour InfluxDB 2 / Flux)

La disposition, les styles et l'esprit des panneaux d'origine ont été conservés. Si vous utilisez encore
MyElectricalData v1 avec InfluxDB, utilisez directement leurs dépôts.

### Ce qui a été modifié dans les adaptations

- Source de données : InfluxDB remplacé par le PostgreSQL de MyElectricalData v2 (requêtes SQL).
- Prix : les tarifs saisis à la main sont remplacés par la table `energy_offers` de MyElectricalData,
  avec le tarif en vigueur à chaque date et la couleur Tempo de chaque jour.
- Plugins Angular retirés (`farski-blendstat-panel`, `blackmirror1-singlestat-math-panel`), incompatibles
  avec Grafana 11 et suivants : remplacés par des panneaux `stat` natifs.
- Années calculées automatiquement, évolutions comparées à période égale.
- Températures Home Assistant retirées (elles venaient d'un bucket InfluxDB que MyElectricalData v2 ne fournit pas).

## Prérequis

1. L'add-on **MyElectricalData v2** avec l'accès Grafana activé : définir `grafana_password` dans la
   configuration de l'add-on, puis renseigner le port `5432` dans l'onglet **Réseau**.
2. Grafana 11 ou plus récent.

## Installation

1. Dans Grafana : **Connections → Data sources → Add → PostgreSQL**
   - Host : `IP_DE_HOME_ASSISTANT:5432`
   - Database : `myelectricaldata_client`
   - User : `grafana_ro` / mot de passe : celui de `grafana_password`
   - TLS/SSL Mode : `disable`
2. **Dashboards → New → Import**, choisir un fichier JSON, sélectionner la source PostgreSQL, **Import**.
3. Ajuster les variables en haut du dashboard : puissance souscrite, surface du logement, début d'année de facturation…

## Bon à savoir

- Heures creuses : 22h-6h (Tempo). Un jour Tempo va de 6h à 6h.
- Les coûts sont calculés à la demi-heure : ils ne sont disponibles que sur la période couverte par la courbe de charge Enedis.
- Les coûts sont des estimations TTC à partir des grilles présentes dans MyElectricalData : si une ancienne grille manque,
  la plus proche est appliquée.
