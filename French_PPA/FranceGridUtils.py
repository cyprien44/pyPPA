"""
FranceGridUtils.py
==================
Remplacement de KEPCOutils.py pour le contexte français.

Contexte économique :
- En France, un industriel HTB (>50MW) paie l'électricité via :
  1. Le TURPE HTB (Tarif d'Utilisation des Réseaux Publics) fixé par la CRE
  2. Le prix de l'énergie : soit marché EPEX Spot Day-Ahead, soit contrat à terme
  3. Les taxes : TICFE (ex-CSPE), CTA, TVA (souvent récupérable pour les industriels)

- Les Garanties d'Origine (GO) remplacent les RECs coréens.
  Prix marché GO en France : ~5-15 €/MWh (vs 80 000 KRW/MWh pour les RECs coréens)

- L'EU ETS (European Emissions Trading System) remplace l'ETS coréen.
  Prix CO2 en 2024 : ~60-70 €/tCO2

Source des données :
- EPEX Spot historique : https://data.rte-france.com (API gratuite après inscription)
- TURPE HTB : CRE - https://www.cre.fr/Electricite/Reseaux-d-electricite/Tarifs-d-acces
- Intensité CO2 / mix : https://odre.opendatasoft.com (eco2mix RTE)
- Prix GO : EEX / AIB
"""

import pandas as pd
import numpy as np
import requests
import sqlite3
import os


# ============================================================
# 1. REMPLACEMENT DE process_kepco_data()
#    → process_france_grid_data()
# ============================================================

def process_france_grid_data(year: int, epex_filepath: str = None,
                              turpe_params: dict = None) -> tuple:
    """
    Construit le profil horaire du coût d'électricité réseau pour un industriel français HTB.

    Logique économique :
    - Le coût total = Prix EPEX Spot (énergie) + TURPE HTB (acheminement)
    - Le TURPE HTB a une composante "puissance" (€/kW/an) et une composante "énergie" (€/MWh)
    - En France il n'y a pas de tarification TOU aussi structurée qu'en Corée,
      mais on distingue les heures HPH/HCH/HPE/HCE/HC-EJP selon le contrat.
    - Pour simplifier et rester fidèle à la structure du code original,
      on retourne un DataFrame horaire avec colonne 'rate' en €/MWh
      et un contract_fee en €/kW/an (équivalent du contract_fee KEPCO).

    Paramètres :
    -----------
    year : int
        Année de modélisation (ex: 2030)
    epex_filepath : str, optional
        Chemin vers un CSV EPEX Spot téléchargé depuis ODRE.
        Si None, utilise des valeurs moyennes de référence.
    turpe_params : dict, optional
        Paramètres TURPE HTB personnalisés. Si None, utilise les valeurs 2024 CRE.

    Retourne :
    ----------
    temporal_df : pd.DataFrame
        DataFrame indexé par datetime avec colonnes 'rate' (€/MWh) et 'contract_fee' (€/MWh)
    contract_fee : float
        Composante puissance du TURPE annualisée en €/kW/an
    """

    # ---- Paramètres TURPE HTB par défaut (CRE 2024, HTB2 - 63kV) ----
    # Source : https://www.cre.fr/Electricite/Reseaux-d-electricite/Tarifs-d-acces
    # Le TURPE HTB2 pour un industriel grand compte (>50 MW) :
    #   - Composante de soutirage annuelle (CS) : ~7.5 €/kW/an (puissance souscrite)
    #   - Composante d'énergie en HPH : ~5.2 €/MWh
    #   - Composante d'énergie en HCH : ~1.8 €/MWh
    #   - Composante d'énergie en HPE/HCE : ~0.5-1.0 €/MWh
    # Note : Ces valeurs évoluent chaque année. Toujours vérifier sur cre.fr.

    if turpe_params is None:
        turpe_params = {
            # Composante annuelle puissance souscrite (€/kW/an) → équiv. contract_fee
            "contract_fee_eur_kw_year": 7.5,
            # Composante énergie par type d'heure (€/MWh)
            "HPH": 5.2,   # Heures Pleines Hiver
            "HCH": 1.8,   # Heures Creuses Hiver
            "HPE": 1.0,   # Heures Pleines Été
            "HCE": 0.5,   # Heures Creuses Été
            # Taxes et contributions (€/MWh) - pour industriels HTB souvent exonérés partiellement
            "ticfe": 0.5,   # TICFE réduite pour industriels intensifs (vs 20.5 €/MWh particuliers)
            "cta": 0.3,     # Contribution Tarifaire d'Acheminement
        }

    contract_fee = turpe_params["contract_fee_eur_kw_year"]

    # ---- Génération du profil horaire ----
    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")

    # Chargement ou simulation du prix EPEX Spot
    if epex_filepath and os.path.exists(epex_filepath):
        epex_df = _load_epex_from_csv(epex_filepath, year)
    else:
        # Profil EPEX Spot synthétique basé sur les moyennes historiques françaises
        # Source : RTE eco2mix historique 2022-2024
        # Prix moyen ~80 €/MWh avec variation saisonnière et heure de pointe
        print("[INFO] Aucun fichier EPEX Spot fourni. Utilisation d'un profil synthétique.")
        print("[INFO] Téléchargez les données réelles sur : https://odre.opendatasoft.com")
        epex_df = _generate_synthetic_epex_profile(year, base_price_eur_mwh=80.0)

    # Calcul du TURPE énergie selon les heures
    turpe_energy = _compute_turpe_energy_by_hour(date_range, turpe_params, year)

    # Assemblage du DataFrame temporel
    temporal_df = pd.DataFrame(index=date_range)
    temporal_df.index.name = "datetime"
    temporal_df["epex_spot"] = epex_df.reindex(date_range).fillna(method="ffill")
    temporal_df["turpe_energy"] = turpe_energy
    temporal_df["ticfe"] = turpe_params["ticfe"]
    temporal_df["cta"] = turpe_params["cta"]

    # Taux total = prix énergie + TURPE énergie + taxes
    temporal_df["rate"] = (
        temporal_df["epex_spot"]
        + temporal_df["turpe_energy"]
        + temporal_df["ticfe"]
        + temporal_df["cta"]
    )

    # Répartir la composante puissance du TURPE sur les heures (pour être comparable au code coréen)
    hours_in_year = len(date_range)
    temporal_df["contract_fee"] = (contract_fee * 1000) / hours_in_year  # converti en €/MWh·h

    return temporal_df, contract_fee


