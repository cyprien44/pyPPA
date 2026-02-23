"""
download_france_data.py
======================
Télécharge / génère les données françaises pour alimenter la version "French_PPA".

✅ Objectif :
- Stocker TOUS les fichiers dans :
    French_PPA/database/
    French_PPA/gisdata/

Crée :
French_PPA/
├── database/
│   ├── grid_france.csv
│   ├── solar_patterns.db
│   ├── wind_patterns.db
│   ├── load_patterns.db
│   ├── TURPE_france.xlsx
│   └── wind_grid_france.xlsx
└── gisdata/
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import requests


# =============================================================
# PATHS (robustes, indépendants du "working directory")
# =============================================================

BASE_DIR = Path(__file__).resolve().parent          # .../French_PPA
DB_DIR = BASE_DIR / "database"                      # .../French_PPA/database
GIS_DIR = BASE_DIR / "gisdata"                      # .../French_PPA/gisdata


def create_database_structure() -> None:
    """Crée les dossiers nécessaires dans French_PPA/."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    GIS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[OK] Dossiers prêts :\n - {DB_DIR}\n - {GIS_DIR}")


# =============================================================
# 1) GRID FRANCE (CO2, REN_SHARE, CAPEX) → database/grid_france.csv
# =============================================================

def create_grid_france_csv() -> pd.DataFrame:
    """
    Crée database/grid_france.csv avec des projections FR (placeholder).
    À remplacer plus tard par des données réelles si besoin.
    """
    years = range(2023, 2051)

    data = {
        "year": list(years),
        # CAPEX solaire (€/MW) — placeholder
        "solar_capex": np.linspace(900_000, 500_000, len(years)),
        # CAPEX éolien offshore (€/MW) — placeholder
        "wind_capex": np.linspace(3_000_000, 1_800_000, len(years)),
        # Intensité CO2 réseau (gCO2/kWh) — placeholder
        "co2": np.linspace(55.0, 20.0, len(years)),
        # Part ENR dans le mix (fraction) — placeholder
        "ren_share": np.linspace(0.29, 0.80, len(years)),
    }

    df = pd.DataFrame(data).set_index("year")
    out_path = DB_DIR / "grid_france.csv"
    df.to_csv(out_path)

    print(f"[OK] {out_path.name} créé → {out_path}")
    print(f"     CO2 2023 : {df.loc[2023, 'co2']:.1f} gCO2/kWh")
    print(f"     CAPEX PV 2030 : {df.loc[2030, 'solar_capex']/1000:.0f} k€/MW")
    return df


# =============================================================
# 2) SOLAR PROFILE (PVGIS) → database/solar_patterns.db
# =============================================================

