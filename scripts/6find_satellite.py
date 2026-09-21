"""
Recherche d'image satellite correspondante a nos evenements suspicieux
"""
import os
import math
import time
import numpy as np
import pandas as pd
from datetime import timedelta
from pystac_client import Client
import planetary_computer
import rasterio
import rasterio.env
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from PIL import Image

# ============================================================
# CONFIGURATION
# ============================================================

ZONES = [
    {
        "nom_zone": "ouest",
        "resume_file": "./data/infractions ouest/resume_infractions.csv",
        "pings_file": "./data/infractions ouest/pings_complets_evenements_suspicieux.csv",
    },
    {
        "nom_zone": "fra_est",
        "resume_file": "./data/infractions fra est/resume_infractions.csv",
        "pings_file": "./data/infractions fra est/pings_complets_evenements_suspicieux.csv",
    },
]

OUTPUT_MATCHES = "./data/sentinel2_fra_infraction.csv"
DOWNLOAD_DIR = "./data/images_satellites_infractions"

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
SENTINEL2_COLLECTION = "sentinel-2-l2a"

# Optionnel : Si vous demandez un token gratuit sur https://planetarycomputer.microsoft.com/account/request
# renseignez-le ici. Sinon, laissez vide (les délais augmentés compenseront).
PC_API_KEY = "PLAKf0c560422bb94349acc50df3f59c054b"

BAND_RED = "B04"
BAND_GREEN = "B03"
BAND_BLUE = "B02"
BAND_NIR = "B08"

BANDS_TO_DOWNLOAD = [BAND_RED, BAND_GREEN, BAND_BLUE, BAND_NIR]

STRETCH_LOW_PCT = 2
STRETCH_HIGH_PCT = 98

RASTERIO_ENV = rasterio.env.Env(
    GDAL_HTTP_USERAGENT="MarineTrawlingAnalysis/1.0 (contact@votre-domaine.com)",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="5",
    VSI_CACHE=True
)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ============================================================
# HELPER RETRY ANTI-RATE-LIMIT
# ============================================================

def execute_stac_search_with_retry(catalog, collections, bbox, datetime_range, max_retries=5):
    """
    Exécute une recherche STAC avec une pause très importante en cas de Rate Limit (HTTP 429).
    """
    delay = 10  # Si bloqué, on attend 10s minimum la première fois
    for attempt in range(1, max_retries + 1):
        try:
            search = catalog.search(
                collections=collections,
                bbox=bbox,
                datetime=datetime_range
            )
            # On force la conversion en liste pour exécuter la requête HTTP immédiatement
            # et intercepter l'erreur ici plutôt que dans la boucle for
            items = list(search.items())
            return items
        except Exception as e:
            err_msg = str(e).lower()
            if "rate limit" in err_msg or "429" in err_msg:
                print(f"   🛑 Rate Limit atteint ! Pause forcée de {delay} secondes... (Tentative {attempt}/{max_retries})")
            else:
                print(f"   ⚠️ Erreur : {e} (Tentative {attempt}/{max_retries})")
            
            if attempt == max_retries:
                raise e
            
            time.sleep(delay)
            delay *= 2.5  # Attend 10s, puis 25s, puis 62s...

# ============================================================
# 1. LECTURE DES FICHIERS (POUR CHAQUE ZONE)
# ============================================================

print("1. Lecture des fichiers...")

resume_frames = []
pings_frames = {}

required_resume = ["nom_bateau", "date_debut_infraction", "date_fin_infraction", "event_id_unique"]
required_pings = ["timestamp", "lat", "lon", "event_id_unique"]

for zone in ZONES:
    nom_zone = zone["nom_zone"]
    resume_file = zone["resume_file"]
    pings_file = zone["pings_file"]

    if not os.path.exists(resume_file) or not os.path.exists(pings_file):
        print(f"⚠️ Fichiers manquants pour la zone '{nom_zone}', zone ignorée.")
        continue

    resume_z = pd.read_csv(resume_file)
    pings_z = pd.read_csv(pings_file)

    for col in required_resume:
        if col not in resume_z.columns:
            raise ValueError(f"Colonne absente de {resume_file} : {col}")

    for col in required_pings:
        if col not in pings_z.columns:
            raise ValueError(f"Colonne absente de {pings_file} : {col}")

    pings_z["timestamp"] = pd.to_datetime(pings_z["timestamp"], utc=True)
    pings_z["lat"] = pd.to_numeric(pings_z["lat"], errors="coerce")
    pings_z["lon"] = pd.to_numeric(pings_z["lon"], errors="coerce")
    pings_z = pings_z.dropna(subset=["timestamp", "lat", "lon"])

    resume_z["nom_zone"] = nom_zone

    resume_frames.append(resume_z)
    pings_frames[nom_zone] = pings_z

    print(f"-> Zone '{nom_zone}' : {len(resume_z)} infractions, {len(pings_z)} pings")

if not resume_frames:
    print("❌ Aucune zone exploitable.")
    raise SystemExit

