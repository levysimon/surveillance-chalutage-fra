# Ce script détecte les événements de pêche suspicieux (>= 3 pings et >= 2h
# passés dans une zone interdite pendant la période de fermeture) pour deux
# zones successives : d'abord la zone ouest (bande 90-100m, frontière
# franco-espagnole, chargée depuis un GeoJSON), puis la zone est (FRA,
# polygone défini en dur). Chaque partie exporte ses propres fichiers
# (pings complets, résumé des infractions, bilan texte) dans son propre
# dossier de sortie.

import json
import math
import os

import pandas as pd
from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union

# =============================================================================
# PARTIE 1 : ZONE OUEST (90-100m, frontière franco-espagnole)
# =============================================================================

INPUT_FILE = os.path.join(
    "data", "evenement de peche", "pings_evenements_peche.csv"
)

GEOJSON_ZONE = os.path.join("data", "profondeur", "zone_90_100m_frontiere_es_v2.geojson")

OUTPUT_DIR = os.path.join("data", "infractions ouest")
os.makedirs(OUTPUT_DIR, exist_ok=True)

FICHIER_PINGS = os.path.join(
    OUTPUT_DIR, "pings_complets_evenements_suspicieux.csv"
)
FICHIER_RESUME = os.path.join(OUTPUT_DIR, "resume_infractions.csv")
FICHIER_TXT = os.path.join(OUTPUT_DIR, "bilan_bateaux.txt")

BUFFER_M = 500.0

DATE_DEBUT_LIMITE = pd.Timestamp("2020-01-01", tz="UTC")
MOIS_FERMETURE = (1, 2, 3, 4, 9, 10, 11, 12)


def load_zone_polygon(path: str):
    with open(path, "r", encoding="utf-8") as f:
        geojson = json.load(f)

    geoms = [shape(feature["geometry"]) for feature in geojson["features"]]
    zone_lonlat = unary_union(geoms)
    print(f"📐 Zone chargée : {len(geoms)} polygone(s) fusionné(s) depuis {path}")
    return zone_lonlat


_ZONE_LONLAT = load_zone_polygon(GEOJSON_ZONE)

_CENTROID = _ZONE_LONLAT.centroid
_LAT0 = _CENTROID.y
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(_LAT0))


def lonlat_to_xy(lon: float, lat: float) -> tuple:
    return lon * _M_PER_DEG_LON, lat * _M_PER_DEG_LAT


def _project_polygon(geom):
    from shapely.ops import transform
    return transform(lambda lon, lat: lonlat_to_xy(lon, lat), geom)


_ZONE_XY = _project_polygon(_ZONE_LONLAT)
_ZONE_XY_BUFFERED = _ZONE_XY.buffer(-BUFFER_M)


def in_fra_zone_ouest(lat: float, lon: float) -> bool:
    if pd.isna(lat) or pd.isna(lon):
        return False
    x, y = lonlat_to_xy(lon, lat)
    return _ZONE_XY_BUFFERED.contains(Point(x, y))


def in_season_ouest(dt) -> bool:
    return (dt >= DATE_DEBUT_LIMITE) and (dt.month in MOIS_FERMETURE)


print(f"📥 Chargement de {INPUT_FILE}...")
df = pd.read_csv(INPUT_FILE)
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

print("🔍 Vérification des intersections avec la zone 90-100m frontière ES (>= 2020)...")
df["est_problematique"] = df.apply(
    lambda row: in_fra_zone_ouest(row["lat"], row["lon"])
    and in_season_ouest(row["timestamp"]),
    axis=1,
)

df = df.sort_values(by=["event_id_unique", "timestamp"]).reset_index(drop=True)

changement = df["est_problematique"] != df.groupby("event_id_unique")[
    "est_problematique"
].shift(1)
df["block_id"] = changement.groupby(df["event_id_unique"]).cumsum()

