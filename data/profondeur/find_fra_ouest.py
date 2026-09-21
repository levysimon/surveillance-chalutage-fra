"""
zone_frontiere_espagnole_v3.py

Objectif : localiser la zone de fermeture "90-100 m, frontière franco-
espagnole / bordure ouest de la FRA CGPM" (Art. 5 ter, 2e alinéa,
arrêté du 20/12/2019).


    Sa bordure ouest est la ligne verticale à 4°20'E, entre 42°40'N et
    43°03'N. La zone 90-100m s'étend entre la frontière espagnole
    (~3,10-3,15°E) et cette ligne — une bande côtière bien plus large
    que dans v2, cohérente avec la carte Ifremer (toute la façade
    Occitanie).

  IMPORTANT : la "frontière franco-espagnole" n'a PAS de tracé légal
  officiel en Méditerranée (aucun traité, contrairement au golfe de
  Gascogne — la France et l'Espagne sont en désaccord depuis les
  années 1970). On utilise donc une limite ouest approximative ancrée
  sur le point terrestre de la frontière (Cap Cerbère, 42°26'N/3°9'E) ;
  à affiner si vous obtenez la carte annexe officielle.

Entrée : MNT bathymétrique ESRI ASCII Grid (.asc), ex.
  MNT_MED100m_GDL-CA_HOMONIM_WGS84_NM_ZNEG.asc

Sorties :
  - zone_90_100m_frontiere_es_v2.geojson
  - zone_90_100m_frontiere_es_v2.png

Dépendances : numpy, scipy, matplotlib, shapely
    pip install numpy scipy matplotlib shapely --break-system-packages
"""
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import unary_union

# =============================================================================
# CONFIGURATION
# =============================================================================

ASC_PATH = "MNT_MED100m_GDL-CA_HOMONIM_WGS84_NM_ZNEG.asc"

ISOBATH_SHALLOW = 90.0   # m
ISOBATH_DEEP = 100.0     # m

# --- Géométrie officielle de la FRA CGPM (recommandation CGPM/33/2009/1,
#     revue CGPM/44/2021/5) — coordonnées données dans le texte source
#     de l'arrêté du 20/12/2019, Art. 5 ter al. 1 ---
def dms_to_dd(deg, minutes, hemisphere):
    dd = deg + minutes / 60.0
    if hemisphere in ("S", "W"):
        dd = -dd
    return dd

# (lat_deg, lat_min, lat_hemi, lon_deg, lon_min, lon_hemi) — dans l'ordre du texte
FRA_CORNERS_DMS = [
    (42, 40.0, "N", 4, 20.0, "E"),
    (42, 40.0, "N", 5, 0.0, "E"),
    (43, 10.0, "N", 5, 0.0, "E"),
    (43, 10.0, "N", 4, 50.0, "E"),
    (43, 3.0, "N", 4, 45.0, "E"),
    (43, 3.0, "N", 4, 20.0, "E"),
]

FRA_POLYGON_LONLAT = [
    (dms_to_dd(lon_d, lon_m, lon_h), dms_to_dd(lat_d, lat_m, lat_h))
    for (lat_d, lat_m, lat_h, lon_d, lon_m, lon_h) in FRA_CORNERS_DMS
]

# Bordure ouest de cette FRA = segment vertical à 4°20'E, entre 42°40'N et
# 43°03'N (1er et dernier point du tracé, qui se referment sur ce côté).
WEST_EDGE = LineString([FRA_POLYGON_LONLAT[0], FRA_POLYGON_LONLAT[-1]])

# --- Frontière franco-espagnole (approximation, pas de tracé légal en
#     Méditerranée) : on ancre sur le point terrestre Cap Cerbère et on
#     prend une ligne verticale approximative comme limite ouest de la
#     fenêtre de recherche. À affiner si vous obtenez le tracé officiel
#     utilisé par l'administration (cf. carte annexe de l'arrêté).
LON_FRONTIERE_ES_APPROX = 3.15  # °E, ancré sur 42°26'N,3°9'E (Cap Cerbère)

# Bornes latitude de la fenêtre de recherche (façade Occitanie, du sud
# de la frontière jusqu'au nord de la FRA). Étendue plus au nord pour
# couvrir davantage de côte au-delà de la FRA CGPM.
LAT_MIN_WINDOW = 42.30
LAT_MAX_WINDOW = 43.60

# Filtre anti-bruit :
# - lissage gaussien du MNT avant extraction (réduit les micro-artefacts
#   de canyons/résolution qui fragmentaient la bande en 104 polygones)
# - aire minimale des polygones conservés (km²)
SMOOTHING_SIGMA_PX = 2.0     # écart-type du filtre gaussien, en pixels
MIN_POLY_AREA_KM2 = 5      # fragments plus petits = bruit, ignorés


# =============================================================================
# LECTURE DU MNT
# =============================================================================

