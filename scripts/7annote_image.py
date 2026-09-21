import os
import re
import glob
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import rasterio
from rasterio.warp import transform as warp_transform
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

ROOT_IMAGES = os.path.join("data", "images_satellites_infractions")
CATEGORIES = [""] 
AIS_CSV = os.path.join("data", "evenement de peche", "pings_evenements_peche.csv")

# Fuseau d'AFFICHAGE uniquement (texte sur l'image). Tous les calculs
# internes restent en UTC. Mettre à None pour tout afficher en UTC.
DISPLAY_TZ = ZoneInfo("Europe/Paris")

COLOR_AIS = (30, 144, 255)          # bleu — points AIS de l'événement
COLOR_AIS_CLOSEST = (255, 140, 0)   # orange — point le plus proche en temps de la photo
COLOR_TRACK = (30, 144, 255)
COLOR_TEXT_BG = (0, 0, 0)

CROSS_SIZE = 9        # demi-longueur des branches de la croix (pixels)
CROSS_WIDTH = 2

# Les .tif sont nommés "<zone>__<NAVIRE>_evt_<N>__<SCENE_ID>.tif"
FILENAME_RE = re.compile(r"^(.+?)__(.+?)_evt_(\d+)__(.+)$")
SENSING_DATE_RE = re.compile(r"_(\d{8}T\d{6})_")


# ---------------------------------------------------------------------------
# OUTILS DATE / HEURE
# ---------------------------------------------------------------------------

def fmt_time(dt_utc):
    if DISPLAY_TZ is not None:
        local = dt_utc.astimezone(DISPLAY_TZ)
        return local.strftime("%Y-%m-%d %H:%M:%S") + " (heure de Paris)"
    return dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_time_short(dt_utc):
    local = dt_utc.astimezone(DISPLAY_TZ) if DISPLAY_TZ is not None else dt_utc
    return local.strftime("%H:%M:%S")


def parse_acquisition_time(scene_id):
    """
    Extrait l'heure de prise de vue Sentinel-2 depuis le SCENE_ID.
    On prend le PREMIER horodatage AAAAMMJJTHHMMSS trouvé (sensing time),
    pas le dernier (qui est l'heure de traitement du produit).
    Retourne un datetime tz-aware en UTC, ou None si non trouvé.
    """
    m = SENSING_DATE_RE.search("_" + scene_id + "_")
    if not m:
        return None
    raw = m.group(1)
    dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
    return dt.replace(tzinfo=timezone.utc)


def parse_filename(tif_path):
    """
    Découpe "<zone>__<NAVIRE>_evt_<N>__<SCENE_ID>.tif" en
    (zone, navire, event_id_unique, scene_id, acq_time).
    Retourne None si le nom ne correspond pas au format attendu.
    """
    base = os.path.splitext(os.path.basename(tif_path))[0]
    m = FILENAME_RE.match(base)
    if not m:
        return None
    zone, navire, evt_num, scene_id = m.group(1), m.group(2), m.group(3), m.group(4)
    event_id_unique = f"{navire}_evt_{evt_num}"
    acq_time = parse_acquisition_time(scene_id)
    return {
        "zone": zone,
        "navire": navire,
        "event_id_unique": event_id_unique,
        "scene_id": scene_id,
        "acq_time": acq_time,
    }


# ---------------------------------------------------------------------------
# CHARGEMENT DES DONNÉES AIS
# ---------------------------------------------------------------------------

def load_ais_events(csv_path):
    """
    Charge le fichier de pings par événement de pêche. timestamp est parsé
    en UTC. On garde tout : le filtrage par event_id_unique se fera ensuite
    à la demande pour chaque image.
    """
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    for col in ["lon", "lat", "speed", "course", "depth"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "lat", "lon", "event_id_unique"])
    return df


# ---------------------------------------------------------------------------
# GÉOMÉTRIE / DESSIN
# ---------------------------------------------------------------------------

def latlon_to_pixel_xy(src, lat, lon):
    xs, ys = warp_transform("EPSG:4326", src.crs, [lon], [lat])
    row, col = src.index(xs[0], ys[0])
    return col, row


def try_load_font(size=16):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_label(draw, xy, text, color, font):
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([x - 2, y - 2, x + tw + 2, y + th + 2], fill=COLOR_TEXT_BG)
    draw.text((x, y), text, fill=color, font=font)


def draw_cross(draw, x, y, color, size=CROSS_SIZE, width=CROSS_WIDTH):
    draw.line([x - size, y - size, x + size, y + size], fill=color, width=width)
    draw.line([x - size, y + size, x + size, y - size], fill=color, width=width)


# ---------------------------------------------------------------------------
# ANNOTATION D'UNE IMAGE
# ---------------------------------------------------------------------------

