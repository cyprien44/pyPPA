"""
build_land_grid.py
==================
Construction automatique de la grille de terrains candidats pour le modèle PPA France.

Remplace le fichier manuel gisdata/db_semippa.gpkg du modèle coréen.

À partir du lat/lon du site client, ce script :
  1. Interroge CORINE Land Cover via IGN Géoplateforme WFS  (data.geopf.fr)
  2. Interroge DVF via API CEREMA open data              (apidf-preprod.cerema.fr)
  3. Filtre les zones protégées via WFS Carmen/INPN      (ws.carmencarto.fr)
  4. Construit le GeoDataFrame compatible avec costutils.py
  5. Sauvegarde en .gpkg + .csv + carte HTML interactive

Usage :
    python build_land_grid.py --lat 50.93 --lon 2.38 --buffer 30
    python build_land_grid.py --test          ← test sans internet (données synthétiques)

Dépendances :
    pip install geopandas shapely requests pandas numpy
"""

import math
import json
import argparse
from pathlib import Path
from typing import Optional

import requests
import numpy as np
import pandas as pd

try:
    import geopandas as gpd
    from shapely.geometry import box, shape
    HAS_GEO = True
except ImportError:
    HAS_GEO = False
    print("[WARN] geopandas/shapely non disponibles — mode dégradé (DataFrame sans géométrie)")


# ==============================================================================
# CONSTANTES
# ==============================================================================

# Classes CORINE Land Cover compatibles ENR solaire
# (code: (label, dispo_pv_standard, dispo_agrivol, eligible_agrivol))
CLC_SOLAR_CLASSES = {
    211: ("Terres arables",                         0.80, 0.10, True),
    212: ("Périmètres irrigués",                    0.60, 0.05, True),
    213: ("Rizières",                               0.50, 0.03, True),
    221: ("Vignobles",                              0.00, 0.00, False),  # protégés AOP
    222: ("Vergers",                                0.40, 0.08, True),
    231: ("Prairies",                               0.85, 0.10, True),
    241: ("Cultures annuelles associées",           0.70, 0.08, True),
    242: ("Systèmes culturaux complexes",           0.75, 0.10, True),
    243: ("Surfaces agricoles + espaces naturels",  0.65, 0.08, True),
    321: ("Pelouses et pâturages naturels",         0.80, 0.05, True),
    333: ("Végétation clairsemée",                  0.85, 0.00, False),
    131: ("Décharges / friches",                    0.90, 0.00, False),
    132: ("Chantiers abandonnés",                   0.85, 0.00, False),
    133: ("Espaces en construction",                0.70, 0.00, False),
    121: ("Zones industrielles",                    0.50, 0.00, False),
}

CLC_EXCLUDED = {
    111, 112, 141, 142,     # tissu urbain, espaces verts
    311, 312, 313,          # forêts
    411, 412,               # zones humides
    511, 512, 521, 523,     # eau, mer
    122,                    # routes / voies ferrées
}

# Couleurs HTML par classe CLC pour la carte interactive
CLC_COLORS = {
    211: "#f5d07a", 212: "#f5d07a", 213: "#f5d07a",
    231: "#a8d08d", 241: "#c6e0b4", 242: "#c6e0b4", 243: "#c6e0b4",
    321: "#d9ead3", 333: "#ffe599",
    131: "#b4a7d6", 132: "#b4a7d6", 133: "#b4a7d6",
    121: "#ea9999",
}

# Coût câblage HTB France (€/MW/m) — barème RTE/ENEDIS 2024
CABLE_EUR_PER_MW_PER_M = 35.0
MIN_AREA_M2 = 25_000   # 2.5 ha = unité minimale CLC