def _load_epex_from_csv(filepath: str, year: int) -> pd.Series:
    """
    Charge les prix EPEX Spot Day-Ahead depuis un CSV téléchargé sur ODRE.

    Format attendu du CSV ODRE :
    Colonnes : "Horodate", "Prix spot France (€/MWh)"
    URL : https://odre.opendatasoft.com/explore/dataset/prix-spot-da-horaires/

    Instructions de téléchargement :
    1. Aller sur https://odre.opendatasoft.com
    2. Rechercher "Prix spot Day-Ahead"
    3. Filtrer par année
    4. Exporter en CSV
    """
    df = pd.read_csv(filepath, sep=";", parse_dates=["Horodate"])
    df = df.set_index("Horodate")
    df = df[df.index.year == year]
    price_col = [c for c in df.columns if "prix" in c.lower() or "price" in c.lower()][0]
    return df[price_col].rename("epex_spot")


def _generate_synthetic_epex_profile(year: int, base_price_eur_mwh: float = 80.0) -> pd.Series:
    """
    Génère un profil EPEX Spot synthétique représentatif du marché français.

    Logique économique :
    - Prix moyen annuel de base (paramètre)
    - Surcôut hivernal (pic de demande de chauffage)
    - Pic de midi (production solaire réduit les prix en été)
    - Variation nuit/jour (courbe de charge industrielle)
    """
    date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
    n = len(date_range)

    # Composante de base
    prices = np.full(n, base_price_eur_mwh)

    months = date_range.month
    hours = date_range.hour

    # Saisonnalité : hiver +30%, été -10% (duck curve solaire)
    seasonal = np.where(months.isin([12, 1, 2]), 1.30,
               np.where(months.isin([6, 7, 8]), 0.90, 1.0))
    prices *= seasonal

    # Variation horaire : pointe matin (8h-10h) et soir (18h-20h), creux nuit et midi en été
    hour_factor = np.ones(n)
    is_summer = months.isin([5, 6, 7, 8, 9])
    is_winter = ~is_summer

    # Pointe matin/soir en hiver
    hour_factor = np.where(is_winter & hours.isin([8, 9, 10]), 1.20, hour_factor)
    hour_factor = np.where(is_winter & hours.isin([18, 19, 20]), 1.25, hour_factor)
    hour_factor = np.where(is_winter & hours.isin([0, 1, 2, 3, 4]), 0.75, hour_factor)

    # Duck curve en été : creux midi (solaire), pointe soir
    hour_factor = np.where(is_summer & hours.isin([11, 12, 13, 14]), 0.80, hour_factor)
    hour_factor = np.where(is_summer & hours.isin([19, 20, 21]), 1.15, hour_factor)

    prices *= hour_factor

    # Bruit aléatoire réaliste (volatilité de marché)
    np.random.seed(42)
    prices *= np.random.lognormal(0, 0.10, n)

    return pd.Series(prices, index=date_range, name="epex_spot")


