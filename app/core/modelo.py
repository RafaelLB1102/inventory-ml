"""
Carga del modelo ONNX y preprocesamiento de imágenes.

El preprocesamiento replica exactamente el usado en evaluación durante el
entrenamiento (Resize(256) + CenterCrop(224) + Normalize con estadísticas de
ImageNet). Cualquier diferencia aquí produciría predicciones silenciosamente
incorrectas: el modelo no fallaría, simplemente acertaría menos y nadie se
enteraría. Por eso los parámetros se leen de metadatos.json en lugar de
escribirse a mano.
"""
import json
import logging
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

from app.core.config import settings

log = logging.getLogger(__name__)


class ErrorImagen(ValueError):
    """La imagen no se puede procesar. Se traduce a HTTP 400."""


class Clasificador:
    def __init__(self, ruta_modelo: str, ruta_metadatos: str):
        base = Path(__file__).resolve().parents[2]
        self.ruta_modelo = base / ruta_modelo
        self.ruta_metadatos = base / ruta_metadatos

        if not self.ruta_modelo.exists():
            raise FileNotFoundError(f"No se encontró el modelo en {self.ruta_modelo}")

        self.metadatos = json.loads(self.ruta_metadatos.read_text(encoding="utf-8"))
        self.clases: list[str] = self.metadatos["clases"]
        self.tam: int = self.metadatos["tam_entrada"]
        self.media = np.array(self.metadatos["normalizacion"]["media"], dtype=np.float32)
        self.desv = np.array(self.metadatos["normalizacion"]["desv"], dtype=np.float32)

        # Un solo hilo por sesión: en Fargate la tarea tiene poca CPU y varios
        # hilos compitiendo empeoran la latencia en vez de mejorarla.
        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = 1
        opciones.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.sesion = ort.InferenceSession(
            str(self.ruta_modelo),
            sess_options=opciones,
            providers=["CPUExecutionProvider"],
        )
        self.entrada = self.sesion.get_inputs()[0].name
        log.info(
            "Modelo cargado: %s clases=%s tam=%d",
            self.ruta_modelo.name, self.clases, self.tam,
        )

    # ------------------------------------------------------------------ #

    def preparar(self, datos: bytes) -> tuple[np.ndarray, tuple[int, int]]:
        """Convierte los bytes de una imagen en el tensor que espera el modelo."""
        from io import BytesIO

        try:
            img = Image.open(BytesIO(datos))
            img.verify()                      # detecta archivos truncados o corruptos
            img = Image.open(BytesIO(datos))  # verify() deja el objeto inutilizable
            # Respeta la orientación EXIF: una foto de celular puede venir
            # rotada y el modelo la vería de lado.
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
        except ErrorImagen:
            raise
        except Exception as exc:
            raise ErrorImagen(f"El archivo no es una imagen válida ({type(exc).__name__})")

        ancho, alto = img.size
        if min(ancho, alto) < settings.LADO_MINIMO_PX:
            raise ErrorImagen(
                f"La imagen es demasiado pequeña ({ancho}x{alto}); "
                f"el lado menor debe superar {settings.LADO_MINIMO_PX} píxeles"
            )

        # Resize del lado corto a 256, preservando proporción
        corto = 256
        if ancho < alto:
            nuevo = (corto, max(1, round(corto * alto / ancho)))
        else:
            nuevo = (max(1, round(corto * ancho / alto)), corto)
        img = img.resize(nuevo, Image.BILINEAR)

        # Recorte central de 224x224
        w, h = img.size
        izq = (w - self.tam) // 2
        arr = (w + self.tam) // 2
        sup = (h - self.tam) // 2
        inf = (h + self.tam) // 2
        img = img.crop((izq, sup, arr, inf))

        x = np.asarray(img, dtype=np.float32) / 255.0      # HWC en [0,1]
        x = (x - self.media) / self.desv                   # normalización ImageNet
        x = np.transpose(x, (2, 0, 1))[None, ...]          # -> NCHW
        return np.ascontiguousarray(x, dtype=np.float32), (ancho, alto)

    def predecir(self, datos: bytes) -> dict:
        """Devuelve la clase predicha, su confianza y todas las probabilidades."""
        tensor, dimensiones = self.preparar(datos)

        t0 = time.perf_counter()
        logits = self.sesion.run(None, {self.entrada: tensor})[0][0]
        latencia_ms = (time.perf_counter() - t0) * 1000

        # Softmax estable: restar el máximo evita desbordar al exponenciar
        e = np.exp(logits - logits.max())
        probabilidades = e / e.sum()

        indice = int(probabilidades.argmax())
        confianza = float(probabilidades[indice])

        return {
            "clase": self.clases[indice],
            "confianza": round(confianza, 4),
            "incierta": confianza < settings.UMBRAL_CONFIANZA,
            "probabilidades": {
                c: round(float(p), 4) for c, p in zip(self.clases, probabilidades)
            },
            "latencia_ms": round(latencia_ms, 2),
            "dimensiones": dimensiones,
        }


_clasificador: Clasificador | None = None


def cargar_clasificador() -> Clasificador:
    """Carga el modelo una sola vez, al arrancar el proceso."""
    global _clasificador
    if _clasificador is None:
        _clasificador = Clasificador(settings.RUTA_MODELO, settings.RUTA_METADATOS)
    return _clasificador


def obtener_clasificador() -> Clasificador | None:
    return _clasificador
