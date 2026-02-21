"""
download_france_data.py
=======================
Scripts de téléchargement des données françaises pour alimenter le modèle PPA.

Ce fichier remplace le contenu du dossier /database/ du projet coréen.

À exécuter UNE FOIS pour préparer votre environnement :
    python download_france_data.py

Crée la structure de dossiers :
    database/
    ├── grid_france.csv          ← Remplace grid.csv
    ├── solar_patterns.db        ← Profils solaires PVGIS (SQLite)
    ├── wind_patterns.db         ← Profils éoliens ERA5 (SQLite)
    ├── load_patterns.db         ← Profil de charge industriel (SQLite)
    ├── KEPCO_france.xlsx        ← Remplace KEPCO.xlsx (tarifs TURPE/EPEX)
    └── NGFS_carbonprice.xlsx    ← Identique (NGFS est global — rien à changer)
"""

import pandas as pd
import numpy as np
import sqlite3
import requests
import json
import os
from pathlib import Path


def create_database_structure():
    """Crée les dossiers nécessaires."""
    Path("database").mkdir(exist_ok=True)
    Path("gisdata").mkdir(exist_ok=True)
    print("[OK] Structure de dossiers créée.")


# =============================================================
# 1. DONNÉES GRID (CO2, REN_SHARE, CAPEX)
#    Source : Projections RTE + ADEME
# =============================================================

def create_grid_france_csv():
    """
    Crée database/grid_france.csv avec les projections françaises.

    POUR DES DONNÉES RÉELLES :
    Télécharger eco2mix depuis ODRE :
    https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/
    → Filtrer toutes les années disponibles → Exporter CSV
    → Appeler ensuite load_eco2mix_into_grid() avec le fichier téléchargé
    """
    years = range(2023, 2051)

    data = {
        "year": list(years),
        # CAPEX solaire (€/MW) — Source : ADEME Futurs Énergétiques 2050
        "solar_capex": np.linspace(900_000, 500_000, len(years)),
        # CAPEX éolien offshore (€/MW) — Source : RTE Bilan Prévisionnel
        "wind_capex": np.linspace(3_000_000, 1_800_000, len(years)),
        # Intensité CO2 réseau (gCO2/kWh) — France très faible grâce au nucléaire
        # Source historique eco2mix : ~55 gCO2/kWh en 2023
        "co2": np.linspace(55.0, 20.0, len(years)),
        # Part ENR dans le mix (fraction, pas %) — Source : RTE scénario M23
        "ren_share": np.linspace(0.29, 0.80, len(years)),
    }

    df = pd.DataFrame(data).set_index("year")
    df.to_csv("database/grid_france.csv")
    print("[OK] database/grid_france.csv créé.")
    print(f"     CO2 initial (2023) : {df.loc[2023, 'co2']:.1f} gCO2/kWh")
    print(f"     CAPEX PV (2030)    : {df.loc[2030, 'solar_capex']/1000:.0f} k€/MW")
    return df


# =============================================================
# 2. PROFILS SOLAIRES PVGIS
#    Source : PVGIS JRC (API gratuite, pas d'inscription requise)
# =============================================================

