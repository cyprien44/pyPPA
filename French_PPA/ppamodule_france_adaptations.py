"""
ppamodule_france_adaptations.py
================================
Patch à appliquer sur ppamodule.py pour l'adapter au contexte français.

Ce fichier ne remplace pas ppamodule.py en entier — il documente
les modifications EXACTES à apporter, section par section.

Les changements sont minimes car la logique PyPSA est universelle.
On modifie essentiellement :
1. Les références aux fichiers de données
2. Les unités (KRW → EUR)
3. Le filtre géographique des parcs éoliens
4. Le nom des industriels
"""

# ============================================================
# MODIFICATION 1 : Adapter les chemins de fichiers
# Dans run_model(), remplacer :
# ============================================================

CHANGES = """
# AVANT (code coréen) :
gridinf_df = pd.read_csv("database/grid.csv", index_col=0)
wind_df = pd.read_excel('database/wind_grid.xlsx', index_col=0)
filepath = "database/KEPCO.xlsx"
solarpattern_df = pd.read_sql_table('solar_patterns', 'sqlite:///database/solar_patterns.db')...
windpattern_df = pd.read_sql_table('wind_patterns', 'sqlite:///database/wind_patterns.db')...

# APRÈS (France) :
gridinf_df = pd.read_csv("database/grid_france.csv", index_col=0)
wind_df = pd.read_excel('database/wind_grid_france.xlsx', index_col=0)
filepath = "database/KEPCO_france.xlsx"
solarpattern_df = pd.read_sql_table('solar_patterns', 'sqlite:///database/solar_patterns.db')...
windpattern_df = pd.read_sql_table('wind_patterns', 'sqlite:///database/wind_patterns.db')...
# (solar et wind patterns gardent le même nom de table SQLite)
"""

# ============================================================
# MODIFICATION 2 : Devise EUR au lieu de KRW
# ============================================================

CURRENCY_CHANGES = """
# AVANT : currency_exchange est utilisé pour convertir KRW → USD
# self.currency_exchange = 1400 (KRW/USD)

# APRÈS : La France travaille en EUR. Deux options :
# Option A (simple) : Mettre currency_exchange = 1 (pas de conversion)
#   → Tous les prix restent en EUR
# Option B : Convertir EUR → USD si besoin de cohérence internationale
#   → currency_exchange = 1.08 (EUR/USD en 2024)

# Dans scenario_defaults.xlsx, changer :
# "Currency Exchange Rate (KRW/USD)" → valeur 1 (ou 1080 pour simuler millier d'EUR)

# IMPORTANT : Les LCOE et coûts KEPCO sont en EUR/MWh dans le cas français.
# Le code utilise ces valeurs de façon cohérente — pas besoin de conversion
# interne si tout est en EUR dès le départ.
"""

# ============================================================
# MODIFICATION 3 : Filtre géographique éolien
# ============================================================

WIND_FILTER_CHANGES = """
# AVANT (filtre sur régions coréennes) :
wind_df = wind_df[wind_df['admin_boundaries'].str.contains('인천광역시|인천 광역시|경기도|충청남도')]

# APRÈS (filtre sur régions françaises pertinentes pour votre client) :
# Exemple pour un industriel dans le Nord ou en Normandie :
wind_df = wind_df[wind_df['admin_boundaries'].str.contains(
    'Hauts-de-France|Normandie|Bretagne|Pays de la Loire|Occitanie'
)]

# Adaptez selon la localisation de votre client industriel.
# Un client à Dunkerque accède plus facilement aux éoliennes Manche Est.
# Un client à Fos-sur-Mer accède aux éoliennes Méditerranée.

# Le mapping équivalent de kr_to_en :
fr_region_mapping = {
    'Hauts-de-France': 'Nord',
    'Normandie': 'Normandie',
    'Bretagne': 'Bretagne',
    'Pays de la Loire': 'LoireAtlantique',
    'Occitanie': 'Mediterranee'
}
wind_df['admin_boundaries'] = wind_df['admin_boundaries'].replace(fr_region_mapping)
"""

# ============================================================
# MODIFICATION 4 : loads_config (nom des entreprises)
# ============================================================

LOAD_CHANGES = """
# AVANT :
loads_config = {
    "SK": get_int(scenario_defaults, "Load for SK (MW)", 3000),
    "Samsung": get_int(scenario_defaults, "Load for Samsung (MW)", 3000)
}

# APRÈS (exemple pour un industriel français unique) :
loads_config = {
    "Site_A": get_int(scenario_defaults, "Load for Site A (MW)", 200),
    "Site_B": get_int(scenario_defaults, "Load for Site B (MW)", 150),
}
# Ou pour une seule entité :
loads_config = {
    "Industriel": get_int(scenario_defaults, "Load (MW)", 350),
}

# Note : La charge en France est typiquement 50-500 MW pour un grand industriel,
# vs 3000 MW pour SK/Samsung. Les ordres de grandeur changent.
"""

# ============================================================
# MODIFICATION 5 : Sheet names KEPCO → TURPE
# ============================================================

