from PIL import Image
import os

# Dossier contenant les images
input_folder = "images"

# Nom du fichier de sortie
output_file = "grille_6x3.png"

# Paramètres
cols = 6
rows = 3
img_width = 150 
img_height = 150
gap = 2  # espace blanc entre les images, en pixels

# Récupérer les fichiers PNG
files = sorted([
    f for f in os.listdir(input_folder)
    if f.lower().endswith(".png")
])



# Dimensions finales :
# 7 images + 6 espaces de 2 px
# 3 images + 2 espaces de 2 px
grid_width = cols * img_width + (cols - 1) * gap
grid_height = rows * img_height + (rows - 1) * gap

# Créer le canevas blanc
grid = Image.new("RGB", (grid_width, grid_height), "white")

# Ajouter les images
for i, filename in enumerate(files):
    img = Image.open(os.path.join(input_folder, filename)).convert("RGB")

  

    col = i % cols
    row = i // cols

    x = col * (img_width + gap)
    y = row * (img_height + gap)

    grid.paste(img, (x, y))

# Sauvegarder
grid.save(output_file)

print(f"Image créée : {output_file}")
print(f"Taille : {grid.size}")