def read_asc(path: str):
    header = {}
    with open(path, "r") as f:
        for _ in range(6):
            key, val = f.readline().split()
            header[key.lower()] = float(val)
        data = np.loadtxt(f)

    ncols = int(header["ncols"])
    nrows = int(header["nrows"])
    xll = header.get("xllcorner", header.get("xllcenter"))
    yll = header.get("yllcorner", header.get("yllcenter"))
    cellsize = header["cellsize"]
    nodata = header.get("nodata_value", -9999)

    data = np.where(data == nodata, np.nan, data)

    lons = xll + (np.arange(ncols) + 0.5) * cellsize
    lats = yll + (np.arange(nrows) + 0.5) * cellsize
    lats = lats[::-1]

    return lons, lats, data


def normalize_depth_positive(Z: np.ndarray) -> np.ndarray:
    median = np.nanmedian(Z)
    return -Z if median < 0 else Z


# =============================================================================
# GÉOMÉTRIE : bande le long du segment ouest de la FRA
# =============================================================================

def km_per_degree(lat_deg: float):
    """Approximation locale : combien de km par degré de lon/lat à cette latitude."""
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * np.cos(np.radians(lat_deg))
    return km_per_deg_lon, km_per_deg_lat


def build_search_window():
    """
    Fenêtre = bande côtière rectangulaire entre la frontière espagnole
    (approximation, cf. note en tête de fichier) et la bordure ouest de
    la FRA CGPM (4°20'E, ligne officielle). C'est une simple box lon/lat,
    pas un buffer étroit : le texte réglementaire ne dit pas que la zone
    est "collée" à la frontière, seulement qu'elle est ENTRE les deux
    lignes — d'où une bande large (~1° de longitude), cohérente avec la
    carte Ifremer qui montre toute la façade Occitanie couverte.
    """
    from shapely.geometry import box
    lon_west = LON_FRONTIERE_ES_APPROX
    lon_east = WEST_EDGE.coords[0][0]  # = 4°20'E
    return box(lon_west, LAT_MIN_WINDOW, lon_east, LAT_MAX_WINDOW)


def load_and_prepare_bathy(lons, lats, Z_raw):
    Z = normalize_depth_positive(Z_raw)
    Z_smooth = gaussian_filter(np.nan_to_num(Z, nan=np.nanmax(Z)), sigma=SMOOTHING_SIGMA_PX)
    # ré-appliquer les NaN d'origine (terre / hors grille) pour ne pas
    # créer de fausses zones sur le lissage des bords NaN
    Z_smooth = np.where(np.isnan(Z), np.nan, Z_smooth)
    return Z_smooth


def extract_band_polygons(lons, lats, Z_smooth, window_polygon):
    mask_band = (Z_smooth >= ISOBATH_SHALLOW) & (Z_smooth <= ISOBATH_DEEP)
    mask_band = mask_band.astype(np.uint8)

    fig, ax = plt.subplots()
    cs = ax.contour(lons, lats, mask_band, levels=[0.5])
    plt.close(fig)

    if hasattr(cs, "allsegs"):
        segs = cs.allsegs[0]
    else:
        segs = [p.vertices for coll in cs.collections for p in coll.get_paths()]

    polys = []
    for seg in segs:
        if len(seg) < 4:
            continue
        line = LineString(seg)
        if not line.is_ring:
            continue
        poly = Polygon(seg)
        if poly.is_valid and poly.area > 0:
            polys.append(poly)

    if not polys:
        return []

    zone = unary_union(polys)
    zone_clipped = zone.intersection(window_polygon)

    if zone_clipped.is_empty:
        return []

    candidates = [zone_clipped] if zone_clipped.geom_type == "Polygon" else list(zone_clipped.geoms)

    # Filtre anti-bruit : aire minimale en km²
    lat_mid = np.mean(lats)
    km_lon, km_lat = km_per_degree(lat_mid)
    km2_per_deg2 = km_lon * km_lat

    kept = []
    for poly in candidates:
        area_km2 = poly.area * km2_per_deg2
        if area_km2 >= MIN_POLY_AREA_KM2:
            kept.append((poly, area_km2))

    kept.sort(key=lambda t: -t[1])
    return kept


# =============================================================================
# MAIN
# =============================================================================

