"""
Va lire les fichiers .csv pour chaque bateau, fais un k-means hybride. On fixe des regles deterministe pour le port et la derive. 
Mais on le laisse choisir pour le transit et le chalutage
"""

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.cluster import KMeans

# ==============================================================================
# 1. PARAMÈTRES ET CONFIGURATION
# ==============================================================================
DOSSIER_CSV = os.path.join("data", "ais", "csv")
DOSSIER_EXPORT = os.path.join("data", "ais", "processed")

# Critères "Au Port" unifiés avec le pipeline de détection des sorties
SPEED_MAX_PORT = 0.5       # Vitesse max au port (kts)
DEPTH_MAX_PORT = -5.0      # Profondeur max au port (mètres)
MAX_GAP_H = 8.0            # Seuil de trou de signal AIS (heures)
MIN_POINTS_RETOUR = 1      # Nombre de points min pour valider un bloc port

# Filtres de nettoyage des pings aberrants
DEPTH_MAX_VALIDE = 2.0     # Suppression pings > 2m (altitude)
SPEED_MAX_VALIDE = 30.0    # Suppression pings > 30 kts (sauts GPS)

# ==============================================================================
# 2. FONCTIONS DE CALCUL & CLUSTERING
# ==============================================================================
def detecter_port_unifie(df_bateau):
    """
    Applique la règle de port unifiée : (speed <= 0.5 & depth >= -5) + gestion des trous AIS.
    """
    df = df_bateau.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    df["gap_h"] = df["timestamp"].diff().dt.total_seconds() / 3600
    df["trou_signal"] = df["gap_h"] > MAX_GAP_H

    # Règle métier déterministe validée
    df["au_port_raw"] = (df["speed"] <= SPEED_MAX_PORT) & (df["depth"] >= DEPTH_MAX_PORT)

    # Découpage en blocs continus
    df["changement"] = (df["au_port_raw"] != df["au_port_raw"].shift(1)) | df["trou_signal"]
    df["bloc_id"] = df["changement"].cumsum()

    blocs_info = (
        df.groupby("bloc_id")
        .agg(au_port_raw=("au_port_raw", "first"), nb_points=("timestamp", "size"))
        .reset_index()
    )
    blocs_info["au_port_valide"] = blocs_info["au_port_raw"] & (blocs_info["nb_points"] >= MIN_POINTS_RETOUR)

    df = df.merge(blocs_info[["bloc_id", "au_port_valide"]], on="bloc_id", how="left")
    return df


def traiter_flotte_unifiee(df_points):
    df_res = df_points.copy()
    df_res["cluster_id"] = np.nan
    rapport_flotte = []
    groupes_traites = []

    for bateau, group in df_res.groupby("bateau"):
        nb_total = len(group)
        if nb_total < 20:
            continue

        # A. Identification du port (Règle unifiée)
        group = detecter_port_unifie(group)

        # 1. Port : Immobile ET en zone côtière/portuaire
        group.loc[group["au_port_valide"], "cluster_id"] = 0
        
        mask_mer = ~group["au_port_valide"]
        
        # 2. Transit Chenal / Côte : Vitesse > 0.5 mais depth >= -5
        mask_chenal = mask_mer & (group["depth"] >= DEPTH_MAX_PORT)
        group.loc[mask_chenal, "cluster_id"] = 2
        
        # 3. Dérive / Arrêt en mer : Immobile (<= 0.5 kts) au large (depth < -5)
        mask_derive = mask_mer & (group["depth"] < DEPTH_MAX_PORT) & (group["speed"] <= SPEED_MAX_PORT)
        group.loc[mask_derive, "cluster_id"] = 3  # Régime "Dérive / Arrêt en mer"
        
        # 4. K-Means (Pêche vs Transit) uniquement sur les points en route au large (> 0.5 kts)
        mask_large_en_route = mask_mer & (group["depth"] < DEPTH_MAX_PORT) & (group["speed"] > SPEED_MAX_PORT)
        group_large = group[mask_large_en_route].copy()
        
        if len(group_large) >= 10:
            X_large = group_large[["speed"]].values

            km = KMeans(n_clusters=2, random_state=42, n_init=10)
            labels_large = km.fit_predict(X_large)

            centres = km.cluster_centers_.flatten()
            ordre = np.argsort(centres) # 0: Pêche, 1: Transit
            mapping = {ancien: (nouveau + 1) for nouveau, ancien in enumerate(ordre)}

            group.loc[group_large.index, "cluster_id"] = [mapping[l] for l in labels_large]

            v_peche = centres[ordre[0]]
            v_transit = centres[ordre[1]]
            seuil_p_t = (v_peche + v_transit) / 2
        else:
            v_peche, v_transit, seuil_p_t = np.nan, np.nan, np.nan

        v_arret = group.loc[group["cluster_id"] == 0, "speed"].mean()

        rapport_flotte.append({
            "bateau": bateau,
            "total_pings": nb_total,
            "pct_arret": round(group["au_port_valide"].mean() * 100, 1),
            "v_arret": round(v_arret, 2) if not np.isnan(v_arret) else 0.0,
            "v_peche": round(v_peche, 2) if not np.isnan(v_peche) else None,
            "seuil_p_t_kts": round(seuil_p_t, 2) if not np.isnan(seuil_p_t) else None,
            "v_transit": round(v_transit, 2) if not np.isnan(v_transit) else None,
        })

        groupes_traites.append(group)

    df_final = pd.concat(groupes_traites, ignore_index=True)
    
    # Cast explicite en type entier (supportant NaN) pour un mapping propre
    df_final["cluster_id"] = df_final["cluster_id"].astype("Int64")
    
    labels_regimes = {
        0: "0. Arrêt / Port",
        1: "1. Pêche",
        2: "2. Transit",
        3: "3. Dérive / Arrêt en mer"
    }
    df_final["regime"] = df_final["cluster_id"].map(labels_regimes)

    return df_final, pd.DataFrame(rapport_flotte)


