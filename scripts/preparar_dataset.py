"""
Construye el dataset de clasificacion a partir de Open Images V7.

Las imagenes de Open Images suelen contener varios objetos a la vez (una foto
de escritorio trae laptop, teclado y mouse). Para clasificacion necesitamos
muestras de una sola clase, asi que se recorta cada caja delimitadora y se
descartan los recortes demasiado pequenos para ser reconocibles.

Salida:  datos/dataset/{train,val,test}/{clase}/*.jpg
"""
import os
import random
import shutil
from pathlib import Path

from PIL import Image
import fiftyone as fo
import fiftyone.zoo as foz

# Clases de Open Images -> etiqueta usada por el modelo.
# Coinciden con productos reales del inventario.
CLASES = {
    "Laptop": "laptop",
    "Computer monitor": "monitor",
    "Computer keyboard": "teclado",
    "Computer mouse": "mouse",
    "Chair": "silla",
    "Printer": "impresora",
}

IMAGENES_POR_CLASE = 700   # imagenes de Open Images a descargar por clase
AREA_MINIMA = 0.03         # la caja debe ocupar al menos 3% de la imagen
LADO_MINIMO = 80           # pixeles minimos por lado del recorte
MARGEN = 0.08              # margen extra alrededor de la caja
LADO_MAXIMO = 400          # se reescala para no ocupar disco de mas
PROPORCIONES = (0.70, 0.15, 0.15)  # train / val / test

RAIZ = Path(__file__).resolve().parents[2] / "datos" / "dataset"


def recortar(sample, clase_oi, destino, contador):
    """Guarda un recorte por cada deteccion valida de la clase pedida."""
    if not sample.has_field("ground_truth") or sample.ground_truth is None:
        return contador
    try:
        img = Image.open(sample.filepath).convert("RGB")
    except Exception:
        return contador
    W, H = img.size

    for det in sample.ground_truth.detections:
        if det.label != clase_oi:
            continue
        x, y, w, h = det.bounding_box           # relativas [0,1]
        if w * h < AREA_MINIMA:
            continue
        # Margen alrededor de la caja, recortado a los limites de la imagen
        x0 = max(0, (x - w * MARGEN)) * W
        y0 = max(0, (y - h * MARGEN)) * H
        x1 = min(1, (x + w * (1 + MARGEN))) * W
        y1 = min(1, (y + h * (1 + MARGEN))) * H
        if (x1 - x0) < LADO_MINIMO or (y1 - y0) < LADO_MINIMO:
            continue

        recorte = img.crop((int(x0), int(y0), int(x1), int(y1)))
        recorte.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)
        recorte.save(destino / f"{contador:05d}.jpg", quality=90)
        contador += 1
    return contador


def main():
    random.seed(7)
    if RAIZ.exists():
        shutil.rmtree(RAIZ)
    crudos = RAIZ.parent / "recortes"
    if crudos.exists():
        shutil.rmtree(crudos)

    resumen = {}
    for clase_oi, etiqueta in CLASES.items():
        print(f"\n=== {clase_oi} -> {etiqueta} ===", flush=True)
        destino = crudos / etiqueta
        destino.mkdir(parents=True, exist_ok=True)

        nombre = f"oi_{etiqueta}"
        if fo.dataset_exists(nombre):
            fo.delete_dataset(nombre)
        ds = foz.load_zoo_dataset(
            "open-images-v7", split="train", label_types=["detections"],
            classes=[clase_oi], max_samples=IMAGENES_POR_CLASE,
            dataset_name=nombre, shuffle=True, seed=7,
        )

        n = 0
        for sample in ds:
            n = recortar(sample, clase_oi, destino, n)
        fo.delete_dataset(nombre)
        resumen[etiqueta] = n
        print(f"  recortes generados: {n}", flush=True)

    # Reparto estratificado por clase
    print("\n=== Reparto train/val/test ===", flush=True)
    for etiqueta, total in resumen.items():
        archivos = sorted((crudos / etiqueta).glob("*.jpg"))
        random.shuffle(archivos)
        n_train = int(len(archivos) * PROPORCIONES[0])
        n_val = int(len(archivos) * PROPORCIONES[1])
        particiones = {
            "train": archivos[:n_train],
            "val": archivos[n_train:n_train + n_val],
            "test": archivos[n_train + n_val:],
        }
        for particion, items in particiones.items():
            carpeta = RAIZ / particion / etiqueta
            carpeta.mkdir(parents=True, exist_ok=True)
            for f in items:
                shutil.copy2(f, carpeta / f.name)
        print(f"  {etiqueta:10s} train={len(particiones['train']):4d} "
              f"val={len(particiones['val']):3d} test={len(particiones['test']):3d}", flush=True)

    shutil.rmtree(crudos)
    print(f"\nDataset listo en {RAIZ}", flush=True)


if __name__ == "__main__":
    main()
