"""
Va reunir ensuite les pings "peche" en evenements de pêche. 
Il faut au minimum 3 pings d'affilée comme "peche" dans un intervalle de 4h pour être defini comme un evenement de peche 
"""

import os
import glob
import pandas as pd

# =============================================================================
# CONFIGURATION
# =============================================================================

DOSSIER_PROCESSED = os.path.join("data", "ais", "processed")
DOSSIER_EXPORT = os.path.join("data", "evenement de peche")
os.makedirs(DOSSIER_EXPORT, exist_ok=True)

NB_PINGS_MIN = 3        # une séquence de pêche doit avoir au moins ce nb de points
MAX_GAP_H = 4.0         # au-delà, un trou AIS coupe la séquence en cours
FUSION_PECHE_H = 2.0    # deux séquences de pêche séparées de moins de X h
                        # (par du transit ou un trou court) sont fusionnées
                        # en un seul événement


# =============================================================================
# 1. LECTURE DES FICHIERS ANNOTÉS
# =============================================================================

fichiers = sorted(glob.glob(os.path.join(DOSSIER_PROCESSED, "*_annotated.csv")))
if not fichiers:
    raise FileNotFoundError(f"Aucun fichier *_annotated.csv trouvé dans '{DOSSIER_PROCESSED}'")

print(f"{len(fichiers)} fichier(s) trouvé(s).")

dfs = []
for fichier in fichiers:
    df_tmp = pd.read_csv(fichier)
    df_tmp["timestamp"] = pd.to_datetime(df_tmp["timestamp"], utc=True, errors="coerce")
    df_tmp["status"] = df_tmp["status"].astype("string").str.strip().str.lower()
    df_tmp = df_tmp.dropna(subset=["timestamp", "bateau", "status"])
    dfs.append(df_tmp)

df = pd.concat(dfs, ignore_index=True)
df = df.sort_values(["bateau", "timestamp"]).reset_index(drop=True)

print(f"Total de pings chargés : {len(df):,}")


# =============================================================================
# 2. DÉCOUPAGE EN SÉQUENCES CONTINUES DE PÊCHE
# =============================================================================
# Une séquence de pêche est un groupe de points consécutifs avec status="pêche",
# sans trou AIS trop grand à l'intérieur.

df["gap_h"] = df.groupby("bateau")["timestamp"].diff().dt.total_seconds() / 3600
df["gap_h"] = df["gap_h"].fillna(0)

df["est_peche"] = df["status"] == "pêche"

# Nouvelle séquence si : changement pêche <-> non-pêche, OU trou AIS trop grand
changement_statut = df["est_peche"] != df.groupby("bateau")["est_peche"].shift(1)
trou_trop_grand = df["gap_h"] > MAX_GAP_H
df["nouvelle_sequence"] = changement_statut | trou_trop_grand
df["sequence_id"] = df.groupby("bateau")["nouvelle_sequence"].cumsum()

sequences = (
    df[df["est_peche"]]
    .groupby(["bateau", "sequence_id"])
    .agg(debut=("timestamp", "min"), fin=("timestamp", "max"), nb_pings=("timestamp", "size"))
    .reset_index()
)

sequences = sequences[sequences["nb_pings"] >= NB_PINGS_MIN].copy()
sequences = sequences.sort_values(["bateau", "debut"]).reset_index(drop=True)

print(f"Séquences de pêche (≥ {NB_PINGS_MIN} pings) : {len(sequences):,}")


# =============================================================================
# 3. FUSION DES SÉQUENCES PROCHES EN ÉVÉNEMENTS
# =============================================================================
# Deux séquences du même bateau sont fusionnées en un seul événement si
# l'écart entre elles est inférieur à FUSION_PECHE_H (ex: un court transit
# entre deux traits de chalut ne doit pas compter comme deux sorties de pêche
# distinctes).

fin_precedente = sequences.groupby("bateau")["fin"].shift(1)
ecart_h = (sequences["debut"] - fin_precedente).dt.total_seconds() / 3600

nouvel_evenement = ecart_h.isna() | (ecart_h > FUSION_PECHE_H)
sequences["evenement_id"] = sequences.groupby("bateau")[
    "sequence_id"
].transform(lambda s: nouvel_evenement.loc[s.index].cumsum())

evenements = (
    sequences
    .groupby(["bateau", "evenement_id"])
    .agg(
        debut=("debut", "min"), 
        fin=("fin", "max"), 
        nb_sequences=("sequence_id", "size"),
        nb_pings=("nb_pings", "sum")  # AJOUT : total des pings de l'événement
    )
    .reset_index()
)

