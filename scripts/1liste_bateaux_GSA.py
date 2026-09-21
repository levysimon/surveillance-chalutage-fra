"""
Prend en entrée le fichier excel GFCM-FLeetRegister 
Et nous donne en sortie les bateaux enregistrés et autorisé operant dans la zone donné 
"""
import os
import pandas as pd

dossier_data = "data"

# Identifiant de la GSA recherchée (utilisé dans les noms de fichiers) // GSA7 = Golfe du Lionx
gsa_nom = "GSA7"

# 1. Chargement du fichier dans le dossier /data
chemin_fichier = os.path.join("data", "GFCM-FleetRegister.xlsx")
df = pd.read_excel(chemin_fichier)

# Nettoyage des noms de colonnes
df.columns = df.columns.str.strip()

cols_gsa = ["GSA 1", "GSA 2 (if any)", "GSA 3 (if any)"]
cols_gear = [
    "Fishing gear 1",
    "Fishing gear 2 (if any)",
    "Fishing gear 3 (if any)",
]

# Nettoyage du texte dans les colonnes cibles
for col in cols_gsa + cols_gear:
    if col in df.columns:
        df[col] = df[col].astype(str).str.strip().str.upper()

# Conversion numérique de la taille (LOA)
df["LOA"] = pd.to_numeric(df["LOA"], errors="coerce")

# 2. Critères de filtrage, chalutage demersale
engins_chalutiers = [
    "OTB",
    "PTB",
    "TBB",
    "TB",
    "TBS"
]
gsa_valides = ["7", "7.0", "07", "GSA 7", "GSA7"]

# Masques de filtrage
cond_gsa = df[cols_gsa].isin(gsa_valides).any(axis=1)
cond_gear = df[cols_gear].isin(engins_chalutiers).any(axis=1)
cond_loa = df["LOA"] >= 15.0  # Filtre LOA >= 15m

# Application des 3 conditions combinées
df_15m = df[cond_gsa & cond_gear & cond_loa].copy()

# 3. Noms des fichiers dynamiques avec le nom de la GSA
fichier_csv = os.path.join(
    dossier_data, f"chalutiers_{gsa_nom}.csv"
)
fichier_txt = os.path.join("data", f"statistiques_chalutiers_{gsa_nom}.txt"
)

# Exportation de la liste filtrée en CSV
df_15m.to_csv(fichier_csv, index=False, encoding="utf-8-sig")

# 4. Conversion numérique du Tonnage (GT)
df_15m["GT"] = pd.to_numeric(df_15m["GT"], errors="coerce")

# 5. Calcul et préparation du texte de statistiques
bins_loa = [15, 18, 24, 40, 100]
labels_loa = ["15-18m", "18-24m", "24-40m", "> 40m"]
df_15m["Tranche_LOA"] = pd.cut(
    df_15m["LOA"], bins=bins_loa, labels=labels_loa, include_lowest=True
)

lignes_stats = []
lignes_stats.append("=" * 55)
lignes_stats.append(
    f" STATISTIQUES CHALUTIERS ≥ 15m ({gsa_nom})"
)
lignes_stats.append("=" * 55)
lignes_stats.append(f"Nombre total de bateaux : {len(df_15m)}\n")

lignes_stats.append("--- Longueur Hors Tout (LOA - mètres) ---")
lignes_stats.append(f"Moyenne : {df_15m['LOA'].mean():.2f} m")
lignes_stats.append(f"Médiane : {df_15m['LOA'].median():.2f} m")
lignes_stats.append(
    f"Min / Max : {df_15m['LOA'].min():.2f} m / {df_15m['LOA'].max():.2f} m\n"
)

lignes_stats.append("--- Tonnage (GT - Jauge Brute) ---")
lignes_stats.append(f"Moyenne : {df_15m['GT'].mean():.2f} GT")
lignes_stats.append(f"Médiane : {df_15m['GT'].median():.2f} GT")
lignes_stats.append(
    f"Min / Max : {df_15m['GT'].min():.2f} GT / {df_15m['GT'].max():.2f} GT\n"
)

lignes_stats.append("--- Distribution par Tranche de Taille (LOA) ---")
dist_loa = df_15m["Tranche_LOA"].value_counts().sort_index()
dist_loa_pct = (
    df_15m["Tranche_LOA"].value_counts(normalize=True).sort_index() * 100
)
for tranche, count in dist_loa.items():
    pct = dist_loa_pct[tranche]
    lignes_stats.append(f"  {tranche:<10}: {count:>3} bateaux ({pct:.1f}%)")

rapport_texte = "\n".join(lignes_stats)

# 6. Exportation du rapport TXT dans /data/
with open(fichier_txt, "w", encoding="utf-8") as f:
    f.write(rapport_texte)

print(f"✔ Fichier CSV généré : '{fichier_csv}'")
print(f"✔ Rapport TXT généré : '{fichier_txt}'")