resume = pd.concat(resume_frames, ignore_index=True)

print(f"\n-> Total : {len(resume)} infractions (toutes zones confondues)\n")

# ============================================================
# 2. CONNEXION PLANETARY COMPUTER
# ============================================================

print("2. Connexion à Planetary Computer...")

headers = {}
if PC_API_KEY:
    headers["Ocp-Apim-Subscription-Key"] = PC_API_KEY
    planetary_computer.set_subscription_key(PC_API_KEY)

catalog = Client.open(STAC_URL, headers=headers)

print("-> Connexion OK\n")

# ============================================================
# 3. RECHERCHE DES POSITIONS ET BBOX
# ============================================================

print("3. Préparation des infractions...\n")

summary = []

for _, row in resume.iterrows():
    event_id = str(row["event_id_unique"])
    bateau = str(row["nom_bateau"])
    nom_zone = str(row["nom_zone"])

    start = pd.to_datetime(row["date_debut_infraction"], utc=True)
    end = pd.to_datetime(row["date_fin_infraction"], utc=True)

    pings = pings_frames[nom_zone]
    event_pings = pings[pings["event_id_unique"].astype(str) == event_id]

    if event_pings.empty:
        print(f"⚠️ Aucun ping pour {event_id} (zone {nom_zone})")
        continue

    start_search = start - timedelta(hours=2)
    end_search = end + timedelta(hours=2)

    min_lat = event_pings["lat"].min()
    max_lat = event_pings["lat"].max()
    min_lon = event_pings["lon"].min()
    max_lon = event_pings["lon"].max()

    lat_buffer = 0.09
    mean_lat = (min_lat + max_lat) / 2
    lon_km_per_degree = 111.32 * math.cos(math.radians(mean_lat))
    lon_buffer = 10 / lon_km_per_degree

    bbox = [
        min_lon - lon_buffer,
        min_lat - lat_buffer,
        max_lon + lon_buffer,
        max_lat + lat_buffer
    ]

    summary.append({
        "event_id_unique": event_id,
        "nom_bateau": bateau,
        "nom_zone": nom_zone,
        "date_debut": start,
        "date_fin": end,
        "start_search": start_search,
        "end_search": end_search,
        "bbox": bbox
    })

# ============================================================
# 4. RECHERCHE SENTINEL-2 (AVEC PAUSE DE SÉCURITÉ)
# ============================================================

print("4. Recherche des images Sentinel-2...\n")

results = []