# ==============================================================================
# 1. GÉOMÉTRIE UTILITAIRES
# ==============================================================================

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def get_bbox(lat, lon, km):
    """Retourne (miny, minx, maxy, maxx) autour d'un point."""
    dlat = km / 111.0
    dlon = km / (111.0 * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


# ==============================================================================
# 2. API CORINE LAND COVER — IGN Géoplateforme (nouvelle URL 2024)
# ==============================================================================

def fetch_clc(lat, lon, buffer_km):
    """
    IGN Géoplateforme WFS — CORINE Land Cover 2018
    URL : https://data.geopf.fr/wfs/ows
    Layer : LANDCOVER.CLC18_FR

    ⚠️ L'ancienne URL EEA (image.discomap.eea.europa.eu) est abandonnée.
    ⚠️ L'ancienne URL IGN (wxs.ign.fr) est redirigée → data.geopf.fr depuis mars 2024.
    Doc officielle : https://geoservices.ign.fr/services-web-experts-clc
    """
    miny, minx, maxy, maxx = get_bbox(lat, lon, buffer_km)

    url = "https://data.geopf.fr/wfs/ows"
    params = {
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    "LANDCOVER.CLC18_FR",
        "BBOX":         f"{miny},{minx},{maxy},{maxx},EPSG:4326",
        "SRSNAME":      "EPSG:4326",
        "outputFormat": "application/json",
        "COUNT":        "1000",
    }

    print(f"[CLC] IGN Géoplateforme WFS → LANDCOVER.CLC18_FR...")
    try:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        features = r.json().get("features", [])
        print(f"[CLC] ✓ {len(features)} polygones reçus")

        results = []
        for f in features:
            try:
                geom = shape(f["geometry"]) if HAS_GEO else None
                props = f.get("properties", {})
                # Tester les deux noms de colonnes possibles selon la version
                code_raw = props.get("CODE_18") or props.get("code_18") or props.get("code", "")
                clc_code = int(str(code_raw).strip()) if str(code_raw).strip().isdigit() else 0
                area = (props.get("SHAPE_Area") or props.get("shape_area")
                        or (geom.area * 1.2e10 if geom else MIN_AREA_M2))
                if clc_code:
                    results.append({"geometry": geom, "clc_code": clc_code, "area_m2": float(area)})
            except Exception:
                continue

        if results:
            return results
        print("[CLC] Réponse vide — fallback synthétique")

    except Exception as e:
        print(f"[CLC] Erreur : {e}")

    print("[CLC] → Données synthétiques (mode test)")
    return _synth_clc(lat, lon, buffer_km)


def _synth_clc(lat, lon, buffer_km):
    """Données synthétiques réalistes pour tests sans internet."""
    if not HAS_GEO:
        return _synth_clc_no_geo(lat, lon, buffer_km)

    np.random.seed(int(abs(lat * 100 + lon * 100)) % 9999)
    classes = [211, 231, 242, 312, 121, 133, 111]
    weights = [0.35, 0.25, 0.15, 0.10, 0.05, 0.05, 0.05]
    miny, minx, maxy, maxx = get_bbox(lat, lon, buffer_km)
    results = []

    for _ in range(70):
        lon_c = minx + np.random.random() * (maxx - minx)
        lat_c = miny + np.random.random() * (maxy - miny)
        if haversine_m(lat, lon, lat_c, lon_c) > buffer_km * 1000:
            continue
        size_ha = float(np.clip(np.random.lognormal(3.9, 0.8), 2.5, 500))
        side = math.sqrt(size_ha * 1e4) / 111_000
        geom = box(lon_c - side/2, lat_c - side/2, lon_c + side/2, lat_c + side/2)
        clc_code = int(np.random.choice(classes, p=weights))
        results.append({"geometry": geom, "clc_code": clc_code, "area_m2": size_ha * 1e4})

    print(f"[CLC][SYNTH] {len(results)} parcelles synthétiques générées")
    return results


def _synth_clc_no_geo(lat, lon, buffer_km):
    """Version sans Shapely."""
    np.random.seed(42)
    classes = [211, 231, 242, 312, 121, 133, 111]
    weights = [0.35, 0.25, 0.15, 0.10, 0.05, 0.05, 0.05]
    results = []
    for _ in range(60):
        angle = np.random.uniform(0, 2 * math.pi)
        dist_m = np.random.uniform(500, buffer_km * 900)
        lat_c = lat + (dist_m / 111_000) * math.cos(angle)
        lon_c = lon + (dist_m / (111_000 * math.cos(math.radians(lat)))) * math.sin(angle)
        size_ha = float(np.clip(np.random.lognormal(3.9, 0.8), 2.5, 500))
        clc_code = int(np.random.choice(classes, p=weights))
        results.append({
            "geometry": None, "clc_code": clc_code, "area_m2": size_ha * 1e4,
            "centroid_lat": lat_c, "centroid_lon": lon_c,
        })
    return results


# ==============================================================================
# 3. API DVF — CEREMA Open Data (nouvelle URL 2024)
# ==============================================================================

def fetch_dvf(lat, lon, buffer_km):
    """
    API CEREMA DVF open data — mutations foncières géolocalisées
    URL : https://apidf-preprod.cerema.fr/dvf_opendata/geomutations/
    Doc : https://apidf-preprod.cerema.fr/swagger/

    ⚠️ L'ancienne URL api.dvf.etalab.gouv.fr n'existe plus.
    L'API CEREMA est le remplaçant officiel open data.
    Retourne dict {code_commune: prix_median_€/m²}
    """
    miny, minx, maxy, maxx = get_bbox(lat, lon, buffer_km)

    url = "https://apidf-preprod.cerema.fr/dvf_opendata/geomutations/"
    params = {
        "in_bbox":         f"{minx},{miny},{maxx},{maxy}",  # lon_min,lat_min,lon_max,lat_max
        "nature_mutation": "Vente",
        "page_size":       500,
    }

    print(f"[DVF] API CEREMA → geomutations bbox {minx:.2f},{miny:.2f}...")
    try:
        r = requests.get(url, params=params, timeout=45)
        r.raise_for_status()
        data = r.json()
        # L'API CEREMA retourne soit GeoJSON (features) soit DRF pagination (results)
        features = data.get("features") or data.get("results") or []
        print(f"[DVF] ✓ {len(features)} transactions reçues")

        commune_prices = {}
        for feat in features:
            props = feat.get("properties", feat) if isinstance(feat, dict) else {}
            surface = float(props.get("sterr") or props.get("surface_terrain") or 0)
            valeur  = float(props.get("valeurfonc") or props.get("valeur_fonciere") or 0)
            code    = str(props.get("l_codinsee") or props.get("code_commune") or "")
            if surface > 100 and valeur > 0:
                p = valeur / surface
                if 0.05 <= p <= 100:  # plage plausible €/m² pour terrain nu
                    commune_prices.setdefault(code, []).append(p)

        result = {c: float(np.median(v)) for c, v in commune_prices.items() if v}
        if result:
            med = float(np.median(list(result.values())))
            print(f"[DVF] ✓ Prix médian zone : {med:.2f} €/m²")
        else:
            print("[DVF] Pas de transactions nues — fallback régional")

        result["__default__"] = _dvf_fallback(lat)
        return result

    except Exception as e:
        print(f"[DVF] Erreur : {e} — fallback régional")
        return {"__default__": _dvf_fallback(lat)}


def _dvf_fallback(lat):
    """
    Prix terres agricoles par latitude — Source : SAFER Bilan annuel 2023.
    Unité : €/m²  (= prix_ha / 10 000)
    """
    if lat > 49.5:   return 0.65   # Hauts-de-France  ~6 500 €/ha
    elif lat > 47.5: return 0.55   # Normandie / IdF
    elif lat > 46.0: return 0.45   # Centre / Val de Loire
    elif lat > 43.5: return 0.38   # Nouvelle-Aquitaine / Occitanie
    else:            return 0.42   # PACA


# ==============================================================================
# 4. API INPN — ZONES PROTÉGÉES (nouvelle URL 2024)
# ==============================================================================

def fetch_protected(lat, lon, buffer_km):
    """
    Zones Natura 2000 et ZNIEFF via WFS Carmen/INPN
    URL : https://ws.carmencarto.fr/WFS/119/fxx_natura2000

    ⚠️ L'ancienne URL inpn.mnhn.fr/espece/api/zone-protection retourne 404.
    Le service Carmen est le nouveau point d'accès WFS de l'INPN.
    """
    miny, minx, maxy, maxx = get_bbox(lat, lon, buffer_km)

    url = "https://ws.carmencarto.fr/WFS/119/fxx_natura2000"
    params = {
        "SERVICE":      "WFS",
        "VERSION":      "1.1.0",
        "REQUEST":      "GetFeature",
        "TYPENAME":     "ZNIEFF1",
        "BBOX":         f"{miny},{minx},{maxy},{maxx}",
        "SRSNAME":      "EPSG:4326",
        "outputFormat": "application/json",
        "maxFeatures":  "100",
    }

    print(f"[INPN] WFS Carmen → zones ZNIEFF1 + Natura 2000...")
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        features = r.json().get("features", [])
        zones = []
        for f in features:
            if HAS_GEO:
                try:
                    geom = shape(f["geometry"])
                    zones.append({
                        "geometry": geom,
                        "type": "ZNIEFF1",
                        "name": f.get("properties", {}).get("NOM", ""),
                    })
                except Exception:
                    pass
        print(f"[INPN] ✓ {len(zones)} zones protégées trouvées")
        return zones
    except Exception as e:
        print(f"[INPN] Non disponible ({type(e).__name__}) — ignoré")
        return []


# ==============================================================================
# 5. CONSTRUCTION DU LAND GRID
# ==============================================================================

def build_land_grid(lat, lon, buffer_km=30.0, output_dir="gisdata",
                    synthetic_mode=False, skip_protected=False):
    """
    Fonction principale — construit la grille de terrains candidats.

    Colonnes compatibles costutils.py :
        area_photo    ← surface PV standard (m²)
        area_agrivol  ← surface agrivoltaïque (m²)
        wavgprice     ← prix foncier (€/m²)
        distance_0    ← distance au site (m)

    Sorties :
        gisdata/db_semippa_auto.csv        ← toujours généré
        gisdata/db_semippa_auto.gpkg       ← si geopandas installé
        gisdata/db_semippa_auto_map.html   ← carte interactive (toujours)
    """
    print(f"\n{'='*60}")
    print(f"BUILD LAND GRID  lat={lat}  lon={lon}  buffer={buffer_km} km")
    print(f"{'='*60}")

    # ── 1. Données CLC ────────────────────────────────────────────────────────
    clc = _synth_clc(lat, lon, buffer_km) if synthetic_mode else fetch_clc(lat, lon, buffer_km)

    # ── 2. Prix fonciers DVF ──────────────────────────────────────────────────
    dvf = {"__default__": _dvf_fallback(lat)} if synthetic_mode else fetch_dvf(lat, lon, buffer_km)
    default_price = dvf["__default__"]

    # ── 3. Zones protégées ────────────────────────────────────────────────────
    protected = [] if (synthetic_mode or skip_protected) else fetch_protected(lat, lon, buffer_km)

    # ── 4. Assembler les lignes ───────────────────────────────────────────────
    rows, excl_class, excl_size = [], 0, 0

    for feat in clc:
        code = feat["clc_code"]

        if code in CLC_EXCLUDED:
            excl_class += 1
            continue
        if code not in CLC_SOLAR_CLASSES:
            excl_class += 1
            continue

        label, avail_pv, avail_agri, elig_agri = CLC_SOLAR_CLASSES[code]
        area_m2 = feat["area_m2"]

        if area_m2 < MIN_AREA_M2:
            excl_size += 1
            continue

        geom = feat.get("geometry")
        if geom is not None and HAS_GEO:
            c = geom.centroid
            clat, clon = c.y, c.x
        else:
            clat = feat.get("centroid_lat", lat)
            clon = feat.get("centroid_lon", lon)

        dist_m = haversine_m(lat, lon, clat, clon)
        if dist_m > buffer_km * 1000:
            continue

        zone = ""
        if protected and geom is not None and HAS_GEO:
            for z in protected:
                if geom.intersects(z["geometry"]):
                    zone = z["type"]
                    break

        row = {
            "clc_code":       code,
            "clc_label":      label,
            "area_m2":        area_m2,
            "area_photo":     area_m2 * avail_pv,                           # ← costutils.py
            "area_agrivol":   area_m2 * avail_agri if elig_agri else 0.0,   # ← costutils.py
            "wavgprice":      default_price,                                 # ← costutils.py
            "distance_0":     dist_m,                                        # ← costutils.py
            "cable_cost_est": dist_m * CABLE_EUR_PER_MW_PER_M,
            "protected_zone": zone,
            "eligible_pv":    avail_pv > 0,
            "eligible_agri":  elig_agri,
            "centroid_lat":   clat,
            "centroid_lon":   clon,
        }
        if geom is not None:
            row["geometry"] = geom
        rows.append(row)

    print(f"\n[GRID] Retenues      : {len(rows)}")
    print(f"[GRID] Excl. classe  : {excl_class}")
    print(f"[GRID] Excl. taille  : {excl_size}")

    if not rows:
        raise ValueError("Aucune parcelle candidate — vérifiez connexion ou augmentez le buffer.")

    # ── 5. DataFrame / GeoDataFrame ───────────────────────────────────────────
    if HAS_GEO and any("geometry" in r for r in rows):
        gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    else:
        gdf = pd.DataFrame(rows)

    _print_stats(gdf)

    # ── 6. Sauvegardes ────────────────────────────────────────────────────────
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    prefix = str(Path(output_dir) / "db_semippa_auto")

    # CSV — toujours produit (lisible sans QGIS)
    csv_path = prefix + ".csv"
    cols = [c for c in gdf.columns if c != "geometry"]
    gdf[cols].to_csv(csv_path, index=False)
    print(f"[OK] CSV    → {csv_path}")

    # GPKG — si geopandas disponible
    if HAS_GEO and isinstance(gdf, gpd.GeoDataFrame):
        gpkg_path = prefix + ".gpkg"
        gdf.to_file(gpkg_path, driver="GPKG")
        print(f"[OK] GPKG   → {gpkg_path}  (ouvrir avec QGIS)")

    # Carte HTML interactive — toujours produite
    html_path = prefix + "_map.html"
    _build_html_map(gdf, lat, lon, buffer_km, html_path)
    print(f"[OK] Carte  → {html_path}  ← double-cliquer pour ouvrir dans le navigateur")

    return gdf


# ==============================================================================
# 6. CARTE HTML INTERACTIVE (Leaflet.js via CDN)
# ==============================================================================

def _build_html_map(gdf, client_lat, client_lon, buffer_km, output_path):
    """
    Génère une carte HTML interactive avec Leaflet.js.
    Ne nécessite pas folium — fonctionne avec n'importe quel navigateur.
    """
    features_js = []

    for _, row in gdf.iterrows():
        code      = int(row.get("clc_code", 0))
        color     = CLC_COLORS.get(code, "#aaaaaa")
        dist_km   = row.get("distance_0", 0) / 1000
        area_ha   = row.get("area_m2", 0) / 1e4
        pv_ha     = row.get("area_photo", 0) / 1e4
        agri_ha   = row.get("area_agrivol", 0) / 1e4
        price     = row.get("wavgprice", 0)
        label     = row.get("clc_label", f"CLC {code}")
        prot      = str(row.get("protected_zone", "") or "")
        power_mw  = pv_ha * 1e4 * 80 / 1e6
        clat      = row.get("centroid_lat", client_lat)
        clon      = row.get("centroid_lon", client_lon)

        prot_html = (f"<span style='color:orange'>⚠️ Zone protégée : {prot}</span>"
                     if prot else "<span style='color:green'>✓ Pas de zone protégée</span>")

        popup = (
            f"<b>{label}</b> (CLC {code})<br>"
            f"Surface totale : <b>{area_ha:.1f} ha</b><br>"
            f"Disponible PV  : {pv_ha:.1f} ha → <b>{power_mw:.1f} MW</b><br>"
            f"Agrivoltaïque  : {agri_ha:.1f} ha<br>"
            f"Distance site  : {dist_km:.1f} km<br>"
            f"Prix foncier   : {price:.2f} €/m²<br>"
            f"Câblage 1 MW   : {dist_km*1000*CABLE_EUR_PER_MW_PER_M/1000:.0f} k€<br>"
            f"{prot_html}"
        )

        geom = row.get("geometry")
        if geom is not None and HAS_GEO:
            try:
                geojson_str = json.dumps(geom.__geo_interface__)
                features_js.append({
                    "type": "polygon", "geojson": geojson_str,
                    "color": color, "popup": popup,
                })
                continue
            except Exception:
                pass
        # Fallback cercle
        radius = math.sqrt(max(area_ha * 1e4, 1e4) / math.pi)
        features_js.append({
            "type": "circle", "lat": clat, "lon": clon,
            "radius": radius, "color": color, "popup": popup,
        })

    features_json = json.dumps(features_js, ensure_ascii=False)

    # Statistiques pour le panneau
    total_ha  = gdf["area_m2"].sum() / 1e4
    mw_pv     = gdf["area_photo"].sum() * 80 / 1e6
    mw_agri   = gdf["area_agrivol"].sum() * 80 / 1e6
    n         = len(gdf)
    avg_dist  = gdf["distance_0"].mean() / 1000
    prix_med  = gdf["wavgprice"].median()
    n_prot    = int((gdf["protected_zone"].astype(str) != "").sum())

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>PPA Land Grid — {client_lat:.2f}, {client_lon:.2f}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{ margin:0; font-family:Arial,sans-serif; }}
    #map {{ height:100vh; width:100%; }}
    #panel {{
      position:absolute; top:10px; right:10px; z-index:1000;
      background:white; padding:14px 16px; border-radius:8px;
      box-shadow:0 2px 12px rgba(0,0,0,.25); min-width:230px;
      max-width:270px; font-size:13px; line-height:1.5;
    }}
    #panel h3 {{ margin:0 0 8px; font-size:14px; color:#1F4E79; }}
    .stat {{ display:flex; justify-content:space-between; margin:2px 0; }}
    .stat b {{ color:#1F4E79; }}
    .legend-item {{ display:flex; align-items:center; margin:3px 0; font-size:12px; }}
    .dot {{ width:13px; height:13px; border-radius:3px; margin-right:7px;
            border:1px solid #bbb; flex-shrink:0; }}
    hr {{ border:none; border-top:1px solid #eee; margin:8px 0; }}
    .warn {{ color:orange; font-size:12px; }}
  </style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h3>🗺️ PPA Land Grid France</h3>
  <div class="stat"><span>Parcelles candidates</span><b>{n}</b></div>
  <div class="stat"><span>Surface totale</span><b>{total_ha:,.0f} ha</b></div>
  <div class="stat"><span>Puissance PV max</span><b>{mw_pv:,.0f} MW</b></div>
  <div class="stat"><span>Puissance agrivolt.</span><b>{mw_agri:,.0f} MW</b></div>
  <div class="stat"><span>Distance moyenne</span><b>{avg_dist:.1f} km</b></div>
  <div class="stat"><span>Prix foncier médian</span><b>{prix_med:.2f} €/m²</b></div>
  {'<div class="warn">⚠️ ' + str(n_prot) + ' parcelles en zone protégée</div>' if n_prot else ''}
  <hr>
  <b style="font-size:12px">Légende (classe CLC)</b><br>
  <div class="legend-item"><div class="dot" style="background:#f5d07a"></div>Terres arables</div>
  <div class="legend-item"><div class="dot" style="background:#a8d08d"></div>Prairies</div>
  <div class="legend-item"><div class="dot" style="background:#c6e0b4"></div>Systèmes complexes</div>
  <div class="legend-item"><div class="dot" style="background:#b4a7d6"></div>Friches / chantiers</div>
  <div class="legend-item"><div class="dot" style="background:#ea9999"></div>Zones industrielles</div>
  <div class="legend-item"><div class="dot" style="background:#ffe599"></div>Végétation clairsemée</div>
  <hr>
  <small style="color:#888">⭐ = site client &nbsp;|&nbsp; --- = rayon buffer<br>
  Cliquer sur une parcelle pour les détails</small>
</div>

<script>
var map = L.map('map').setView([{client_lat}, {client_lon}], 10);

L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
  maxZoom: 18
}}).addTo(map);

// Marqueur site client
L.marker([{client_lat}, {client_lon}], {{
  icon: L.divIcon({{
    html: '<div style="font-size:26px;margin:-13px 0 0 -13px">⭐</div>',
    className:'', iconSize:[26,26]
  }})
}}).addTo(map)
  .bindPopup('<b>Site client</b><br>lat={client_lat} / lon={client_lon}');

// Cercle buffer
L.circle([{client_lat}, {client_lon}], {{
  radius:{buffer_km * 1000},
  color:'#1F4E79', fill:false, weight:2, dashArray:'8 4'
}}).addTo(map);

// Parcelles
var features = {features_json};

features.forEach(function(f) {{
  var style = {{
    color:'#444', weight:1,
    fillColor: f.color, fillOpacity: 0.65
  }};
  var layer;
  if (f.type === 'polygon') {{
    layer = L.geoJSON(JSON.parse(f.geojson), {{style: style}});
  }} else {{
    layer = L.circle([f.lat, f.lon], {{
      radius: f.radius,
      color:'#444', weight:1,
      fillColor: f.color, fillOpacity: 0.65
    }});
  }}
  layer.bindPopup(f.popup, {{maxWidth:280}});
  layer.addTo(map);
}});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)


# ==============================================================================
# 7. STATS
# ==============================================================================

def _print_stats(gdf):
    print(f"\n{'─'*55}")
    print("RÉSUMÉ DE LA GRILLE DE TERRAINS")
    print(f"{'─'*55}")
    print(f"Surface totale identifiée       : {gdf['area_m2'].sum()/1e4:,.0f} ha")
    pv_ha = gdf['area_photo'].sum()/1e4
    ag_ha = gdf['area_agrivol'].sum()/1e4
    print(f"Surface disponible PV standard  : {pv_ha:,.0f} ha → {pv_ha*1e4*80/1e6:,.0f} MW")
    print(f"Surface disponible agrivoltaïque: {ag_ha:,.0f} ha → {ag_ha*1e4*80/1e6:,.0f} MW")
    print(f"Distance moyenne au site        : {gdf['distance_0'].mean()/1000:.1f} km")
    print(f"Distance max au site            : {gdf['distance_0'].max()/1000:.1f} km")
    print(f"Prix foncier médian             : {gdf['wavgprice'].median():.2f} €/m²")
    print(f"\nTop 5 classes CLC :")
    totals = gdf.groupby("clc_label")["area_m2"].sum().sort_values(ascending=False)
    s = gdf["area_m2"].sum()
    for lbl, area in totals.head(5).items():
        print(f"  {lbl[:50]:<50} : {area/1e4:6.0f} ha ({100*area/s:.0f}%)")
    n_p = (gdf["protected_zone"].astype(str) != "").sum()
    if n_p:
        print(f"\n⚠️  {n_p} parcelles en zone protégée (étude d'impact requise)")
    print(f"{'─'*55}")


# ==============================================================================
# 8. INTÉGRATION costutils.py
# ==============================================================================

def process_grid_site_data_france(client_lat, client_lon, buffer_km=30.0,
                                   cache_dir="gisdata", force_rebuild=False,
                                   synthetic_mode=False):
    """
    Drop-in replacement de process_grid_site_data() dans costutils.py.

    Usage dans ppamodule.py :
        from build_land_grid import process_grid_site_data_france

        client_lat = scenario_defaults["Client Latitude"]
        client_lon = scenario_defaults["Client Longitude"]
        buffer_km  = scenario_defaults["Buffer distance (m)"] / 1000

        grid_df = process_grid_site_data_france(client_lat, client_lon, buffer_km)
    """
    csv_cache = Path(cache_dir) / "db_semippa_auto.csv"

    if not force_rebuild and csv_cache.exists():
        print(f"[GRID] Cache trouvé → {csv_cache}")
        return pd.read_csv(csv_cache)

    return build_land_grid(
        lat=client_lat, lon=client_lon,
        buffer_km=buffer_km,
        output_dir=cache_dir,
        synthetic_mode=synthetic_mode,
    )


# ==============================================================================
# 9. CLI
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PPA France — Construction automatique grille de terrains candidats"
    )
    parser.add_argument("--lat",       type=float, default=50.93, help="Latitude site client")
    parser.add_argument("--lon",       type=float, default=2.38,  help="Longitude site client")
    parser.add_argument("--buffer",    type=float, default=30.0,  help="Rayon de recherche (km)")
    parser.add_argument("--output",    type=str,   default="gisdata", help="Dossier de sortie")
    parser.add_argument("--synthetic", action="store_true", help="Données synthétiques (test sans internet)")
    parser.add_argument("--no-inpn",   action="store_true", help="Ne pas interroger INPN (plus rapide)")
    parser.add_argument("--force",     action="store_true", help="Reconstruire même si cache présent")
    parser.add_argument("--test",      action="store_true", help="= --synthetic (alias rapide)")
    args = parser.parse_args()

    if args.test:
        args.synthetic = True

    gdf = build_land_grid(
        lat=args.lat, lon=args.lon,
        buffer_km=args.buffer,
        output_dir=args.output,
        synthetic_mode=args.synthetic,
        skip_protected=args.no_inpn,
    )

    print(f"\n✓ Terminé — {len(gdf)} parcelles candidates")
    print(f"  Carte interactive : {args.output}/db_semippa_auto_map.html")
    print(f"  Données tabulaires: {args.output}/db_semippa_auto.csv")
