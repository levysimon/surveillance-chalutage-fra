"""
Crée deux visualisations de l'effort de pêche dans le Golfe du Lion :
1. Heatmap classique de l'effort de pêche
2. Heatmap enrichie avec la superposition des zones protégées en transparence
"""

import os
import contextily as cx
import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Polygon

# =============================================================================
# CONFIGURATION
# =============================================================================

DOSSIER_PROCESSED = os.path.join("data", "evenement de peche")
FICHIER_PINGS_PECHE = os.path.join(
    DOSSIER_PROCESSED, "pings_evenements_peche.csv"
)

# Fichier GeoJSON des zones protégées (profondeur)
FICHIER_GEOJSON_ZONE = os.path.join(
    "data", "profondeur", "zone_90_100m_frontiere_es_v2.geojson"
)

# Taille de cellule de la grille, en mètres (1000 = 1km x 1km)
TAILLE_CELLULE_M = 1000

# Système de coordonnées projeté (mètres) adapté au Golfe du Lion (UTM zone 31N)
CRS_PROJETE = "EPSG:32631"

DOSSIER_VISU = os.path.join(DOSSIER_PROCESSED, "visu")
os.makedirs(DOSSIER_VISU, exist_ok=True)

FICHIER_SORTIE_GRILLE = os.path.join(DOSSIER_VISU, "effort_peche_grille.csv")
FICHIER_SORTIE_CARTE_1 = os.path.join(DOSSIER_VISU, "effort_peche_heatmap.png")
FICHIER_SORTIE_CARTE_2 = os.path.join(
    DOSSIER_VISU, "effort_peche_heatmap_zones_protegees.png"
)

MON_EMAIL = "fill_it@gmail.com"


# =============================================================================
# 1. CHARGEMENT ET PRÉPARATION DES DONNÉES
# =============================================================================

# --- Pings de pêche ---
if not os.path.exists(FICHIER_PINGS_PECHE):
    raise FileNotFoundError(
        f"❌ Le fichier '{FICHIER_PINGS_PECHE}' n'existe pas."
    )

df_peche = pd.read_csv(FICHIER_PINGS_PECHE)
df_peche = df_peche.dropna(subset=["lat", "lon"])
print(f"📥 {len(df_peche):,} pings de pêche chargés directement.")

# --- Construction de la zone FRA CGPM ---
fra_coords = [
    [4.3333, 42.6667],
    [5.0000, 42.6667],
    [5.0000, 43.1667],
    [4.8333, 43.1667],
    [4.7500, 43.0500],
    [4.3333, 43.0500],
    [4.3333, 42.6667],
]
fra_polygon = Polygon(fra_coords)
gdf_fra = gpd.GeoDataFrame(geometry=[fra_polygon], crs="EPSG:4326").to_crs(
    CRS_PROJETE
)

# --- Chargement de la zone GeoJSON ---
if os.path.exists(FICHIER_GEOJSON_ZONE):
    gdf_geojson = gpd.read_file(FICHIER_GEOJSON_ZONE)
    if gdf_geojson.crs is None:
        gdf_geojson.set_crs("EPSG:4326", inplace=True)
    gdf_geojson = gdf_geojson.to_crs(CRS_PROJETE)
    print(f"📥 Zone GeoJSON chargée ({len(gdf_geojson)} entités).")
else:
    gdf_geojson = None
    print(
        f"⚠️ Fichier GeoJSON non trouvé : {FICHIER_GEOJSON_ZONE}. Seule la zone"
        " FRA CGPM sera affichée."
    )


# =============================================================================
# 2. PROJECTION EN MÈTRES ET CONSTRUCTION DE LA GRILLE
# =============================================================================

transformer = Transformer.from_crs("EPSG:4326", CRS_PROJETE, always_xy=True)

x_m, y_m = transformer.transform(
    df_peche["lon"].values,
    df_peche["lat"].values,
)

