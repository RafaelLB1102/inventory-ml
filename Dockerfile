FROM python:3.12-slim

WORKDIR /app

# Sin esto Python bufferiza stdout y los logs se pierden si el contenedor muere,
# que es justo cuando mas falta hacen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Las dependencias se instalan antes de copiar el codigo para que Docker
# reutilice la capa cuando solo cambia la aplicacion.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El modelo se copia dentro de la imagen: evita una dependencia de red al
# arrancar y garantiza que la version desplegada sea exactamente la evaluada.
COPY modelo/clasificador.onnx modelo/metadatos.json ./modelo/
COPY app ./app

EXPOSE 8000

# Un solo worker: onnxruntime ya usa la CPU disponible y varios procesos
# cargarian el modelo varias veces en memoria.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