def download_pvgis_solar_profile(
    lat: float,
    lon: float,
    year: int = 2020,
    output_db: Path | None = None
) -> pd.DataFrame:
    """
    Télécharge un profil solaire horaire depuis PVGIS et le stocke dans SQLite
    table: solar_patterns, colonne: q99, index: datetime.
    """
    output_db = output_db or (DB_DIR / "solar_patterns.db")
    output_db.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n[PVGIS] Profil solaire lat={lat}, lon={lon}, year={year}")
    print(f"       -> DB: {output_db}")

    url = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
    params = {
        "lat": lat,
        "lon": lon,
        "raddatabase": "PVGIS-SARAH2",
        "startyear": year,
        "endyear": year,
        "pvcalculation": 1,
        "peakpower": 1,          # 1 kWp
        "loss": 14,              # pertes système
        "pvtechchoice": "crystSi",
        "mountingplace": "free",
        "angle": 30,
        "aspect": 0,
        "outputformat": "json",
        "browser": 0,
    }

    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        hourly = data["outputs"]["hourly"]
        df = pd.DataFrame(hourly)
        df["time"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M")
        df = df.set_index("time")

        # Normalisation 0..1
        df["capacity_factor"] = (df["P"] / 1000.0).clip(0, 1)

        df_out = df[["capacity_factor"]].copy()
        df_out.columns = ["q99"]
        df_out.index.name = "datetime"

        # SQLite (format compatible avec ton code)
        conn = sqlite3.connect(str(output_db))
        to_sql = df_out.reset_index()
        to_sql["datetime"] = to_sql["datetime"].astype(str)
        to_sql.to_sql("solar_patterns", conn, if_exists="replace", index=False)
        conn.close()

        print(f"[OK] solar_patterns.db écrit : {len(df_out)} heures, CF mean={df_out['q99'].mean():.3f}")
        return df_out

    except requests.exceptions.ConnectionError:
        print("[WARN] PVGIS inaccessible → profil synthétique")
        return _generate_synthetic_solar_and_save(lat, year, output_db)
    except Exception as e:
        print(f"[WARN] PVGIS erreur ({e}) → profil synthétique")
        return _generate_synthetic_solar_and_save(lat, year, output_db)


def _generate_synthetic_solar_and_save(lat: float, year: int, output_db: Path) -> pd.DataFrame:
    """Profil solaire synthétique si PVGIS n'est pas dispo."""
    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
    hours = date_range.hour
    day_of_year = date_range.dayofyear

    sunrise = 6 + 2 * np.cos(2 * np.pi * (day_of_year - 172) / 365)
    sunset = 18 + 2 * np.sin(2 * np.pi * (day_of_year - 172) / 365)
    solar_noon = (sunrise + sunset) / 2

    daylight = (hours >= sunrise.astype(int)) & (hours <= sunset.astype(int))
    peak_factor = 0.85 * (1 + 0.15 * np.sin(2 * np.pi * (day_of_year - 80) / 365))
    gaussian = np.exp(-0.5 * ((hours - solar_noon) / 3) ** 2)

    cf = np.where(daylight, peak_factor * gaussian, 0.0)
    cf = np.clip(cf, 0, 1)

    df_out = pd.DataFrame({"datetime": date_range.astype(str), "q99": cf})

    conn = sqlite3.connect(str(output_db))
    df_out.to_sql("solar_patterns", conn, if_exists="replace", index=False)
    conn.close()

    print(f"[OK] Profil solaire synthétique : CF mean={cf.mean():.3f} → {output_db}")
    return df_out.set_index("datetime")


# =============================================================
# 3) WIND PROFILE (Open-Meteo) → database/wind_patterns.db
# =============================================================

def download_wind_profile_openmeteo(
    lat: float,
    lon: float,
    year: int = 2020,
    region_name: str = "Nord",
    output_db: Path | None = None
) -> pd.DataFrame:
    """
    Télécharge le profil de vent Open-Meteo (wind_speed_100m) et le convertit en CF.
    Stocke dans SQLite table: wind_patterns, index: "index", colonne: {region_name}
    """
    output_db = output_db or (DB_DIR / "wind_patterns.db")
    output_db.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n[WIND] Profil éolien {region_name} (lat={lat}, lon={lon})")
    print(f"       -> DB: {output_db}")

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": "wind_speed_100m",
        "wind_speed_unit": "ms",
        "timezone": "Europe/Paris",
    }

    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        times = pd.to_datetime(data["hourly"]["time"])
        wind_speed = np.array(data["hourly"]["wind_speed_100m"], dtype=float)

        cf = _wind_speed_to_capacity_factor(wind_speed)

        df_out = pd.DataFrame({"index": times.astype(str), region_name: cf}).set_index("index")

        conn = sqlite3.connect(str(output_db))

        # merge si table existante
        try:
            existing = pd.read_sql("SELECT * FROM wind_patterns", conn, index_col="index")
            df_out = existing.join(df_out, how="outer")
        except Exception:
            pass

        df_out.to_sql("wind_patterns", conn, if_exists="replace")
        conn.close()

        print(f"[OK] Profil éolien {region_name} : CF mean={cf.mean():.3f}")
        return df_out

    except Exception as e:
        print(f"[WARN] Erreur Open-Meteo ({e}) → profil synthétique")
        return _generate_synthetic_wind_and_save(year, region_name, output_db)


