# Guide d'adaptation PPA — Version France 🇫🇷

## Vue d'ensemble

Ce dossier contient tout le nécessaire pour adapter le modèle PPA coréen (SK/Samsung)
au contexte d'un industriel français cherchant à optimiser son approvisionnement
en électricité renouvelable via des PPAs.

---

## Structure des fichiers à créer/modifier

```
votre_projet/
├── FranceGridUtils.py              ← NOUVEAU (remplace KEPCOutils.py)
├── download_france_data.py         ← NOUVEAU (télécharge les données)
├── ppamodule_france_adaptations.py ← DOCUMENTATION des modifications
│
├── database/                        ← À créer avec download_france_data.py
│   ├── grid_france.csv              ← Remplace grid.csv
│   ├── KEPCO_france.xlsx            ← Remplace KEPCO.xlsx
│   ├── wind_grid_france.xlsx        ← Remplace wind_grid.xlsx
│   ├── solar_patterns.db            ← Téléchargé depuis PVGIS
│   ├── wind_patterns.db             ← Téléchargé depuis Open-Meteo
│   ├── load_patterns.db             ← Profil industriel français
│   └── NGFS_carbonprice.xlsx        ← INCHANGÉ (NGFS est global)
│
└── gisdata/                         ← Données géospatiales (voir ci-dessous)
    ├── clusterpolygon.gpkg          ← Site(s) de l'industriel
    └── db_semippa.gpkg              ← Grille de terrains disponibles
```

---

## Étape 1 : Installer les dépendances

```bash
# Les mêmes que le projet original, plus :
pip install requests open-meteo pvlib
```

---

## Étape 2 : Télécharger les données automatiquement

```bash
python download_france_data.py
```

Ce script va créer toute la structure `database/` avec :
- Des données synthétiques (disponibles immédiatement)
- Des données réelles depuis PVGIS et Open-Meteo (si connexion internet disponible)

---

## Étape 3 : Remplacer les données par des données réelles (recommandé)

### 3.1 Prix EPEX Spot (énergie réseau)

**Source :** ODRE (Open Data Réseaux Énergies)
**URL :** https://odre.opendatasoft.com/explore/dataset/prix-spot-da-horaires/

```
1. Aller sur https://odre.opendatasoft.com
2. Rechercher "Prix spot Day-Ahead horaires France"
3. Télécharger toutes les années en CSV
4. Placer dans database/epex_spot_france.csv
```

Puis modifier `FranceGridUtils.py` :
```python
temporal_df, contract_fee = process_france_grid_data(
    year=2030,
    epex_filepath="database/epex_spot_france.csv",
    turpe_params=None  # utilise valeurs CRE 2024 par défaut
)
```

### 3.2 Intensité CO2 et mix énergétique (grid.csv)

**Source :** RTE eco2mix via ODRE
**URL :** https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/

```
1. Filtrer : toutes les années disponibles
2. Colonnes utiles : "Date - Heure", "Taux de CO2 (g/kWh)", "Taux d'EnR (%)"
3. Télécharger en CSV
4. Passer le chemin à build_france_grid_info(eco2mix_filepath="...")
```

### 3.3 TURPE HTB (tarif réseau)