def _compute_turpe_energy_by_hour(date_range: pd.DatetimeIndex,
                                   turpe_params: dict, year: int) -> pd.Series:
    """
    Calcule la composante énergie du TURPE HTB par heure.

    Grille HPH/HCH/HPE/HCE selon le calendrier EDF/ENEDIS :
    - Hiver = Novembre à Mars (inclusive)
    - HP = 6h-22h en semaine (hors JF)
    - HC = 22h-6h + week-ends + jours fériés
    """
    months = date_range.month
    hours = date_range.hour
    weekdays = date_range.weekday  # 0=lundi, 5=samedi, 6=dimanche

    is_winter = months.isin([11, 12, 1, 2, 3])
    is_daytime = (hours >= 6) & (hours < 22)
    is_weekday = weekdays < 5  # Lundi-Vendredi

    hph_mask = is_winter & is_daytime & is_weekday
    hch_mask = is_winter & ~(is_daytime & is_weekday)
    hpe_mask = ~is_winter & is_daytime & is_weekday
    hce_mask = ~is_winter & ~(is_daytime & is_weekday)

    turpe_energy = np.zeros(len(date_range))
    turpe_energy[hph_mask] = turpe_params["HPH"]
    turpe_energy[hch_mask] = turpe_params["HCH"]
    turpe_energy[hpe_mask] = turpe_params["HPE"]
    turpe_energy[hce_mask] = turpe_params["HCE"]

    return pd.Series(turpe_energy, index=date_range)


# ============================================================
# 2. REMPLACEMENT DE multiyear_pricing()
#    → multiyear_pricing_france()
# ============================================================

def multiyear_pricing_france(temporal_df: pd.DataFrame, contract_fee: float,
                              start_year: int, num_years: int,
                              rate_increase: float,
                              annualised_contract: bool = True) -> tuple:
    """
    Projection multi-annuelle des tarifs électricité français.

    Logique économique :
    - Le TURPE est revu chaque année par la CRE (hausse moyenne ~4-6%/an depuis 2020)
    - Le prix EPEX Spot évolue selon le marché (très volatil — on utilise une tendance)
    - On applique le même mécanisme d'escalade que dans le code coréen original.

    IMPORTANT sur le taux d'escalade pour la France :
    - Historiquement TURPE : +4-6%/an
    - Prix de marché : très variable, hypothèse centrale +2-3%/an en termes réels
    - La valeur rate_increase couvre les deux composantes de façon simplifiée.
    """
    # Identique à la logique coréenne — la structure est universelle
    all_years_df = []
    preset_df = temporal_df.copy()
    preset_df.index = preset_df.index.strftime("%m-%d %H:%M")

    # Ajout du 29 février si nécessaire
    if not any(preset_df.index.str.startswith("02-29")):
        feb_28 = preset_df.loc["02-28 00:00":"02-28 23:00"].copy()
        feb_29 = feb_28.copy()
        feb_29.index = feb_29.index.str.replace("02-28", "02-29")
        preset_df = pd.concat([preset_df, feb_29])

    contract_fees = []

    for year in range(start_year, start_year + num_years):
        date_range = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31 23:00", freq="h")
        current_df = pd.DataFrame(index=date_range, columns=temporal_df.columns)
        current_df.index = current_df.index.strftime("%m-%d %H:%M")
        matching = current_df.index.intersection(preset_df.index)
        current_df.loc[matching] = preset_df.loc[matching].values

        escalation = (1 + rate_increase) ** (year - start_year)
        current_df["rate"] = current_df["rate"] * escalation

        current_df.index = date_range
        year_contract_fee = contract_fee * escalation
        contract_fees.append({"year": year, "rate": year_contract_fee})

        if annualised_contract:
            hours_in_year = len(date_range)
            current_df["contract_fee"] = year_contract_fee / hours_in_year

        all_years_df.append(current_df)

    long_df = pd.concat(all_years_df)
    return long_df, pd.DataFrame(contract_fees)