def main():
    print(f"📂 Lecture du MNT : {ASC_PATH}")
    lons, lats, Z_raw = read_asc(ASC_PATH)
    print(f"   Grille {Z_raw.shape[1]}x{Z_raw.shape[0]} px | "
          f"lon [{lons.min():.3f}, {lons.max():.3f}] | "
          f"lat [{lats.min():.3f}, {lats.max():.3f}]")

    fra_polygon = Polygon(FRA_POLYGON_LONLAT)
    print("📐 FRA CGPM (rec. CGPM/33/2009/1, revue 2021) :",
          [(round(x, 4), round(y, 4)) for x, y in FRA_POLYGON_LONLAT])
    print(f"📏 Bordure ouest (4°20'E) : {list(WEST_EDGE.coords)}")

    window = build_search_window()
    print(f"🪟 Fenêtre de recherche : lon [{window.bounds[0]:.3f}, {window.bounds[2]:.3f}] "
          f"| lat [{window.bounds[1]:.3f}, {window.bounds[3]:.3f}]")

    print(f"🎛️  Lissage gaussien (sigma={SMOOTHING_SIGMA_PX}px) + extraction "
          f"bande {ISOBATH_SHALLOW:.0f}-{ISOBATH_DEEP:.0f} m dans cette fenêtre...")
    Z_smooth = load_and_prepare_bathy(lons, lats, Z_raw)

    kept = extract_band_polygons(lons, lats, Z_smooth, window)

    if not kept:
        print("❌ Aucune zone trouvée après filtrage. Essayez d'augmenter "
              "BAND_WIDTH_KM ou de réduire MIN_POLY_AREA_KM2.")
        return

    print(f"✅ {len(kept)} polygone(s) retenu(s) après filtrage anti-bruit "
          f"(aire min {MIN_POLY_AREA_KM2} km²) :")
    for i, (poly, area) in enumerate(kept):
        cx, cy = poly.centroid.x, poly.centroid.y
        print(f"   #{i}: {area:.2f} km² | centroïde ({cy:.4f}N, {cx:.4f}E)")

    # --- Export GeoJSON ---
    features = []
    for i, (poly, area) in enumerate(kept):
        features.append({
            "type": "Feature",
            "properties": {
                "id": i,
                "area_km2": round(area, 2),
                "isobath_min_m": ISOBATH_SHALLOW,
                "isobath_max_m": ISOBATH_DEEP,
                "reference": "Art. 5 ter al.2 (JORF 26/12/2019) + FRA géométrie "
                             "arrêté 23/04/2018 art.1.2",
            },
            "geometry": mapping(poly),
        })
    geojson = {"type": "FeatureCollection", "features": features}

    out_geojson = "./zone_90_100m_frontiere_es_v2.geojson"
    with open(out_geojson, "w") as f:
        json.dump(geojson, f, indent=2)
    print(f"✅ GeoJSON exporté : {out_geojson}")

    # --- Carte de contrôle ---
    fig, ax = plt.subplots(figsize=(9, 7))

    # Paliers de profondeur : plus fins autour de 90-100m (avec une couleur
    # dédiée pour cette bande), plus larges ailleurs, et une seule catégorie
    # pour tout ce qui dépasse 200m.
    depth_levels = [0, 20, 40, 60, 80, 90, 100, 120, 140, 160, 180, 200]
    depth_colors = [
        "#f7fbff",  # 0-20 m
        "#deebf7",  # 20-40 m
        "#c6dbef",  # 40-60 m
        "#9ecae1",  # 60-80 m
        "#6baed6",  # 80-90 m
        "#59e569",  # 90-100 m (bande réglementaire, mise en évidence)
        "#3182bd",  # 100-120 m
        "#2171b5",  # 120-140 m
        "#08519c",  # 140-160 m
        "#08306b",  # 160-180 m
        "#041a3d",  # 180-200 m
    ]

    depth_cmap = matplotlib.colors.ListedColormap(depth_colors)
    depth_cmap.set_over("#000000")  # tout ce qui est > 200 m : une seule couleur
    depth_norm = matplotlib.colors.BoundaryNorm(depth_levels, depth_cmap.N)

    cf = ax.contourf(
        lons, lats, Z_smooth,
        levels=depth_levels,
        cmap=depth_cmap,
        norm=depth_norm,
        extend="max",
    )
    cbar = plt.colorbar(cf, ax=ax, label="Profondeur (m, lissée)")
    cbar.set_ticks(depth_levels)
    cbar.set_ticklabels([str(int(l)) for l in depth_levels])

    fx, fy = fra_polygon.exterior.xy
    ax.plot(fx, fy, color="black", linewidth=1.5, linestyle="--", label="FRA CGPM (rec. 2009/2021)")

    wx, wy = WEST_EDGE.xy
    ax.plot(wx, wy, color="orange", linewidth=2.5, label="Bordure ouest de la FRA (4°20'E)")

    winx, winy = window.exterior.xy
    ax.plot(winx, winy, color="gray", linewidth=1, linestyle=":", label="Fenêtre de recherche")

    for poly, area in kept:
        x, y = poly.exterior.xy
        ax.plot(x, y, color="red", linewidth=2)
        ax.fill(x, y, color="red", alpha=0.4)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Zone 90-100m — bordure ouest FRA CGPM (Art. 5 ter al.2)")
    ax.legend(loc="best", fontsize=8)
    ax.set_xlim(LON_FRONTIERE_ES_APPROX - 0.4, FRA_POLYGON_LONLAT[1][0] + 0.5)
    ax.set_ylim(LAT_MIN_WINDOW - 0.2, max(LAT_MAX_WINDOW, fra_polygon.bounds[3]) + 0.2)

    out_png = "./zone_90_100m_frontiere_es_v2.png"
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    print(f"🖼️  Carte exportée : {out_png}")


if __name__ == "__main__":
    main()