def annotate(tif_path, navire, event_id_unique, acq_time, points_df, out_png_path, zone=None):
    with rasterio.open(tif_path) as src:
        width, height = src.width, src.height

        png_source = tif_path.replace(".tif", ".png")
        if os.path.exists(png_source):
            img = Image.open(png_source).convert("RGB")
            scale_x = img.width / width
            scale_y = img.height / height
        else:
            import numpy as np
            data = src.read()
            
            # Gestion d'une seule bande (N&B)
            if data.shape[0] == 1:
                arr = data[0].astype(np.float32)
            # Gestion multibandes (RGB)
            else:
                arr = np.moveaxis(data[:3], 0, -1).astype(np.float32)
            
            # Normalisation de 16 bits vers 8 bits (0-255)
            arr_max = arr.max()
            if arr_max > 0:
                arr = (arr / arr_max) * 255
            
            img = Image.fromarray(arr.astype(np.uint8)).convert("RGB")
            scale_x = scale_y = 1.0

        draw = ImageDraw.Draw(img)
        font = try_load_font(14)
        font_title = try_load_font(18)

        def to_px(lat, lon):
            col, row = latlon_to_pixel_xy(src, lat, lon)
            return col * scale_x, row * scale_y

        plotted = 0
        prev_xy = None

        if not points_df.empty and acq_time is not None:
            closest_idx = (points_df["timestamp"] - acq_time).abs().idxmin()
        else:
            closest_idx = None

        for idx, pt in points_df.sort_values("timestamp").iterrows():
            px, py = to_px(pt["lat"], pt["lon"])
            if not (0 <= px <= img.width and 0 <= py <= img.height):
                prev_xy = None
                continue

            # trait pointillé reliant les points successifs de l'événement
            if prev_xy is not None:
                x0, y0 = prev_xy
                n_dashes = 12
                for i in range(n_dashes):
                    if i % 2 == 0:
                        fx0 = x0 + (px - x0) * i / n_dashes
                        fy0 = y0 + (py - y0) * i / n_dashes
                        fx1 = x0 + (px - x0) * (i + 1) / n_dashes
                        fy1 = y0 + (py - y0) * (i + 1) / n_dashes
                        draw.line([fx0, fy0, fx1, fy1], fill=COLOR_TRACK, width=1)

            is_closest = (idx == closest_idx)
            color = COLOR_AIS_CLOSEST if is_closest else COLOR_AIS
            draw_cross(draw, px, py, color)

            t_str = fmt_time_short(pt["timestamp"])
            speed_str = f"{pt['speed']:.1f} nds" if pd.notna(pt.get("speed")) else "vitesse ?"
            tag = " ⭐" if is_closest else ""
            draw_label(draw, (px + CROSS_SIZE + 4, py - 6), f"{t_str} — {speed_str}{tag}", color, font)

            prev_xy = (px, py)
            plotted += 1

        # --- Bandeau titre : heure exacte de la photo ---
        if acq_time is not None:
            heure_txt = fmt_time(acq_time)
        else:
            heure_txt = "heure inconnue (non extraite du nom de fichier)"
        zone_txt = f"[{zone}] " if zone else ""
        title = f"{zone_txt}{navire} — {event_id_unique} — Photo satellite : {heure_txt}"
        tb = draw.textbbox((0, 0), title, font=font_title)
        draw.rectangle([0, 0, img.width, tb[3] - tb[1] + 16], fill=COLOR_TEXT_BG)
        draw.text((10, 6), title, fill=(255, 255, 255), font=font_title)

        subtitle = f"{plotted} point(s) AIS de l'événement affiché(s) — orange = plus proche en temps de la photo"
        stb = draw.textbbox((0, 0), subtitle, font=font)
        y0 = tb[3] - tb[1] + 16
        draw.rectangle([0, y0, img.width, y0 + stb[3] - stb[1] + 12], fill=COLOR_TEXT_BG)
        draw.text((10, y0 + 4), subtitle, fill=(200, 200, 200), font=font)

        img.save(out_png_path)
        return plotted


# ---------------------------------------------------------------------------
# TRAITEMENT PAR LOT
# ---------------------------------------------------------------------------

def process_one(tif_path, ais_df):
    info = parse_filename(tif_path)
    if info is None:
        print(f"   ⏭️  Nom de fichier non reconnu (attendu '<zone>__<NAVIRE>_evt_<N>__<SCENE_ID>.tif') — ignoré.")
        return

    zone = info["zone"]
    navire = info["navire"]
    event_id_unique = info["event_id_unique"]
    acq_time = info["acq_time"]

    pts = ais_df[ais_df["event_id_unique"] == event_id_unique].copy()

    print(f"   🌍 Zone : {zone}  |  🚢 Navire : {navire}  |  Événement : {event_id_unique}")
    if acq_time is not None:
        print(f"   📸 Heure photo (extraite du nom, UTC) : {fmt_time(acq_time)}")
    else:
        print(f"   ⚠️  Impossible d'extraire l'heure de prise de vue depuis le nom de fichier.")

    if pts.empty:
        print(f"   ⏭️  Aucun point AIS trouvé pour '{event_id_unique}' dans {AIS_CSV} — ignoré.")
        return

    for _, pt in pts.sort_values("timestamp").iterrows():
        print(f"      ✚ {fmt_time(pt['timestamp'])} | ({pt['lat']:.5f}, {pt['lon']:.5f}) | {pt['speed']:.1f} nds")

    out_path = tif_path.replace(".tif", "_annotated.png")
    n = annotate(tif_path, navire, event_id_unique, acq_time, pts, out_path, zone=zone)
    print(f"   ✅ {n} point(s) tracé(s) sur l'image → {out_path}")


def run_batch():
    if not os.path.exists(AIS_CSV):
        print(f"Erreur : {AIS_CSV} introuvable.")
        return
    if not os.path.isdir(ROOT_IMAGES):
        print(f"Erreur : dossier '{ROOT_IMAGES}/' introuvable.")
        return

    ais_df = load_ais_events(AIS_CSV)

    tif_files = []
    for cat in CATEGORIES:
        cat_dir = os.path.join(ROOT_IMAGES, cat)
        if not os.path.isdir(cat_dir):
            print(f"⚠️  Dossier '{cat_dir}' introuvable — ignoré.")
            continue
        tif_files.extend(glob.glob(os.path.join(cat_dir, "*.tif")))

    if not tif_files:
        print(f"Aucun .tif trouvé dans {ROOT_IMAGES}/.")
        return

    print(f"🔎 {len(tif_files)} image(s) à traiter...\n")

    for tif_path in sorted(tif_files):
        print(f"📡 {tif_path}")
        process_one(tif_path, ais_df)
        print()


if __name__ == "__main__":
    run_batch()