# ============================================================
# 3. REMPLACEMENT DE create_rec_grid()
#    → create_go_grid() (Garanties d'Origine)
# ============================================================

def create_go_grid(start_year: int, end_year: int,
                   initial_go_price_eur_mwh: float = 8.0,
                   rate_increase: float = 0.0) -> pd.DataFrame:
    """
    Génère la trajectoire de prix des Garanties d'Origine (GO) françaises.

    Contexte économique :
    - Les GO sont l'équivalent français des RECs coréens.
    - Elles attestent qu'1 MWh a été produit à partir d'une source renouvelable.
    - Prix marché en 2024 : ~5-15 €/MWh (source : EEX, AIB)
    - Contrairement aux RECs coréens (80 000 KRW/MWh ≈ 57 €/MWh), les GO françaises
      sont beaucoup moins chères car il y a déjà un fort % de nucléaire dans le mix.
    - En France, les GOs sont souvent incluses dans les contrats PPA directement.

    Sources pour le prix des GO :
    - EEX : https://www.eex.com/en/market-data/environmental-markets
    - AIB : https://www.aib-net.org/facts/residual-mix
    - VERT : plateforme française de GO https://www.vertvertu.fr

    Paramètres :
    -----------
    initial_go_price_eur_mwh : float
        Prix initial des GO en €/MWh. Par défaut 8 €/MWh (valeur 2024 approx.)
        Note : Ce prix peut varier fortement selon la conjoncture.
    """
    go_values = {
        year: initial_go_price_eur_mwh * (1 + rate_increase) ** (year - start_year)
        for year in range(start_year, end_year + 1)
    }
    return pd.DataFrame({"value": go_values})


# ============================================================
# 4. DONNÉES GRID.CSV → build_france_grid_info()
# ============================================================