def _wind_speed_to_capacity_factor(
    wind_speed: np.ndarray,
    v_cut_in: float = 3.0,
    v_rated: float = 12.0,
    v_cut_out: float = 25.0
) -> np.ndarray:
    """Courbe de puissance simplifiée (offshore)."""
    cf = np.zeros_like(wind_speed, dtype=float)
    linear_zone = (wind_speed >= v_cut_in) & (wind_speed < v_rated)
    cf[linear_zone] = ((wind_speed[linear_zone] - v_cut_in) / (v_rated - v_cut_in)) ** 3
    full_power = (wind_speed >= v_rated) & (wind_speed <= v_cut_out)
    cf[full_power] = 1.0
    return np.clip(cf, 0, 1)


def _generate_synthetic_wind_and_save(year: int, region_name: str, output_db: Path) -> pd.DataFrame:
    """Profil éolien synthétique."""
    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
    months = date_range.month

    monthly_cf = {1: 0.45, 2: 0.42, 3: 0.38, 4: 0.35, 5: 0.30,
                  6: 0.28, 7: 0.25, 8: 0.27, 9: 0.32, 10: 0.38, 11: 0.43, 12: 0.46}
    base_cf = np.array([monthly_cf[m] for m in months], dtype=float)

    rng = np.random.default_rng(123)
    cf = np.clip(base_cf * rng.lognormal(0, 0.3, len(date_range)), 0, 1)

    df_out = pd.DataFrame({"index": date_range.astype(str), region_name: cf}).set_index("index")

    conn = sqlite3.connect(str(output_db))
    df_out.to_sql("wind_patterns", conn, if_exists="replace")
    conn.close()

    print(f"[OK] Profil éolien synthétique {region_name} : CF mean={cf.mean():.3f} → {output_db}")
    return df_out


# =============================================================
# 4) INDUSTRIAL LOAD PATTERN → database/load_patterns.db
# =============================================================

def create_industrial_load_pattern(year: int = 2030, output_db: Path | None = None) -> pd.DataFrame:
    """
    Crée un profil de charge industriel normalisé (0..1) dans SQLite.
    """
    output_db = output_db or (DB_DIR / "load_patterns.db")
    output_db.parent.mkdir(parents=True, exist_ok=True)

    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
    hours = date_range.hour
    weekdays = date_range.weekday

    is_weekend = weekdays >= 5
    is_night = (hours >= 22) | (hours < 6)
    is_daytime = (hours >= 8) & (hours < 20)

    load = np.ones(len(date_range)) * 0.90
    load = np.where(is_weekend & is_night, 0.70, load)
    load = np.where(is_weekend & ~is_night, 0.80, load)
    load = np.where(~is_weekend & is_daytime, 1.00, load)

    rng = np.random.default_rng(42)
    load = np.clip(load + rng.normal(0, 0.02, len(date_range)), 0.5, 1.0)

    df_out = pd.DataFrame({"datetime": date_range.astype(str), "value": load})

    conn = sqlite3.connect(str(output_db))
    df_out.to_sql("load_patterns", conn, if_exists="replace", index=False)
    conn.close()

    print(f"[OK] Profil charge industriel : CF mean={load.mean():.3f} → {output_db}")
    return df_out


# =============================================================
# 5) TURPE_FRANCE.XLSX (TURPE-like) → database/TURPE_france.xlsx
# =============================================================