blocs_infraction = (
    df[df["est_problematique"]]
    .groupby(["event_id_unique", "block_id"])
    .agg(
        nb_pings=("timestamp", "size"),
        debut_bloc=("timestamp", "min"),
        fin_bloc=("timestamp", "max"),
    )
    .reset_index()
)

blocs_infraction["duree_bloc_h"] = (
    blocs_infraction["fin_bloc"] - blocs_infraction["debut_bloc"]
).dt.total_seconds() / 3600.0

ids_suspicieux = blocs_infraction[
    (blocs_infraction["nb_pings"] >= 3) & (blocs_infraction["duree_bloc_h"] >= 2.0)
]["event_id_unique"].unique()

df_suspicieux = df[df["event_id_unique"].isin(ids_suspicieux)].copy()
df_suspicieux = df_suspicieux.drop(columns=["block_id"])

print(
    f"⚠️ {len(ids_suspicieux)} événement(s) de pêche en infraction détecté(s)"
    " (>= 3 pings ET >= 2h)."
)

df_in_zone = df_suspicieux[df_suspicieux["est_problematique"]]

resume = (
    df_in_zone.groupby(["bateau", "event_id_unique"])
    .agg(
        date_debut_infraction=("timestamp", "min"),
        date_fin_infraction=("timestamp", "max"),
        nombre_points_en_zone=("timestamp", "size"),
    )
    .reset_index()
)

resume["duree_infraction_h"] = (
    resume["date_fin_infraction"] - resume["date_debut_infraction"]
).dt.total_seconds() / 3600.0
resume["duree_infraction_h"] = resume["duree_infraction_h"].round(2)
resume["fichier_source"] = os.path.basename(INPUT_FILE)
resume = resume.rename(columns={"bateau": "nom_bateau"})

colonnes_export = [
    "nom_bateau",
    "date_debut_infraction",
    "date_fin_infraction",
    "duree_infraction_h",
    "nombre_points_en_zone",
    "fichier_source",
    "event_id_unique",
]
resume = resume[colonnes_export]

df_suspicieux.to_csv(FICHIER_PINGS, index=False)
print(f"💾 Fichier exporté : {FICHIER_PINGS}")

resume.to_csv(FICHIER_RESUME, index=False)
print(f"💾 Fichier exporté : {FICHIER_RESUME}")

df_res_stats = resume.copy()
df_res_stats["date_debut"] = pd.to_datetime(df_res_stats["date_debut_infraction"])
df_res_stats["date_fin"] = pd.to_datetime(df_res_stats["date_fin_infraction"])
df_res_stats["duree_heures"] = (
    df_res_stats["date_fin"] - df_res_stats["date_debut"]
).dt.total_seconds() / 3600.0

total_infractions_global = len(df_res_stats)
total_heures_global = df_res_stats["duree_heures"].sum()

synthèse_bateau = (
    df_res_stats.groupby("nom_bateau")
    .agg(
        nombre_infractions=("nom_bateau", "count"),
        total_heures=("duree_heures", "sum"),
    )
    .reset_index()
)
synthèse_bateau["total_heures"] = synthèse_bateau["total_heures"].round(2)
synthèse_bateau = synthèse_bateau.sort_values(
    by="total_heures", ascending=False
)

with open(FICHIER_TXT, "w", encoding="utf-8") as f:
    bilan = resume["nom_bateau"].value_counts().sort_index()
    f.write("--- NOMBRE D'ÉVÉNEMENTS SUSPICIEUX PAR BATEAU ---\n\n")
    f.write(bilan.to_string())
    f.write("\n\n" + "=" * 50 + "\n\n")

    f.write("=" * 50 + "\n")
    f.write("📈 BILAN GLOBAL DE LA FLOTTE\n")
    f.write("=" * 50 + "\n")
    f.write("Zone : bande 90-100m, frontière franco-espagnole "
             "(Art. 5 ter al.2, JORF 26/12/2019)\n")
    f.write("Période d'interdiction : 1er janvier - 30 avril "
             "et 1er septembre - 31 décembre, depuis le 1er janvier 2020\n")
    f.write(f"• Nombre total d'infractions : {total_infractions_global}\n")
    f.write(f"• Nombre total d'heures cumulées : {total_heures_global:.2f} h\n")
    f.write("=" * 50 + "\n\n")

    f.write("📊 SYNTHÈSE DÉTAILLÉE PAR BATEAU :\n\n")
    f.write(synthèse_bateau.to_string(index=False))
    f.write("\n")