evenements["duree_h"] = (evenements["fin"] - evenements["debut"]).dt.total_seconds() / 3600
evenements = evenements.sort_values(["bateau", "debut"]).reset_index(drop=True)
evenements["event_peche"] = evenements.groupby("bateau").cumcount() + 1

# AJOUT : Création d'un ID unique pour chaque événement (ex: "MonBateau_evt_1")
evenements["event_id_unique"] = evenements["bateau"].astype(str) + "_evt_" + evenements["event_peche"].astype(str)


# =============================================================================
# 4. GARANTIE D'ABSENCE DE CHEVAUCHEMENT
# =============================================================================
# Par construction (fusion par intervalle de temps sur des séquences déjà
# triées et non chevauchantes à la base), les événements ne devraient jamais
# se chevaucher. On le vérifie explicitement pour être sûr.

chevauchement_detecte = False
for bateau, grp in evenements.groupby("bateau"):
    grp = grp.sort_values("debut")
    fin_prec = grp["fin"].shift(1)
    if (grp["debut"] < fin_prec).any():
        chevauchement_detecte = True
        print(f"  ⚠️ Chevauchement détecté pour {bateau} — à vérifier.")

if not chevauchement_detecte:
    print("✅ Aucun chevauchement entre événements de pêche.")

# =============================================================================
# 5. STATISTIQUES ET EXPORT DES 3 FICHIERS
# =============================================================================

# Calcul des statistiques par bateau
stats_bateaux = (
    evenements.groupby("bateau")
    .agg(
        nb_evenements=("event_peche", "size"),
        duree_totale_h=("duree_h", "sum"),
        duree_moy_h=("duree_h", "mean"),
        duree_mediane_h=("duree_h", "median"),
    )
    .round(2)
    .reset_index()
)

# =============================================================================
# 6. EXPORTS
# =============================================================================

# --- Fichier 1 : Statistiques complètes (TXT) ---
fichier_stats_txt = os.path.join(DOSSIER_EXPORT, "stats_peche_bateaux.txt")

with open(fichier_stats_txt, "w", encoding="utf-8") as f:
    f.write(f"Événements de pêche : {len(evenements):,}\n")
    f.write(
        f"Événements issus de plusieurs séquences fusionnées :"
        f" {(evenements['nb_sequences'] > 1).sum():,}\n\n"
    )

    f.write("--- ÉVÉNEMENTS PAR BATEAU ---\n")
    f.write(stats_bateaux.to_string(index=False) + "\n\n")

    f.write(
        "--- Distribution du nombre de séquences fusionnées par événement ---\n"
    )
    f.write(evenements["nb_sequences"].value_counts().sort_index().to_string())

print(f"💾 1. Statistiques exportées (TXT) : {fichier_stats_txt}")


# --- Fichier 2 : Événements de pêche (CSV) ---
colonnes_export_evt = [
    "event_id_unique",
    "bateau",
    "event_peche",
    "debut",
    "fin",
    "duree_h",
    "nb_sequences",
    "nb_pings",
]
fichier_evenements = os.path.join(DOSSIER_EXPORT, "evenements_peche.csv")
evenements[colonnes_export_evt].to_csv(fichier_evenements, index=False)
print(f"💾 2. Événements exportés (CSV) : {fichier_evenements}")


# --- Fichier 3 : Pings détaillés liés aux événements (CSV) ---
mapping_seq_evt = pd.merge(
    sequences[["bateau", "sequence_id", "evenement_id"]],
    evenements[["bateau", "evenement_id", "event_id_unique"]],
    on=["bateau", "evenement_id"],
)

df_pings_peche = pd.merge(
    df[df["est_peche"]],
    mapping_seq_evt[["bateau", "sequence_id", "event_id_unique"]],
    on=["bateau", "sequence_id"],
    how="inner",
)

colonnes_pings_voulues = [
    "lon",
    "lat",
    "course",
    "timestamp",
    "speed",
    "depth",
    "seg_id",
    "bateau",
    "status",
    "event_id_unique",
]
colonnes_pings_finales = [
    c for c in colonnes_pings_voulues if c in df_pings_peche.columns
]

fichier_pings = os.path.join(DOSSIER_EXPORT, "pings_evenements_peche.csv")
df_pings_peche[colonnes_pings_finales].to_csv(fichier_pings, index=False)
print(f"💾 3. Pings des événements exportés (CSV) : {fichier_pings}")