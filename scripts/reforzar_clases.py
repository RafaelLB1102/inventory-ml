"""
Refuerza las clases con pocas muestras descargando mas imagenes de Open Images,
y recorta las clases sobrerrepresentadas. El objetivo es reducir el desbalance
antes de entrenar, no eliminarlo por completo: un dataset perfectamente
balanceado seria artificial, pero una proporcion de 6 a 1 sesga el modelo.
"""
import random, shutil
from pathlib import Path
from PIL import Image
import fiftyone as fo
import fiftyone.zoo as foz

REFUERZO = {"Printer": ("impresora", 4000), "Computer mouse": ("mouse", 2500)}
TOPE_POR_CLASE = 900           # se recorta lo que exceda
AREA_MINIMA, LADO_MINIMO, MARGEN, LADO_MAXIMO = 0.02, 70, 0.08, 400
PROPORCIONES = (0.70, 0.15, 0.15)

RAIZ = Path(__file__).resolve().parents[2] / "datos" / "dataset"
TMP = RAIZ.parent / "refuerzo"


def recortes_de(sample, clase_oi, destino, n):
    if not sample.has_field("ground_truth") or sample.ground_truth is None:
        return n
    try:
        img = Image.open(sample.filepath).convert("RGB")
    except Exception:
        return n
    W, H = img.size
    for det in sample.ground_truth.detections:
        if det.label != clase_oi:
            continue
        x, y, w, h = det.bounding_box
        if w * h < AREA_MINIMA:
            continue
        x0, y0 = max(0, x - w*MARGEN)*W, max(0, y - h*MARGEN)*H
        x1, y1 = min(1, x + w*(1+MARGEN))*W, min(1, y + h*(1+MARGEN))*H
        if (x1-x0) < LADO_MINIMO or (y1-y0) < LADO_MINIMO:
            continue
        r = img.crop((int(x0), int(y0), int(x1), int(y1)))
        r.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)
        r.save(destino / f"r{n:05d}.jpg", quality=90)
        n += 1
    return n


def main():
    random.seed(7)
    if TMP.exists():
        shutil.rmtree(TMP)

    for clase_oi, (etiqueta, pedir) in REFUERZO.items():
        print(f"\n=== refuerzo {clase_oi} -> {etiqueta} (pidiendo {pedir}) ===", flush=True)
        destino = TMP / etiqueta
        destino.mkdir(parents=True, exist_ok=True)
        nombre = f"ref_{etiqueta}"
        if fo.dataset_exists(nombre):
            fo.delete_dataset(nombre)
        ds = foz.load_zoo_dataset(
            "open-images-v7", split="train", label_types=["detections"],
            classes=[clase_oi], max_samples=pedir, dataset_name=nombre,
            shuffle=True, seed=99,
        )
        n = 0
        for s in ds:
            n = recortes_de(s, clase_oi, destino, n)
        fo.delete_dataset(nombre)
        print(f"  recortes nuevos: {n}", flush=True)

        # Se reparten respetando las proporciones, sin tocar lo ya existente
        archivos = sorted(destino.glob("*.jpg"))
        random.shuffle(archivos)
        n_tr = int(len(archivos)*PROPORCIONES[0]); n_va = int(len(archivos)*PROPORCIONES[1])
        for part, items in (("train", archivos[:n_tr]), ("val", archivos[n_tr:n_tr+n_va]), ("test", archivos[n_tr+n_va:])):
            carpeta = RAIZ / part / etiqueta
            carpeta.mkdir(parents=True, exist_ok=True)
            for f in items:
                shutil.copy2(f, carpeta / f.name)

    # Recorte de las clases dominantes
    print("\n=== aplicando tope por clase ===", flush=True)
    for part, cuota in (("train", int(TOPE_POR_CLASE*PROPORCIONES[0])),
                        ("val", int(TOPE_POR_CLASE*PROPORCIONES[1])),
                        ("test", int(TOPE_POR_CLASE*PROPORCIONES[2]))):
        for carpeta in sorted((RAIZ/part).iterdir()):
            archivos = sorted(carpeta.glob("*.jpg"))
            if len(archivos) > cuota:
                random.shuffle(archivos)
                for f in archivos[cuota:]:
                    f.unlink()
                print(f"  {part}/{carpeta.name}: recortado a {cuota}", flush=True)

    shutil.rmtree(TMP)
    print("\nlisto", flush=True)


if __name__ == "__main__":
    main()