SHEET_CHANGES = """
# AVANT :
sheet_options = ["HV_C_I", "HV_C_II", "HV_C_III"]
default_sheet = "HV_C_III"

# APRÈS :
sheet_options = ["HTB1", "HTB2", "HTB3"]
default_sheet = "HTB3"
# HTB3 = raccordement 63kV (le plus courant pour les industriels moyens)
# HTB2 = raccordement 225kV (grands sites)
# HTB1 = raccordement 400kV (très grands sites, rares)
"""

# ============================================================
# MODIFICATION 6 : Prix SMP (plancher LCOE)
# ============================================================

SMP_CHANGES = """
# AVANT : Initial SMP (KRW/MWh) = 167000
# Le SMP coréen sert de plancher pour les contrats PPA.

# APRÈS : En France, le plancher équivalent est le prix spot EPEX.
# Valeur de référence 2024 : ~60-80 €/MWh (période post-crise)
# Dans scenario_defaults.xlsx : "Initial SMP (KRW/MWh)" → 70000
# (Astuce : travailler en millièmes d'euro pour garder la même échelle)
# OU : changer l'unité directement dans le code si vous refactorisez.

# Valeur prudente : 50 €/MWh (correspond au coût marginal gaz cycle combiné)
"""

# ============================================================
# MODIFICATION 7 : REC → Garanties d'Origine (GO)
# ============================================================

REC_CHANGES = """
# Les RECs coréens (80 000 KRW/MWh ≈ 57 €/MWh) sont bien plus chers
# que les Garanties d'Origine françaises (5-15 €/MWh).

# Dans scenario_defaults.xlsx :
# "Initial REC Price (KRW/MWh)" → 10000 (= 10 €/MWh)
# "REC Reduction" → True (les GO tendent vers 0 avec la parité réseau)
# "REC Include in PPA Fees" → True (standard dans les PPA français)

# Logique inchangée dans le code — seule la valeur numérique change.
"""

# ============================================================
# MODIFICATION 8 : Prix CO2 (EU ETS vs ETS coréen)
# ============================================================

CO2_CHANGES = """
# BONNE NOUVELLE : Le NGFS carbon price scenario est GLOBAL.
# Le fichier database/NGFS_carbonprice.xlsx ne change pas !

# La seule différence :
# AVANT : carbonprice_grid *= self.currency_exchange  # KRW/kgCO2
# APRÈS : Pas de conversion nécessaire si on travaille en EUR directement.
#
# Vérifier que l'unité finale est cohérente :
# EU ETS 2024 : ~60-70 €/tCO2 = 0.060-0.070 €/kgCO2
# NGFS "Net Zero 2050" monte à ~150-250 USD/tCO2 en 2050.
# Si le fichier NGFS est en USD/tCO2, convertir : × 0.92 (EUR/USD) → €/tCO2
"""

# ============================================================
# RÉSUMÉ DES MODIFICATIONS À FAIRE DANS scenario_defaults.xlsx
# ============================================================

SCENARIO_DEFAULTS_CHANGES = """
Colonne à modifier dans scenario_defaults.xlsx :
+---------------------------------------+------------------+------------------+
| Paramètre                             | Valeur coréenne  | Valeur française |
+---------------------------------------+------------------+------------------+
| Load for SK (MW)                      | 3000             | 150-350          |
| Load for Samsung (MW)                 | 3000             | 0 (ou 2nd site)  |
| Currency Exchange Rate (KRW/USD)      | 1400             | 1                |
| Selected Sheet                        | HV_C_III         | HTB3             |
| Initial SMP (KRW/MWh)                 | 167000           | 60000 (60 €/MWh) |
| Initial REC Price (KRW/MWh)           | 80000            | 10000 (10 €/MWh) |
| Initial Year                          | 2023             | 2024             |
| Model Year                            | 2030             | 2030             |
| Analysis Target Year                  | 2050             | 2050             |
| Battery Capital Cost per MW (KRW)     | 1,000,000,000    | 600,000          |
| Battery Capital Cost per MWh (KRW)    | 250,000,000      | 150,000          |
| Power Density (W/m2)                  | 120              | 100 (standard FR)|
+---------------------------------------+------------------+------------------+

Note sur les unités :
- Le code travaille en "KRW" mais si currency_exchange=1, les valeurs
  deviennent directement en EUR. C'est la façon la plus simple d'adapter.
- Alternativement, renommer les paramètres dans l'UI Streamlit.
"""


if __name__ == "__main__":
    print("Ce fichier est une documentation de référence.")
    print("Appliquez les modifications manuellement dans ppamodule.py et app.py")
    print("\nChangements requis :")
    for section_name, content in [
        ("Chemins fichiers", CHANGES),
        ("Devise", CURRENCY_CHANGES),
        ("Filtre éolien", WIND_FILTER_CHANGES),
        ("Loads config", LOAD_CHANGES),
        ("Sheets TURPE", SHEET_CHANGES),
        ("Prix SMP", SMP_CHANGES),
        ("RECs → GOs", REC_CHANGES),
        ("Prix CO2", CO2_CHANGES),
        ("scenario_defaults.xlsx", SCENARIO_DEFAULTS_CHANGES),
    ]:
        print(f"\n{'='*50}")
        print(f"  {section_name}")
        print('='*50)
        print(content)