def build_france_grid_info(start_year: int = 2023, end_year: int = 2050,
                            eco2mix_filepath: str = None) -> pd.DataFrame:
    """
    Construit le fichier grid.csv équivalent pour la France.
    Remplace le fichier database/grid.csv du projet coréen.

    Colonnes produites (même format que l'original) :
    - solar_capex  : CAPEX solaire en €/MW (source ADEME/IRENA)
    - wind_capex   : CAPEX éolien offshore en €/MW
    - co2          : Intensité carbone du réseau français en gCO2/kWh
    - ren_share    : Part renouvelable dans le mix électrique français

    Sources de données :
    - CAPEX : ADEME "Futurs Énergétiques 2050", RTE Bilan Prévisionnel
              https://www.rte-france.com/analyses-tendances-et-prospectives/bilan-previsionnel-2050-futurs-energetiques
    - CO2 / ren_share : RTE eco2mix ODRE
              https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/

    INSTRUCTIONS pour les données réelles :
    ----------------------------------------
    1. Télécharger eco2mix depuis ODRE :
       URL : https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/
       → Filtrer par an, exporter en CSV
       → Colonnes utiles : "taux_co2" (gCO2/kWh), "taux_enr" (%)

    2. Pour les CAPEX, utiliser les trajectoires ADEME :
       - Scénario M0 (100% ENR) ou S3 (mix) selon le client
       - Valeurs typiques 2030 : PV sol ~650-750 k€/MW, éolien offshore ~2500-3000 k€/MW

    Paramètres :
    -----------
    eco2mix_filepath : str, optional
        Chemin vers un CSV eco2mix téléchargé depuis ODRE.
        Si None, utilise des projections de référence RTE.
    """
    years = range(start_year, end_year + 1)

    # --- CAPEX solaire (€/MW) ---
    # Source : ADEME Futurs Énergétiques 2050, Annexe CAPEX
    # Trajectoire de baisse : ~900 k€/MW en 2023 → ~500 k€/MW en 2050
    solar_capex_2023 = 900_000   # €/MW
    solar_capex_2050 = 500_000   # €/MW
    solar_capex = np.linspace(solar_capex_2023, solar_capex_2050, len(years))

    # --- CAPEX éolien offshore (€/MW) ---
    # Source : RTE Bilan Prévisionnel 2023
    # Trajectoire : ~3000 k€/MW en 2023 → ~1800 k€/MW en 2050
    wind_capex_2023 = 3_000_000  # €/MW
    wind_capex_2050 = 1_800_000  # €/MW
    wind_capex = np.linspace(wind_capex_2023, wind_capex_2050, len(years))

    # --- Intensité carbone (gCO2/kWh) ---
    # La France a déjà une intensité très faible (~55 gCO2/kWh en 2023) grâce au nucléaire
    # Trajectoire vers la neutralité : ~30 gCO2/kWh en 2050
    # Source : RTE eco2mix, données historiques
    co2_2023 = 55.0    # gCO2/kWh (France 2023, très bas vs Corée ~450 gCO2/kWh)
    co2_2050 = 20.0
    co2 = np.linspace(co2_2023, co2_2050, len(years))

    # --- Part renouvelable (%) ---
    # Source : RTE Futurs Énergétiques - scénario de référence
    # France 2023 : ~29% ENR (hors nucléaire) | avec nucléaire ~93% décarboné
    # Objectif 2030 : 40% ENR, 2050 : 100% ENR selon scénario M0
    ren_2023 = 0.29
    ren_2050 = 0.80   # Hypothèse conservatrice (vs 1.0 scénario M0)
    ren_share = np.linspace(ren_2023, ren_2050, len(years))

    if eco2mix_filepath and os.path.exists(eco2mix_filepath):
        print(f"[INFO] Chargement eco2mix depuis {eco2mix_filepath}")
        co2, ren_share = _load_eco2mix_historical(eco2mix_filepath, years, co2, ren_share)

    grid_df = pd.DataFrame({
        "solar_capex": solar_capex,
        "wind_capex": wind_capex,
        "co2": co2,
        "ren_share": ren_share,
    }, index=list(years))
    grid_df.index.name = "year"

    return grid_df


def _load_eco2mix_historical(filepath: str, years, co2_default, ren_default):
    """Charge les données eco2mix réelles depuis ODRE pour remplacer les estimations."""
    try:
        df = pd.read_csv(filepath, sep=";", parse_dates=["Date - Heure"])
        df["year"] = df["Date - Heure"].dt.year
        annual = df.groupby("year").agg({
            "Taux de CO2 (g/kWh)": "mean",
            "Taux d'EnR (%)": "mean"
        })
        co2_out = []
        ren_out = []
        for y in years:
            if y in annual.index:
                co2_out.append(annual.loc[y, "Taux de CO2 (g/kWh)"])
                ren_out.append(annual.loc[y, "Taux d'EnR (%)"] / 100)
            else:
                idx = list(years).index(y)
                co2_out.append(co2_default[idx])
                ren_out.append(ren_default[idx])
        return np.array(co2_out), np.array(ren_out)
    except Exception as e:
        print(f"[WARN] Impossible de charger eco2mix : {e}. Utilisation des valeurs par défaut.")
        return co2_default, ren_default