# ==============================================================================
# 3. CHARGEMENT ET NETTOYAGE DES DONNÉES ET SAUVEGARDE DU RAPPORT
# ==============================================================================
FICHIER_RAPPORT = "./data/k-means-vitesse.txt"

with open(FICHIER_RAPPORT, "w", encoding="utf-8") as report:

    def log(message=""):
        report.write(str(message) + "\n")

    fichiers = [f for f in os.listdir(DOSSIER_CSV) if f.endswith(".csv")]
    tous_les_points = []
    log("--- Chargement des fichiers CSV ---")

    for nom_fichier in fichiers:
        chemin_complet = os.path.join(DOSSIER_CSV, nom_fichier)
        df_raw = pd.read_csv(chemin_complet)

        nom_bateau = nom_fichier.split(" (")[0].replace(".csv", "")
        df_raw["bateau"] = nom_bateau
        tous_les_points.append(df_raw)

    df = pd.concat(tous_les_points, ignore_index=True)

    # Nettoyage des pings aberrants
    taille_initiale = len(df)
    df_clean = df.dropna(subset=["speed", "depth"]).copy()
    df_clean = df_clean[
        (df_clean["depth"] <= DEPTH_MAX_VALIDE)
        & (df_clean["speed"] <= SPEED_MAX_VALIDE)
        & (df_clean["speed"] >= 0)
    ].copy()

    nb_suppr = taille_initiale - len(df_clean)
    log(
        f"✅ Nettoyage : {nb_suppr:,} pings supprimés ({nb_suppr/taille_initiale*100:.2f}%)."
    )
    log(f"📊 Pings valides retenus : {len(df_clean):,}\n")

    # ==============================================================================
    # 4. EXÉCUTION DU CLUSTERING UNIFIÉ
    # ==============================================================================
    log(
        "=== Application du Pipeline Unifié (Port unifié + K-Means 1D Vitesse) ==="
    )
    df_clean, df_synth_flotte = traiter_flotte_unifiee(df_clean)

    # ==============================================================================
    # 5. RÉSULTATS & SYNTHÈSE
    # ==============================================================================
    log("\n--- SYNTHÈSE PAR BATEAU ---")
    log(df_synth_flotte.to_string(index=False))

    log("\n--- STATISTIQUES GLOBALES TOUS BATEAUX CONFONDUS ---")
    stats_globales = (
        df_clean.groupby("regime")
        .agg(
            vitesse_moy=("speed", "mean"),
            vitesse_min=("speed", "min"),
            vitesse_max=("speed", "max"),
            prof_moyenne=("depth", "mean"),
            nb_pings=("speed", "count"),
        )
        .round(2)
    )
    log(stats_globales.to_string())

# ==============================================================================
# 6. VISUALISATION DES RÉSULTATS
# ==============================================================================
plt.figure(figsize=(10, 5))
palette_couleurs = {
    "0. Arrêt / Port": "gray", 
    "1. Pêche": "dodgerblue", 
    "2. Transit": "crimson",
    "3. Dérive / Arrêt en mer": "orange"
}

sns.histplot(
    data=df_clean, 
    x="speed", 
    hue="regime", 
    bins=60, 
    palette=palette_couleurs,
    element="step",
    alpha=0.6
)
plt.axvline(SPEED_MAX_PORT, color="black", linestyle="--", label=f"Seuil Max Port ({SPEED_MAX_PORT} kts)")
plt.title("Classification de la flotte (Port Unifié + K-Means 1D Vitesse)", fontweight="bold")
plt.xlabel("Vitesse (nœuds)")
plt.ylabel("Nombre de pings")
plt.legend(title="Régime")
plt.grid(axis="y", linestyle="--", alpha=0.5)
plt.tight_layout()
plt.show()

# ==============================================================================
# 7. EXPORT DES CSV ANNOTÉS
# ==============================================================================
mapping_status = {
    0: "arrêt",
    1: "pêche",
    2: "transit",
    3: "dérive"
}
df_clean["status"] = df_clean["cluster_id"].map(mapping_status)

os.makedirs(DOSSIER_EXPORT, exist_ok=True)
print(f"\n--- Sauvegarde des fichiers annotés dans '{DOSSIER_EXPORT}' ---")

for bateau, group in df_clean.groupby("bateau"):
    cols_a_retirer = ["cluster_id", "regime", "au_port_raw", "changement", "bloc_id", "au_port_valide", "gap_h", "trou_signal"]
    df_export = group.drop(columns=cols_a_retirer, errors="ignore")
    
    chemin_fichier_export = os.path.join(DOSSIER_EXPORT, f"{bateau}_annotated.csv")
    df_export.to_csv(chemin_fichier_export, index=False)
    print(f"💾 {bateau} ({len(df_export):,} pings) -> {chemin_fichier_export}")

print("\nDone")