for item_data in summary:
    event_id = item_data["event_id_unique"]
    bateau = item_data["nom_bateau"]
    nom_zone = item_data["nom_zone"]
    bbox = item_data["bbox"]

    start = item_data["start_search"]
    end = item_data["end_search"]

    datetime_range = (
        f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/"
        f"{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )

    print(f"🔎 [{nom_zone}] {bateau} — {event_id}")

    try:
        items = execute_stac_search_with_retry(
            catalog,
            collections=[SENTINEL2_COLLECTION],
            bbox=bbox,
            datetime_range=datetime_range
        )

        count = 0
        for item in items:
            count += 1
            cloud = item.properties.get("eo:cloud_cover")

            if cloud is not None:
                cloud = round(float(cloud), 2)

            if cloud is not None and cloud > 50:
                print(f"   ⛅ {item.id} ignorée : {cloud}% de nuages")
                continue

            missing_bands = [
                b for b in BANDS_TO_DOWNLOAD
                if b not in item.assets
            ]
            if missing_bands:
                print(f"   ⚠️ {item.id} ignorée : bandes manquantes {missing_bands}")
                continue

            acquisition = (
                item.datetime.strftime("%Y-%m-%d %H:%M:%S UTC")
                if item.datetime else ""
            )

            results.append({
                "nom_zone": nom_zone,
                "event_id_unique": event_id,
                "Nom_Bateau": bateau,
                "Scene_ID": item.id,
                "Date_Acquisition_UTC": acquisition,
                "Nuages_%": cloud,
                "_item": item,
                "_bbox": bbox
            })

        print(f"   -> {count} scène(s) trouvée(s)\n")

    except Exception as e:
        print(f"   ❌ Erreur de recherche finale sur {event_id} (zone {nom_zone}) : {e}\n")

    # PAUSE ANTICIPATIVE : 3 secondes de pause entre chaque recherche STAC
    # pour ne pas surcharger l'API de Microsoft
    time.sleep(0.1)

# ============================================================
# 5. EXPORT DES RÉSULTATS
# ============================================================

df = pd.DataFrame(results)

if df.empty:
    print("❌ Aucune image Sentinel-2 trouvée.")
    raise SystemExit

df = df.drop_duplicates(subset=["nom_zone", "event_id_unique", "Scene_ID"])

export_cols = [
    "nom_zone",
    "event_id_unique",
    "Nom_Bateau",
    "Scene_ID",
    "Date_Acquisition_UTC",
    "Nuages_%"
]

df[export_cols].to_csv(OUTPUT_MATCHES, index=False, encoding="utf-8")

print("=" * 60)
print("IMAGES TROUVÉES")
print("=" * 60)
print(df[export_cols].to_string(index=False))

print(f"\nFichier : {OUTPUT_MATCHES}")
print(f"Nombre de scènes : {len(df)}")

# ============================================================
# 6. DEMANDE DE CONFIRMATION
# ============================================================

reponse = input(
    "\nVoulez-vous télécharger ces images en .tif "
    f"(bandes brutes {'/'.join(BANDS_TO_DOWNLOAD)}) + .png ? (O/N) : "
).strip().lower()

if reponse not in ["o", "oui"]:
    print("Téléchargement annulé.")
    raise SystemExit

# ============================================================
# 7. TÉLÉCHARGEMENT
# ============================================================

def download_tif_raw_bands(item, bbox, filepath):
    signed = planetary_computer.sign(item)
    band_assets = {band: signed.assets.get(band) for band in BANDS_TO_DOWNLOAD}

    missing = [k for k, v in band_assets.items() if v is None]
    if missing:
        print(f"   ⚠️ Asset(s) manquant(s) : {missing}")
        return False

    try:
        band_arrays = []
        profile = None
        win_transform = None

        for band_name in BANDS_TO_DOWNLOAD:
            asset = band_assets[band_name]

            with rasterio.open(asset.href) as src:
                left, bottom, right, top = transform_bounds(
                    "EPSG:4326", src.crs, *bbox
                )
                window = from_bounds(
                    left, bottom, right, top, transform=src.transform
                )
                window = window.round_offsets().round_lengths()
                data = src.read(1, window=window)

                if data.size == 0:
                    print(f"   ⚠️ Découpe vide pour la bande {band_name}")
                    return False

                band_arrays.append(data)

                if profile is None:
                    profile = src.profile.copy()
                    win_transform = src.window_transform(window)

        stacked = np.stack(band_arrays, axis=0)

        profile.update({
            "driver": "GTiff",
            "count": stacked.shape[0],
            "height": stacked.shape[1],
            "width": stacked.shape[2],
            "transform": win_transform,
            "dtype": stacked.dtype,
        })

        with rasterio.open(filepath, "w", **profile) as dst:
            dst.write(stacked)
            for idx, band_name in enumerate(BANDS_TO_DOWNLOAD, start=1):
                dst.set_band_description(idx, band_name)

        return True

    except Exception as e:
        print(f"   ❌ TIF : {e}")
        return False


def compute_joint_stretch_bounds(bands, low_pct=STRETCH_LOW_PCT, high_pct=STRETCH_HIGH_PCT):
    valid_pixels = np.concatenate([b[b > 0].ravel() for b in bands if (b > 0).any()])

    if valid_pixels.size == 0:
        return 0.0, 1.0

    low = np.percentile(valid_pixels, low_pct)
    high = np.percentile(valid_pixels, high_pct)

    if high <= low:
        high = low + 1

    return low, high


def apply_stretch(band, low, high):
    stretched = np.clip((band.astype(np.float32) - low) / (high - low), 0, 1)
    return (stretched * 255).astype(np.uint8)


def download_png_from_tif(tif_path, png_path):
    try:
        with rasterio.open(tif_path) as src:
            data = src.read()

        rgb_bands = data[:3]
        low, high = compute_joint_stretch_bounds(rgb_bands)

        rgb = np.stack(
            [apply_stretch(rgb_bands[i], low, high) for i in range(3)],
            axis=-1
        )

        image = Image.fromarray(rgb, mode="RGB")
        image.save(png_path, "PNG")
        return True

    except Exception as e:
        print(f"   ❌ PNG : {e}")
        return False


print("\n" + "=" * 60)
print("TÉLÉCHARGEMENT")
print("=" * 60)

with RASTERIO_ENV:
    for i, row in df.iterrows():

        nom_zone = row["nom_zone"]
        event_id = row["event_id_unique"]
        scene_id = row["Scene_ID"]

        item = row["_item"]
        bbox = row["_bbox"]

        base_name = f"{nom_zone}__{event_id}__{scene_id}"
        tif_path = os.path.join(DOWNLOAD_DIR, base_name + ".tif")
        png_path = os.path.join(DOWNLOAD_DIR, base_name + ".png")

        print(f"\n[{i + 1}/{len(df)}] [{nom_zone}] {event_id}")

        tif_ok = download_tif_raw_bands(item, bbox, tif_path)

        if tif_ok:
            print(f"   ✓ TIF : {tif_path}")
            if download_png_from_tif(tif_path, png_path):
                print(f"   ✓ PNG : {png_path}")
        else:
            print("   ⏭️  PNG non généré (échec du TIF)")

        time.sleep(3)

print("\n" + "=" * 60)
print("TERMINÉ")
print("=" * 60)
print(f"Images sauvegardées dans : {DOWNLOAD_DIR}")