print(f"💾 Fichier exporté : {FICHIER_TXT}")

# =============================================================================
# PARTIE 2 : ZONE EST (FRA)
# =============================================================================

INPUT_FILE = os.path.join(
    "data", "evenement de peche", "pings_evenements_peche.csv"
)

OUTPUT_DIR = os.path.join("data", "infractions est")
os.makedirs(OUTPUT_DIR, exist_ok=True)

FICHIER_PINGS = os.path.join(
    OUTPUT_DIR, "pings_complets_evenements_suspicieux.csv"
)
FICHIER_RESUME = os.path.join(OUTPUT_DIR, "resume_infractions.csv")
FICHIER_TXT = os.path.join(OUTPUT_DIR, "bilan_bateaux.txt")

BUFFER_M = 100.0
DATE_DEBUT_LIMITE = pd.Timestamp("2020-01-01", tz="UTC")


def dms_to_dd(deg, minutes):
    return deg + minutes / 60


FRA_POLYGON_LATLON = [
    (dms_to_dd(42, 40), dms_to_dd(4, 20)),
    (dms_to_dd(42, 40), dms_to_dd(5, 0)),
    (dms_to_dd(43, 10), dms_to_dd(5, 0)),
    (dms_to_dd(43, 10), dms_to_dd(4, 50)),
    (dms_to_dd(43, 3), dms_to_dd(4, 45)),
    (dms_to_dd(43, 3), dms_to_dd(4, 20)),
]

_LAT0 = sum(p[0] for p in FRA_POLYGON_LATLON) / len(FRA_POLYGON_LATLON)
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(_LAT0))


def latlon_to_xy(lat: float, lon: float) -> tuple:
    return lon * _M_PER_DEG_LON, lat * _M_PER_DEG_LAT


_FRA_POLY_XY = Polygon(
    [latlon_to_xy(lat, lon) for lat, lon in FRA_POLYGON_LATLON]
)
_FRA_POLY_XY_BUFFERED = _FRA_POLY_XY.buffer(-BUFFER_M)


def in_fra_zone_est(lat: float, lon: float) -> bool:
    if pd.isna(lat) or pd.isna(lon):
        return False
    x, y = latlon_to_xy(lat, lon)
    return _FRA_POLY_XY_BUFFERED.contains(Point(x, y))


def in_season_est(dt) -> bool:
    return (dt >= DATE_DEBUT_LIMITE) and (dt.month in (11, 12, 1, 2, 3, 4))


print(f"📥 Chargement de {INPUT_FILE}...")
df = pd.read_csv(INPUT_FILE)
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

print("🔍 Vérification des intersections avec la zone FRA (>= 2020)...")
df["est_problematique"] = df.apply(
    lambda row: in_fra_zone_est(row["lat"], row["lon"])
    and in_season_est(row["timestamp"]),
    axis=1,
)

df = df.sort_values(by=["event_id_unique", "timestamp"]).reset_index(drop=True)

changement = df["est_problematique"] != df.groupby("event_id_unique")[
    "est_problematique"
].shift(1)
df["block_id"] = changement.groupby(df["event_id_unique"]).cumsum()

blocs_infraction = (
    df[df["est_problematique"]]
    .groupby(["event_id_unique", "block_id"])
    .agg(
        nb_pings=("timestamp", "size"),
        debut_bloc=("timestamp", "min"),
        fin_bloc=("timestamp", "max"),
    )
    .reset_index()
)