def create_TURPE_france_excel(output_path: Path | None = None) -> Path:
    """
    Crée un Excel au format "TURPE.xlsx" compatible avec ton parseur :
    - timezone
    - season
    - contract
    - HTB1 / HTB2 / HTB3
    """
    output_path = output_path or (DB_DIR / "TURPE_france.xlsx")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    months_fr = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    winter_months = {"Jan", "Feb", "Mar", "Nov", "Dec"}

    # timezone sheet
    timezone_data = {"hours": list(range(24))}
    for m in months_fr:
        timezone_data[m] = ["HC" if (h < 6 or h >= 22) else "HP" for h in range(24)]
    timezone_df = pd.DataFrame(timezone_data)

    # season sheet
    season_df = pd.DataFrame({
        "month": months_fr,
        "season": ["Hiver" if m in winter_months else "Ete" for m in months_fr]
    })

    # contract sheet (€/kW/an)
    contract_df = pd.DataFrame({
        "tarif": ["HTB1", "HTB2", "HTB3"],
        "fees": [5.5, 7.5, 9.2]
    }).set_index("tarif")

    # energy component (€/MWh) by season x (HP/HC)
    htb3_data = pd.DataFrame({"Hiver": [5.2, 1.8], "Ete": [1.0, 0.5]}, index=["HP", "HC"])
    htb2_data = pd.DataFrame({"Hiver": [4.5, 1.5], "Ete": [0.9, 0.4]}, index=["HP", "HC"])
    htb1_data = pd.DataFrame({"Hiver": [3.8, 1.2], "Ete": [0.8, 0.3]}, index=["HP", "HC"])

    with pd.ExcelWriter(str(output_path), engine="openpyxl") as writer:
        timezone_df.to_excel(writer, sheet_name="timezone", index=False)
        season_df.to_excel(writer, sheet_name="season", index=False)
        contract_df.to_excel(writer, sheet_name="contract")
        htb3_data.to_excel(writer, sheet_name="HTB3")
        htb2_data.to_excel(writer, sheet_name="HTB2")
        htb1_data.to_excel(writer, sheet_name="HTB1")

    print(f"[OK] {output_path.name} créé → {output_path}")
    return output_path


# =============================================================
# 6) WIND GRID FRANCE → database/wind_grid_france.xlsx
# =============================================================

def create_wind_grid_france(output_path: Path | None = None) -> Path:
    """Crée wind_grid_france.xlsx (placeholder) compatible avec ton code."""
    output_path = output_path or (DB_DIR / "wind_grid_france.xlsx")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wind_data = pd.DataFrame({
        "nom_parc": [
            "Saint-Nazaire", "Fécamp", "Courseulles-sur-Mer",
            "Saint-Brieuc", "Dunkerque", "Golfe du Lion"
        ],
        "admin_boundaries": [
            "Pays de la Loire", "Normandie", "Normandie",
            "Bretagne", "Hauts-de-France", "Occitanie"
        ],
        "LCOE": [65.0, 70.0, 72.0, 75.0, 60.0, 80.0],     # €/MWh placeholder
        "rec_weight": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "capacity": [480, 500, 450, 496, 600, 250],
        "DT_m": [12_000, 15_000, 10_000, 16_000, 10_000, 20_000],
        "lat": [47.2, 49.8, 49.4, 48.6, 51.1, 43.1],
        "lon": [-2.5, 0.5, -0.5, -2.8, 2.2, 4.2],
    })

    wind_data.to_excel(str(output_path), index=False)
    print(f"[OK] {output_path.name} créé → {output_path}")
    return output_path


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("INITIALISATION DES DONNÉES FRANCE PPA (French_PPA)")
    print("=" * 60)

    create_database_structure()

    print("\n--- 1) Grid France ---")
    create_grid_france_csv()

    print("\n--- 2) Tarifs réseau (TURPE_france.xlsx) ---")
    create_TURPE_france_excel()

    print("\n--- 3) Wind grid (wind_grid_france.xlsx) ---")
    create_wind_grid_france()

    print("\n--- 4) Profil de charge industriel ---")
    create_industrial_load_pattern(year=2030)

    print("\n--- 5) Profil solaire PVGIS (Dunkerque) ---")
    download_pvgis_solar_profile(
        lat=51.03, lon=2.38, year=2020,
        output_db=DB_DIR / "solar_patterns.db"
    )

    print("\n--- 6) Profils éoliens Open-Meteo (plusieurs régions) ---")
    sites_eoliens = [
        ("Hauts-de-France", 51.0, 2.5),
        ("Normandie", 49.8, 0.5),
        ("Bretagne", 48.0, -3.0),
        ("Pays de la Loire", 47.0, -2.5),
        ("Occitanie", 43.0, 4.5),
    ]
    for region, lat, lon in sites_eoliens:
        download_wind_profile_openmeteo(
            lat=lat, lon=lon, year=2020,
            region_name=region,
            output_db=DB_DIR / "wind_patterns.db"
        )

    print("\n" + "=" * 60)
    print("DONNÉES FRANÇAISES PRÊTES.")
    print("=" * 60)
    print(f"DB_DIR  : {DB_DIR}")
    print(f"GIS_DIR : {GIS_DIR}")