def download_pvgis_solar_profile(lat: float, lon: float,
                                  year: int = 2020,
                                  output_db: str = "database/solar_patterns.db"):
    """
    Télécharge le profil solaire horaire depuis l'API PVGIS (JRC/Commission Européenne).

    PVGIS est gratuit et ne nécessite pas d'inscription.
    Documentation API : https://joint-research-centre.ec.europa.eu/pvgis-photovoltaic-geographical-information-system/getting-started-pvgis/api-non-interactive-service_en

    Paramètres :
    -----------
    lat, lon : float
        Coordonnées GPS du site industriel.
        Exemple Valenciennes (industrie nord) : lat=50.36, lon=3.52
        Exemple Fos-sur-Mer (pétrochimie) : lat=43.44, lon=4.95
        Exemple Dunkerque (industrie lourde) : lat=51.03, lon=2.38
    year : int
        Année météo de référence (PVGIS-SARAH2 couvre 2005-2020)
    """
    print(f"\n[PVGIS] Téléchargement profil solaire pour lat={lat}, lon={lon}, année={year}...")

    # Appel API PVGIS - données horaires
    url = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
    params = {
        "lat": lat,
        "lon": lon,
        "raddatabase": "PVGIS-SARAH2",
        "startyear": year,
        "endyear": year,
        "pvcalculation": 1,
        "peakpower": 1,          # 1 kWp installé → profil normalisé
        "loss": 14,              # Pertes système typiques (%)
        "pvtechchoice": "crystSi",
        "mountingplace": "free",
        "angle": 30,             # Inclinaison optimale pour la France
        "aspect": 0,             # Orientation sud
        "outputformat": "json",
        "browser": 0,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Extraction des données horaires
        hourly = data["outputs"]["hourly"]
        df = pd.DataFrame(hourly)
        df["time"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M")
        df = df.set_index("time")

        # P_kWh → profil normalisé entre 0 et 1 (capacity factor horaire)
        # "P" en PVGIS = puissance en W pour 1 kWp installé → diviser par 1000
        df["capacity_factor"] = df["P"] / 1000.0
        df["capacity_factor"] = df["capacity_factor"].clip(0, 1)

        # Sauvegarder dans SQLite (même format que solar_patterns.db original)
        conn = sqlite3.connect(output_db)
        # Renommer pour correspondre au format du code original
        df_out = df[["capacity_factor"]].copy()
        df_out.columns = ["q99"]
        df_out.index.name = "datetime"
        df_out["datetime"] = df_out.index.astype(str)
        df_out.to_sql("solar_patterns", conn, if_exists="replace", index=False)
        conn.close()

        print(f"[OK] {len(df_out)} heures sauvegardées dans {output_db}")
        print(f"     Facteur de charge annuel moyen : {df_out['q99'].mean():.3f}")
        return df_out

    except requests.exceptions.ConnectionError:
        print("[WARN] Connexion PVGIS impossible. Génération d'un profil synthétique.")
        return _generate_synthetic_solar_and_save(lat, year, output_db)
    except Exception as e:
        print(f"[WARN] Erreur PVGIS : {e}. Génération d'un profil synthétique.")
        return _generate_synthetic_solar_and_save(lat, year, output_db)


def _generate_synthetic_solar_and_save(lat: float, year: int, output_db: str):
    """Profil solaire synthétique quand PVGIS n'est pas accessible."""
    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
    hours = date_range.hour
    months = date_range.month
    day_of_year = date_range.dayofyear

    # Modèle simplifié : variation saisonnière + courbe journalière
    # Durée d'ensoleillement varie de ~9h (décembre) à ~16h (juin) à lat=47°N
    sunrise = 6 + 2 * np.cos(2 * np.pi * (day_of_year - 172) / 365)  # h
    sunset = 18 + 2 * np.sin(2 * np.pi * (day_of_year - 172) / 365)  # h
    solar_noon = (sunrise + sunset) / 2

    cf = np.zeros(len(date_range))
    daylight = (hours >= sunrise.astype(int)) & (hours <= sunset.astype(int))
    peak_factor = 0.85 * (1 + 0.15 * np.sin(2 * np.pi * (day_of_year - 80) / 365))
    gaussian = np.exp(-0.5 * ((hours - solar_noon) / 3) ** 2)
    cf = np.where(daylight, peak_factor * gaussian, 0)
    cf = np.clip(cf, 0, 1)

    df_out = pd.DataFrame({"datetime": date_range.astype(str), "q99": cf})

    conn = sqlite3.connect(output_db)
    df_out.to_sql("solar_patterns", conn, if_exists="replace", index=False)
    conn.close()

    print(f"[OK] Profil solaire synthétique créé : CF moyen = {cf.mean():.3f}")
    return df_out


# =============================================================
# 3. PROFILS ÉOLIENS
#    Source : ERA5 via API CDS Copernicus (gratuit, inscription requise)
#    Alternative : open-meteo.com (sans inscription)
# =============================================================

def download_wind_profile_openmeteo(lat: float, lon: float,
                                     year: int = 2020,
                                     region_name: str = "Nord",
                                     output_db: str = "database/wind_patterns.db"):
    """
    Télécharge le profil de vent depuis Open-Meteo (gratuit, sans inscription).

    Alternative sans API key à ERA5.
    Documentation : https://open-meteo.com/en/docs/historical-weather-api

    Pour l'éolien offshore français, les principales zones sont :
    - Manche Est / Mer du Nord : lat=50.5-51.5, lon=1.5-3.5
    - Golfe du Lion : lat=42.5-43.5, lon=3.5-5.0
    - Atlantique : lat=46.5-47.5, lon=-3.0 à -1.5

    NOTE SUR LES DONNÉES ÉOLIENNES :
    Le projet coréen utilise wind_grid.xlsx avec des données de parcs identifiés.
    En France, les parcs éoliens offshore planifiés sont listés sur :
    - https://www.thewindpower.net
    - CRE : https://www.cre.fr/content/download/22800/290788
    - RTE : https://www.rte-france.com/analyses-tendances-et-prospectives/les-projets-de-developpement-du-reseau/les-projets-de-raccordement-en-mer
    """
    print(f"\n[WIND] Téléchargement profil éolien pour {region_name} (lat={lat}, lon={lon})...")

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": "wind_speed_100m",  # Vitesse de vent à 100m (hub height typique)
        "wind_speed_unit": "ms",
        "timezone": "Europe/Paris"
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        times = pd.to_datetime(data["hourly"]["time"])
        wind_speed = np.array(data["hourly"]["wind_speed_100m"])

        # Conversion vitesse → facteur de charge via courbe de puissance simplifiée
        # Turbine de référence : Vestas V164 offshore (8 MW)
        cf = _wind_speed_to_capacity_factor(wind_speed)

        df_out = pd.DataFrame({
            "index": times.astype(str),
            region_name: cf
        }).set_index("index")

        # Sauvegarder dans SQLite
        conn = sqlite3.connect(output_db)

        # Charger les données existantes si elles existent
        try:
            existing = pd.read_sql("SELECT * FROM wind_patterns", conn, index_col="index")
            df_out = existing.join(df_out, how="outer")
        except Exception:
            pass

        df_out.to_sql("wind_patterns", conn, if_exists="replace")
        conn.close()

        print(f"[OK] Profil éolien {region_name} : CF moyen = {cf.mean():.3f}")
        return df_out

    except Exception as e:
        print(f"[WARN] Erreur téléchargement vent : {e}. Génération profil synthétique.")
        return _generate_synthetic_wind_and_save(year, region_name, output_db)


def _wind_speed_to_capacity_factor(wind_speed: np.ndarray,
                                    v_cut_in: float = 3.0,
                                    v_rated: float = 12.0,
                                    v_cut_out: float = 25.0) -> np.ndarray:
    """
    Courbe de puissance simplifiée pour éolienne offshore.
    Paramètres typiques pour Vestas V164-9.5MW ou similaire.
    """
    cf = np.zeros_like(wind_speed, dtype=float)
    # Rampe linéaire entre cut-in et rated
    linear_zone = (wind_speed >= v_cut_in) & (wind_speed < v_rated)
    cf[linear_zone] = ((wind_speed[linear_zone] - v_cut_in) / (v_rated - v_cut_in)) ** 3
    # Pleine puissance entre rated et cut-out
    full_power = (wind_speed >= v_rated) & (wind_speed <= v_cut_out)
    cf[full_power] = 1.0
    return np.clip(cf, 0, 1)


def _generate_synthetic_wind_and_save(year: int, region_name: str, output_db: str):
    """Profil éolien offshore synthétique basé sur des statistiques françaises."""
    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
    n = len(date_range)
    months = date_range.month

    # CF mensuel moyen pour l'éolien offshore français (source : RTE bilans annuels)
    monthly_cf = {1: 0.45, 2: 0.42, 3: 0.38, 4: 0.35, 5: 0.30,
                  6: 0.28, 7: 0.25, 8: 0.27, 9: 0.32, 10: 0.38, 11: 0.43, 12: 0.46}
    base_cf = np.array([monthly_cf[m] for m in months])

    np.random.seed(123)
    cf = base_cf * np.random.lognormal(0, 0.3, n)
    cf = np.clip(cf, 0, 1)

    df_out = pd.DataFrame({"index": date_range.astype(str), region_name: cf}).set_index("index")

    conn = sqlite3.connect(output_db)
    df_out.to_sql("wind_patterns", conn, if_exists="replace")
    conn.close()

    print(f"[OK] Profil éolien synthétique {region_name} : CF moyen = {cf.mean():.3f}")
    return df_out


# =============================================================
# 4. PROFIL DE CHARGE INDUSTRIEL
#    Source : Données types ENTSO-E / RTE
# =============================================================

def create_industrial_load_pattern(year: int = 2030,
                                    output_db: str = "database/load_patterns.db"):
    """
    Crée le profil de charge horaire normalisé pour un industriel français.

    Dans le code coréen, ce profil représente la consommation horaire normalisée
    de SK et Samsung. En France, on utilise un profil industriel type.

    Logique économique :
    - Un industriel continu (chimie, métallurgie, etc.) a un profil quasi-plat (CF 85-95%)
    - Un industriel 3x8 a un profil avec creux nuit/week-end
    - Le profil est normalisé entre 0 et 1 — la charge réelle = profil × load_MW

    Profils de référence disponibles sur :
    - RTE : https://www.rte-france.com/analyses-tendances-et-prospectives/bilan-previsionnel-2050-futurs-energetiques
    - ENTSO-E : https://transparency.entsoe.eu/load-domain/r2/totalLoadR2/show

    Choisissez le profil adapté à votre client industriel :
    """
    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
    hours = date_range.hour
    weekdays = date_range.weekday

    is_weekend = weekdays >= 5
    is_night = (hours >= 22) | (hours < 6)
    is_daytime = (hours >= 8) & (hours < 20)

    # Profil semi-continu typique (ex: chimie fine, datacenter, cimenterie)
    load = np.ones(len(date_range)) * 0.90  # base à 90%
    load = np.where(is_weekend & is_night, 0.70, load)   # creux nuit week-end
    load = np.where(is_weekend & ~is_night, 0.80, load)  # week-end jour
    load = np.where(~is_weekend & is_daytime, 1.00, load) # pointe semaine

    # Légère variabilité aléatoire
    np.random.seed(42)
    load += np.random.normal(0, 0.02, len(date_range))
    load = np.clip(load, 0.5, 1.0)

    df_out = pd.DataFrame({
        "datetime": date_range.astype(str),
        "value": load
    })

    conn = sqlite3.connect(output_db)
    df_out.to_sql("load_patterns", conn, if_exists="replace", index=False)
    conn.close()

    print(f"[OK] Profil de charge industriel créé. CF moyen = {load.mean():.3f}")
    return df_out


# =============================================================
# 5. KEPCO_FRANCE.XLSX (tarifs réseau)
#    Remplace database/KEPCO.xlsx
# =============================================================

def create_kepco_france_excel(output_path: str = "database/KEPCO_france.xlsx"):
    """
    Crée le fichier Excel équivalent au KEPCO.xlsx coréen pour la France.

    Structure identique pour rester compatible avec le code original.
    Les sheets correspondent aux options de tarification TURPE HTB.
    """

    # ---- Sheet "timezone" ----
    # Mapping heure → type de plage (HPH/HCH/HPE/HCE)
    # Simplifié : HP=jour semaine, HC=nuit+weekend
    timezone_data = {"hours": list(range(24))}
    months_fr = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    winter_months = ["Jan", "Feb", "Mar", "Nov", "Dec"]
    summer_months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"]

    for m in months_fr:
        if m in winter_months:
            timezone_data[m] = ["HC" if h < 6 or h >= 22 else "HP" for h in range(24)]
        else:
            timezone_data[m] = ["HC" if h < 6 or h >= 22 else "HP" for h in range(24)]

    timezone_df = pd.DataFrame(timezone_data)

    # ---- Sheet "season" ----
    season_data = {
        "month": months_fr,
        "season": ["Hiver", "Hiver", "Hiver", "Ete", "Ete", "Ete",
                   "Ete", "Ete", "Ete", "Ete", "Hiver", "Hiver"]
    }
    season_df = pd.DataFrame(season_data)

    # ---- Sheet "contract" ----
    # Composante puissance TURPE HTB (€/kW/an)
    # HTB1 = 400kV, HTB2 = 225kV, HTB3 = 63kV
    contract_data = {
        "tarif": ["HTB1", "HTB2", "HTB3"],
        "fees": [5.5, 7.5, 9.2]  # €/kW/an (source CRE 2024)
    }
    contract_df = pd.DataFrame(contract_data).set_index("tarif")

    # ---- Sheets de tarifs ----
    # Composante énergie TURPE (€/MWh) par saison et type d'heure
    # Source : CRE délibération TURPE 6 HTB (dernière en vigueur)

    htb3_data = pd.DataFrame({
        "Hiver": [5.2, 1.8],   # HP Hiver, HC Hiver
        "Ete": [1.0, 0.5]       # HP Été, HC Été
    }, index=["HP", "HC"])

    htb2_data = pd.DataFrame({
        "Hiver": [4.5, 1.5],
        "Ete": [0.9, 0.4]
    }, index=["HP", "HC"])

    htb1_data = pd.DataFrame({
        "Hiver": [3.8, 1.2],
        "Ete": [0.8, 0.3]
    }, index=["HP", "HC"])

    # ---- Écriture Excel ----
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        timezone_df.to_excel(writer, sheet_name="timezone", index=False)
        season_df.to_excel(writer, sheet_name="season", index=False)
        contract_df.to_excel(writer, sheet_name="contract")
        htb3_data.to_excel(writer, sheet_name="HTB3")
        htb2_data.to_excel(writer, sheet_name="HTB2")
        htb1_data.to_excel(writer, sheet_name="HTB1")

    print(f"[OK] {output_path} créé avec les tarifs TURPE HTB 2024.")
    print("     IMPORTANT : Vérifiez les tarifs sur https://www.cre.fr")


# =============================================================
# 6. WIND_GRID.XLSX (données parcs éoliens offshore)
#    Remplace database/wind_grid.xlsx
# =============================================================

def create_wind_grid_france(output_path: str = "database/wind_grid_france.xlsx"):
    """
    Crée le fichier wind_grid.xlsx pour les parcs éoliens offshore français planifiés.

    Source officielle :
    - CRE : https://www.cre.fr/Electricite/Energies-renouvelables/L-eolien-en-mer
    - RTE : https://www.rte-france.com/analyses-tendances-et-prospectives
    - thewindpower.net : https://www.thewindpower.net/country_fr_5_france.php

    PARCS OFFSHORE FRANÇAIS (planifiés ou construits au 2024) :
    +-----------------------+----------+--------+-----------+----------+
    | Parc                  | Région   | MW     | Statut    | Distance |
    +-----------------------+----------+--------+-----------+----------+
    | Saint-Nazaire         | Pays Loire| 480   | Opérationnel| 12km   |
    | Fécamp                | Normandie | 500   | En cours   | 15km   |
    | Courseulles-sur-Mer   | Normandie | 450   | Planifié   | 10km   |
    | Saint-Brieuc          | Bretagne  | 496   | En cours   | 16km   |
    | Dunkerque             | Hauts-Fr  | 600   | Planifié   | 10km   |
    | Golfe du Lion         | Occitanie | 250   | Planifié   | 20km   |
    +-----------------------+----------+--------+-----------+----------+
    """
    wind_data = pd.DataFrame({
        # Colonnes identiques au code coréen pour compatibilité
        "nom_parc": [
            "Saint-Nazaire", "Fécamp", "Courseulles-sur-Mer",
            "Saint-Brieuc", "Dunkerque", "Golfe du Lion"
        ],
        "admin_boundaries": [
            "Pays de la Loire", "Normandie", "Normandie",
            "Bretagne", "Hauts-de-France", "Occitanie"
        ],
        # LCOE estimé en €/MWh (source : ADEME, commission CRE)
        # Les appels d'offres récents ont atteint ~50-80 €/MWh
        "LCOE": [65.0, 70.0, 72.0, 75.0, 60.0, 80.0],
        # Poids REC/GO (1.0 = une GO par MWh)
        "rec_weight": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        # Capacité par bin (MW) — à adapter
        "capacity": [480, 500, 450, 496, 600, 250],
        # Distance à la côte (m) — influence les coûts de connexion
        "DT_m": [12_000, 15_000, 10_000, 16_000, 10_000, 20_000],
        # Coordonnées approximatives
        "lat": [47.2, 49.8, 49.4, 48.6, 51.1, 43.1],
        "lon": [-2.5, 0.5, -0.5, -2.8, 2.2, 4.2],
    })

    wind_data.to_excel(output_path, index=False)
    print(f"[OK] {output_path} créé avec {len(wind_data)} parcs éoliens offshore français.")
    print("     Source : CRE appels d'offres éolien en mer")
    print("     IMPORTANT : Mettez à jour avec les données CRE les plus récentes.")


# =============================================================
# PROGRAMME PRINCIPAL
# =============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("INITIALISATION DES DONNÉES FRANCE PPA")
    print("=" * 60)

    create_database_structure()

    print("\n--- 1. Grid France ---")
    create_grid_france_csv()

    print("\n--- 2. Tarifs réseau TURPE ---")
    create_kepco_france_excel()

    print("\n--- 3. Parcs éoliens offshore ---")
    create_wind_grid_france()

    print("\n--- 4. Profil de charge industriel ---")
    create_industrial_load_pattern(year=2030)

    print("\n--- 5. Profils solaires PVGIS ---")
    print("Exemples pour 3 localisations industrielles françaises :")
    # Dunkerque (zone industrielle nord, ArcelorMittal, etc.)
    download_pvgis_solar_profile(lat=51.03, lon=2.38, year=2020,
                                  output_db="database/solar_patterns.db")

    print("\n--- 6. Profils éoliens Open-Meteo ---")
    sites_eoliens = [
        ("Hauts-de-France", 51.0, 2.5),    # Manche Est
        ("Normandie",       49.8, 0.5),    # Côte normande
        ("Bretagne",        48.0, -3.0),   # Atlantique nord
        ("Pays de la Loire", 47.0, -2.5),  # Atlantique centre
        ("Occitanie",       43.0, 4.5),    # Méditerranée
    ]
    for region, lat, lon in sites_eoliens:
        download_wind_profile_openmeteo(lat=lat, lon=lon, year=2020,
                                         region_name=region,
                                         output_db="database/wind_patterns.db")

    print("\n" + "=" * 60)
    print("DONNÉES FRANÇAISES PRÊTES.")
    print("=" * 60)
    print("""
PROCHAINES ÉTAPES :
1. Modifier ppamodule.py :
   - Changer 'database/grid.csv' → 'database/grid_france.csv'
   - Changer 'database/KEPCO.xlsx' → 'database/KEPCO_france.xlsx'
   - Changer 'database/wind_grid.xlsx' → 'database/wind_grid_france.xlsx'
   - Adapter les charges : SK/Samsung → votre industriel français
   - Adapter la devise : KRW → EUR (supprimer le currency_exchange ou mettre 1)
   - Adapter le filtre régional admin_boundaries (régions françaises)

2. Modifier scenario_defaults.xlsx :
   - Currency Exchange Rate : 1 (EUR = EUR)
   - Initial SMP (€/MWh) : 50000 (= 50 €/MWh en valeurs natives)
   - Initial REC Price (€/MWh) : 8000 (= 8 €/MWh)
   - Selected Sheet : HTB3 (ou HTB2 selon le raccordement)

3. Pour les données GIS (gisdata/) :
   - Télécharger BDTopo IGN : https://geoservices.ign.fr/bdtopo
   - Télécharger RPG (parcelles agricoles) : https://geoservices.ign.fr/rpg
   - Télécharger zones disponibles ENEDIS : https://data.enedis.fr

4. Pour les données réelles (fortement recommandé) :
   EPEX Spot : https://data.rte-france.com → S'inscrire → API Wholesale Market
   eco2mix   : https://odre.opendatasoft.com → eco2mix national consolidé
   TURPE     : https://www.cre.fr → Délibérations TURPE 6 HTB
""")