# Assignation de chaque point à une cellule
df_peche["cell_x"] = (x_m // TAILLE_CELLULE_M).astype(int)
df_peche["cell_y"] = (y_m // TAILLE_CELLULE_M).astype(int)

# Agrégation pour obtenir l'effort de pêche (nombre de pings par cellule)
grille = (
    df_peche.groupby(["cell_x", "cell_y"]).size().reset_index(name="nb_points")
)

# Reconversion des centres de cellules en coordonnées Lat/Lon (WGS84)
transformer_inverse = Transformer.from_crs(
    CRS_PROJETE, "EPSG:4326", always_xy=True
)
centre_x_m = grille["cell_x"] * TAILLE_CELLULE_M + TAILLE_CELLULE_M / 2
centre_y_m = grille["cell_y"] * TAILLE_CELLULE_M + TAILLE_CELLULE_M / 2
centre_lon, centre_lat = transformer_inverse.transform(
    centre_x_m.values, centre_y_m.values
)

grille["centre_lon"] = centre_lon
grille["centre_lat"] = centre_lat

grille.to_csv(FICHIER_SORTIE_GRILLE, index=False)
print(f"💾 Grille d'effort de pêche exportée : {FICHIER_SORTIE_GRILLE}")


# =============================================================================
# 3. ÉTAPE COMMUNE DE PLOT (FONCTION UTILITAIRE)
# =============================================================================


def dresser_carte_base():
    """Génère la figure et trace la heatmap ainsi que le fond de carte."""
    fig, ax = plt.subplots(figsize=(12, 10))

    X_MIN, X_MAX = 480000, 700000
    Y_MIN, Y_MAX = 4670000, 4830000

    ax.set_xlim(X_MIN, X_MAX)
    ax.set_ylim(Y_MIN, Y_MAX)

    sc = ax.scatter(
        grille["cell_x"] * TAILLE_CELLULE_M,
        grille["cell_y"] * TAILLE_CELLULE_M,
        c=grille["nb_points"],
        cmap="inferno",
        marker="s",
        s=12,
        alpha=0.75,
        zorder=2,
    )

    cx.add_basemap(
        ax,
        crs=CRS_PROJETE,
        source=cx.providers.OpenStreetMap.Mapnik,
        headers={"User-Agent": f"EffortPecheScript/1.0 ({MON_EMAIL})"},
        zorder=1,
    )

    cbar = plt.colorbar(sc, ax=ax, shrink=0.7)
    cbar.set_label(
        "Nombre de pings AIS en pêche", fontsize=12, fontweight="bold"
    )

    plt.xlabel("Est (m, UTM 31N)", fontsize=11)
    plt.ylabel("Nord (m, UTM 31N)", fontsize=11)
    ax.set_aspect("equal")
    plt.grid(True, linestyle="--", alpha=0.5, zorder=0)

    return fig, ax


# =============================================================================
# 4. CARTE 1 : HEATMAP SEULE
# =============================================================================

fig1, ax1 = dresser_carte_base()
ax1.set_title(
    f"Effort de pêche — Golfe du Lion\n(Grille {TAILLE_CELLULE_M}m + Fond"
    " OpenStreetMap)",
    fontsize=14,
    fontweight="bold",
    pad=15,
)
plt.tight_layout()
plt.savefig(FICHIER_SORTIE_CARTE_1, dpi=150, bbox_inches="tight")
plt.close(fig1)
print(f"🖼️ Carte 1 sauvegardée : {FICHIER_SORTIE_CARTE_1}")


# =============================================================================
# 5. CARTE 2 : HEATMAP + ZONES PROTÉGÉES EN TRANSPARENCE
# =============================================================================

fig2, ax2 = dresser_carte_base()

# Tracé de la zone FRA CGPM (en bleu semi-transparent)
gdf_fra.plot(
    ax=ax2,
    facecolor="cyan",
    edgecolor="blue",
    linewidth=1.5,
    alpha=0.35,
    zorder=3,
)

# Tracé de la zone GeoJSON (en rouge/orange semi-transparent)
if gdf_geojson is not None:
    gdf_geojson.plot(
        ax=ax2,
        facecolor="orange",
        edgecolor="darkred",
        linewidth=1.5,
        alpha=0.35,
        zorder=3,
    )

# Construction de la légende pour les zones protégées
patches_legende = [
    mpatches.Patch(
        facecolor="cyan",
        edgecolor="blue",
        alpha=0.35,
        label="Zone FRA EST",
    )
]

if gdf_geojson is not None:
    patches_legende.append(
        mpatches.Patch(
            facecolor="orange",
            edgecolor="darkred",
            alpha=0.35,
            label="Zone FRA OUEST",
        )
    )

ax2.legend(
    handles=patches_legende,
    loc="upper left",
    fontsize=10,
    framealpha=0.9,
    facecolor="white",
)

ax2.set_title(
    f"Effort de pêche & Zones Protégées — Golfe du Lion\n(Grille"
    f" {TAILLE_CELLULE_M}m + Fond OpenStreetMap)",
    fontsize=14,
    fontweight="bold",
    pad=15,
)

plt.tight_layout()
plt.savefig(FICHIER_SORTIE_CARTE_2, dpi=150, bbox_inches="tight")
plt.close(fig2)
print(f"🖼️ Carte 2 sauvegardée : {FICHIER_SORTIE_CARTE_2}")