**Source :** CRE (Commission de Régulation de l'Énergie)
**URL :** https://www.cre.fr/Electricite/Reseaux-d-electricite/Tarifs-d-acces

```
Valeurs TURPE 6 HTB 2 (en vigueur 2024) :
- Composante souscrite : 7.5 €/kW/an (HTB2 - 225kV)
- Composante HPH : 5.2 €/MWh
- Composante HCH : 1.8 €/MWh
- Composante HPE : 1.0 €/MWh
- Composante HCE : 0.5 €/MWh

⚠️  Ces tarifs sont révisés chaque année par délibération CRE.
    Vérifiez toujours la version en vigueur sur cre.fr.
```

### 3.4 Prix des Garanties d'Origine (= RECs coréens)

**Source :** EEX Environmental Markets
**URL :** https://www.eex.com/en/market-data/environmental-markets/go-market

```
Prix 2024 : ~5-15 €/MWh (variable selon source)
Dans scenario_defaults.xlsx : mettre "Initial REC Price (KRW/MWh)" = 10000
(avec currency_exchange = 1, ce sera 10 €/MWh)
```

### 3.5 Profils solaires (PVGIS)

**API directe (gratuite, sans inscription) :**
```python
# Exemple pour le site industriel de votre client
from download_france_data import download_pvgis_solar_profile

download_pvgis_solar_profile(
    lat=48.86,   # Latitude de votre site (ex: Paris)
    lon=2.35,    # Longitude
    year=2020,   # Année météo de référence
    output_db="database/solar_patterns.db"
)
```

### 3.6 Profils éoliens offshore

**Via Open-Meteo (gratuit, sans inscription) :**
```python
from download_france_data import download_wind_profile_openmeteo

# Télécharger pour chaque zone éolienne accessible depuis votre site
for region, lat, lon in [
    ("Hauts-de-France", 51.0, 2.5),
    ("Normandie", 49.8, 0.5),
]:
    download_wind_profile_openmeteo(lat, lon, year=2020,
                                     region_name=region)
```

**Pour des données ERA5 de qualité recherche :**
```
1. S'inscrire sur https://cds.climate.copernicus.eu
2. Installer cdsapi : pip install cdsapi
3. Télécharger wind_speed_100m pour les coordonnées offshore françaises
```

---

## Étape 4 : Données GIS (gisdata/) — La partie la plus complexe

Le projet coréen utilisait des données GIS propriétaires sur les terrains disponibles
autour des sites SK/Samsung. Pour la France, vous devez reconstituer ces données.

### 4.1 Site de l'industriel (clusterpolygon.gpkg)

C'est le polygone du/des site(s) de votre client industriel.

**Comment l'obtenir :**
```
Option A : Dessiner manuellement dans QGIS (gratuit)
   - Installer QGIS : https://www.qgis.org
   - Créer une couche vecteur polygone
   - Dessiner les limites du site
   - Exporter en GeoPackage (.gpkg)

Option B : Données cadastrales IGN
   - https://geoservices.ign.fr/parcellaire-express
   - Rechercher la parcelle cadastrale du site
   - Exporter en .gpkg
```

### 4.2 Grille de terrains disponibles (db_semippa.gpkg)

C'est la couche la plus complexe — elle contient tous les terrains potentiels
pour installer des panneaux solaires, avec leurs caractéristiques.

**Sources françaises :**

```
A. RPG (Registre Parcellaire Graphique) — Terrains agricoles
   URL : https://geoservices.ign.fr/rpg
   → Contient les parcelles agricoles (potentiel agrivoltaïque)
   → Colonnes utiles : surface, type culture, code commune

B. BDTopo IGN — Zonage et occupation du sol
   URL : https://geoservices.ign.fr/bdtopo
   → Bâtiments, routes, cours d'eau (à exclure)
   → Données sur les zones industrielles

C. CORINE Land Cover — Occupation du sol
   URL : https://www.statistiques.developpement-durable.gouv.fr/corine-land-cover
   → Classes d'occupation du sol à 1:100000
   → Identifier les zones compatibles ENR

D. PLU/PLUi — Plan Local d'Urbanisme
   URL : https://www.geoportail-urbanisme.gouv.fr
   → Zonage réglementaire (zones A/N = agricole/naturel)
   → Contraintes de constructibilité
```

**Script de création simplifié :**
```python
import geopandas as gpd
import requests

# Exemple : Télécharger RPG pour un département
def download_rpg(departement: str = "59", year: int = 2023):
    '''Télécharge le Registre Parcellaire Graphique depuis l'IGN.'''
    url = f"https://data.geopf.fr/telechargement/download/RPG/RPG_{year}-01-01/RPG_{year}-01-01_SHP_LAMB93_D0{departement}_2024-09-16.7z"
    # Note : Les URLs IGN changent selon les millésimes.
    # Consulter https://geoservices.ign.fr/rpg pour l'URL exacte.
    print(f"Télécharger manuellement depuis : {url}")
    print("Puis convertir en GeoPackage avec QGIS ou ogr2ogr.")

# Colonnes minimales requises par costutils.py :
# - geometry   : polygone de la parcelle
# - area_photo : surface disponible pour PV standard (m²)
# - area_agrivol : surface disponible pour agrivoltaïque (m²)
# - wavgprice  : prix moyen du terrain (€/m²)
# - distance_0 : distance au site industriel (m) — calculée par process_grid_site_data()
```

---

## Étape 5 : Modifier ppamodule.py

Voir `ppamodule_france_adaptations.py` pour la liste complète des modifications.

**Modifications critiques (minimum requis) :**

```python
# Ligne ~50 dans run_model() :
# AVANT :
gridinf_df = pd.read_csv("database/grid.csv", index_col=0)
# APRÈS :
gridinf_df = pd.read_csv("database/grid_france.csv", index_col=0)

# Ligne ~100 :
# AVANT :
wind_df = pd.read_excel('database/wind_grid.xlsx', index_col=0)
wind_df = wind_df[wind_df['admin_boundaries'].str.contains('인천광역시|경기도|충청남도')]
# APRÈS :
wind_df = pd.read_excel('database/wind_grid_france.xlsx', index_col=0)
wind_df = wind_df[wind_df['admin_boundaries'].str.contains(
    'Hauts-de-France|Normandie|Bretagne|Pays de la Loire|Occitanie'
)]

# Ligne ~120 :
# AVANT :
filepath = "database/KEPCO.xlsx"
# APRÈS :
filepath = "database/KEPCO_france.xlsx"
```

---

## Étape 6 : Modifier scenario_defaults.xlsx

| Paramètre | Valeur coréenne | → | Valeur française |
|---|---|---|---|
| Load for SK (MW) | 3000 | → | 100-400 |
| Load for Samsung (MW) | 3000 | → | 0 ou 2nd site |
| Currency Exchange Rate | 1400 | → | **1** |
| Selected Sheet | HV_C_III | → | **HTB3** |
| Initial SMP (KRW/MWh) | 167000 | → | **60000** (60 €/MWh) |
| Initial REC Price (KRW/MWh) | 80000 | → | **10000** (10 €/MWh) |
| Battery Capital Cost per MW | 1,000,000,000 | → | **600,000** (600 k€) |
| Battery Capital Cost per MWh | 250,000,000 | → | **150,000** (150 k€) |

---

## Correspondance des concepts Corée ↔ France

| Concept coréen | Équivalent français | Source données |
|---|---|---|
| KEPCO (fournisseur réseau) | EDF/fournisseur alternatif | CRE / contrat |
| Tarif HV_C (haute tension) | TURPE HTB (63/225/400 kV) | cre.fr |
| SMP (System Marginal Price) | Prix EPEX Spot Day-Ahead | odre.opendatasoft.com |
| REC (Renewable Energy Cert.) | GO (Garantie d'Origine) | eex.com |
| ETS coréen | EU ETS | prix-co2.fr / EEX |
| CO2 intensity 450 gCO2/kWh | CO2 intensity 55 gCO2/kWh | eco2mix RTE |
| Zone KEPCO (Corée) | Zone de desserte ENEDIS/RTE | data.enedis.fr |
| Land price (won/m²) | Prix terrain (€/m²) | DVF / notaires |

---

## Questions fréquentes

**Q : Pourquoi le CO2 français est-il si différent (55 vs 450 gCO2/kWh) ?**
R : La France produit ~70% de son électricité avec le nucléaire, qui est quasi-zéro carbone.
   Cela change fondamentalement la logique économique des PPAs : en France, l'argument
   est moins la décarbonation (déjà très bonne) que l'indépendance au prix de marché
   et l'amélioration de la compétitivité par rapport aux prix EPEX spot volatils.

**Q : Les RECs (GOs) étant si peu chères en France, ça change quoi ?**
R : Oui, significativement. En Corée, les RECs à 80 000 KRW/MWh (~57 €/MWh) représentaient
   un coût important qui justifiait des PPAs même plus chers. En France, les GOs à 8-15 €/MWh
   ont un impact beaucoup plus faible sur le calcul du LCOE renouvelable.
   La décision PPA se prend donc davantage sur la compétitivité prix pure.

**Q : Quid de l'ARENH ?**
R : L'ARENH (Accès Régulé à l'Énergie Nucléaire Historique) donnait aux industriels accès
   à l'électricité nucléaire d'EDF à prix régulé (42 €/MWh). Il a expiré fin 2025.
   Son successeur (CSPE réformée, contrats de long terme EDF) doit être pris en compte
   comme alternative aux PPAs renouvelables dans votre modèle.
   C'est l'équivalent d'un "KEPCO ultra-compétitif" qui concurrence les PPAs.
