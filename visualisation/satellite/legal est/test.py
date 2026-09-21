from PIL import Image
import os
import colorsys

# Dossier contenant les images
input_folder = "images"

# Nom du fichier de sortie
output_file = "grille_7x7.png"

# Paramètres
cols = 7
rows = 7
img_width = 150
img_height = 150
gap = 2  # espace blanc entre les images, en pixels


def average_color(img):
    small = img.resize((1, 1))
    return small.getpixel((0, 0))


def sort_key(img):
    r, g, b = average_color(img)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return (h, s, v)


# Récupérer les fichiers PNG
files = sorted([
    f for f in os.listdir(input_folder)
    if f.lower().endswith(".png")
])

# Charger les images et les trier par couleur moyenne
images = [Image.open(os.path.join(input_folder, f)).convert("RGB") for f in files]
images.sort(key=sort_key)

# Dimensions finales :
# 7 images + 6 espaces de 2 px
# 7 images + 6 espaces de 2 px
grid_width = cols * img_width + (cols - 1) * gap
grid_height = rows * img_height + (rows - 1) * gap

# Créer le canevas blanc
grid = Image.new("RGB", (grid_width, grid_height), "white")

# Ajouter les images
for i, img in enumerate(images):
    col = i % cols
    row = i // cols

    x = col * (img_width + gap)
    y = row * (img_height + gap)

    grid.paste(img, (x, y))

# Sauvegarder
grid.save(output_file)

print(f"Image créée : {output_file}")
print(f"Taille : {grid.size}")