blocs_infraction["duree_bloc_h"] = (
    blocs_infraction["fin_bloc"] - blocs_infraction["debut_bloc"]
).dt.total_seconds() / 3600.0

ids_suspicieux = blocs_infraction[
    (blocs_infraction["nb_pings"] >= 3) & (blocs_infraction["duree_bloc_h"] >= 2.0)
]["event_id_unique"].unique()

df_suspicieux = df[df["event_id_unique"].isin(ids_suspicieux)].copy()
df_suspicieux = df_suspicieux.drop(columns=["block_id"])

print(
    f"⚠️ {len(ids_suspicieux)} événement(s) de pêche en infraction détecté(s)"
    " (>= 3 pings ET >= 2h)."
)

df_in_zone = df_suspicieux[df_suspicieux["est_problematique"]]

resume = (
    df_in_zone.groupby(["bateau", "event_id_unique"])
    .agg(
        date_debut_infraction=("timestamp", "min"),
        date_fin_infraction=("timestamp", "max"),
        nombre_points_en_zone=("timestamp", "size"),
    )
    .reset_index()
)

resume["duree_infraction_h"] = (
    resume["date_fin_infraction"] - resume["date_debut_infraction"]
).dt.total_seconds() / 3600.0
resume["duree_infraction_h"] = resume["duree_infraction_h"].round(2)
resume["fichier_source"] = os.path.basename(INPUT_FILE)
resume = resume.rename(columns={"bateau": "nom_bateau"})

colonnes_export = [
    "nom_bateau",
    "date_debut_infraction",
    "date_fin_infraction",
    "duree_infraction_h",
    "nombre_points_en_zone",
    "fichier_source",
    "event_id_unique",
]
resume = resume[colonnes_export]

df_suspicieux.to_csv(FICHIER_PINGS, index=False)
print(f"💾 Fichier exporté : {FICHIER_PINGS}")

resume.to_csv(FICHIER_RESUME, index=False)
print(f"💾 Fichier exporté : {FICHIER_RESUME}")

df_res_stats = resume.copy()
df_res_stats["date_debut"] = pd.to_datetime(df_res_stats["date_debut_infraction"])
df_res_stats["date_fin"] = pd.to_datetime(df_res_stats["date_fin_infraction"])
df_res_stats["duree_heures"] = (
    df_res_stats["date_fin"] - df_res_stats["date_debut"]
).dt.total_seconds() / 3600.0

total_infractions_global = len(df_res_stats)
total_heures_global = df_res_stats["duree_heures"].sum()

synthèse_bateau = (
    df_res_stats.groupby("nom_bateau")
    .agg(
        nombre_infractions=("nom_bateau", "count"),
        total_heures=("duree_heures", "sum"),
    )
    .reset_index()
)
synthèse_bateau["total_heures"] = synthèse_bateau["total_heures"].round(2)
synthèse_bateau = synthèse_bateau.sort_values(
    by="total_heures", ascending=False
)

with open(FICHIER_TXT, "w", encoding="utf-8") as f:
    bilan = resume["nom_bateau"].value_counts().sort_index()
    f.write("--- NOMBRE D'ÉVÉNEMENTS SUSPICIEUX PAR BATEAU ---\n\n")
    f.write(bilan.to_string())
    f.write("\n\n" + "=" * 50 + "\n\n")

    f.write("=" * 50 + "\n")
    f.write("📈 BILAN GLOBAL DE LA FLOTTE\n")
    f.write("=" * 50 + "\n")
    f.write(f"• Nombre total d'infractions : {total_infractions_global}\n")
    f.write(f"• Nombre total d'heures cumulées : {total_heures_global:.2f} h\n")
    f.write("=" * 50 + "\n\n")

    f.write("📊 SYNTHÈSE DÉTAILLÉE PAR BATEAU :\n\n")
    f.write(synthèse_bateau.to_string(index=False))
    f.write("\n")

print(f"💾 Fichier exporté : {FICHIER_TXT}")