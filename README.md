# inventory-ml

Servicio de clasificación de imágenes de productos de inventario.

Recibe la fotografía de un producto y devuelve su categoría entre seis
posibles: **impresora, laptop, monitor, mouse, silla y teclado**. Forma parte
del sistema de inventario desplegado en AWS, junto a
[inventory-be](https://github.com/RafaelLB1102/inventory-be) (API de negocio) e
[inventory-fe](https://github.com/RafaelLB1102/inventory-fe) (interfaz web).

---

## El modelo

MobileNetV3-Large preentrenada en ImageNet, adaptada por transfer learning y
exportada a ONNX para la inferencia.

| Métrica | Valor |
|---|---|
| Exactitud sobre el conjunto de prueba | **92,4 %** |
| F1 macro | 0,924 |
| Latencia mediana (CPU, una imagen) | **2,2 ms** |
| Tamaño del archivo ONNX | 16 MB |

Desempeño por categoría:

| Categoría | Precisión | Recall | F1 |
|---|---|---|---|
| mouse | 0,985 | 0,985 | 0,985 |
| impresora | 0,986 | 0,907 | 0,944 |
| silla | 0,961 | 0,911 | 0,935 |
| monitor | 0,889 | 0,948 | 0,918 |
| teclado | 0,808 | 0,981 | 0,886 |
| laptop | 0,945 | 0,812 | 0,874 |

La categoría más difícil es `laptop`, y la razón es semántica más que técnica:
una laptop **contiene** un teclado y una pantalla, de modo que el modelo la
confunde con `teclado` (12 casos) y `monitor` (9 casos). Eso explica también la
precisión baja de `teclado`, que absorbe esos errores.

### Predicciones inciertas

Durante la evaluación la confianza media fue de 0,88 en los aciertos y 0,59 en
los errores. El servicio aprovecha esa separación: cuando la confianza no
supera `UMBRAL_CONFIANZA` (0,60 por defecto), la respuesta se marca como
`incierta`. Así una predicción frágil se distingue de una sólida en lugar de
presentarse con la misma autoridad.

---

## El dataset

4.706 imágenes obtenidas de **Open Images V7**, recortadas por su caja
delimitadora.

El recorte no es un detalle menor. Open Images etiqueta escenas completas: una
fotografía de escritorio contiene laptop, teclado y mouse a la vez. Entrenar
con la imagen entera le enseñaría al modelo asociaciones falsas entre clases.
Recortando cada caja se obtienen muestras limpias de una sola categoría.

| Categoría | train | val | test |
|---|---|---|---|
| mouse | 630 | 135 | 135 |
| silla | 630 | 135 | 135 |
| monitor | 625 | 133 | 135 |
| laptop | 593 | 127 | 128 |
| teclado | 475 | 101 | 103 |
| impresora | 339 | 72 | 75 |

El desbalance restante (1,9 a 1) se compensa durante el entrenamiento con un
muestreador ponderado, no descartando datos.

### Reproducir el dataset

```bash
python -m venv .venv && source .venv/bin/activate
pip install fiftyone pillow

python scripts/preparar_dataset.py    # descarga y recorta (~20 min)
python scripts/reforzar_clases.py     # equilibra las clases
```

Las imágenes quedan en `../datos/dataset/{train,val,test}/{categoría}/`.
La primera ejecución descarga ~5 GB de metadatos de Open Images; es un costo
único que queda en caché.

### Reentrenar el modelo

```bash
pip install torch torchvision onnx onnxruntime onnxscript scikit-learn matplotlib seaborn jupyter
jupyter nbconvert --to notebook --execute --inplace notebooks/entrenamiento.ipynb
```

En un Apple M4 Pro el entrenamiento completo (14 épocas) tarda unos 140
segundos. Genera `modelo/clasificador.onnx`, `modelo/metadatos.json` y las
gráficas de evaluación.

---

## La API

Todos los endpoints bajo `/ml` requieren el token JWT que emite
`inventory-be` en `/api/auth/login`. Documentación interactiva en `/ml/docs`.

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/ml/predict` | Clasifica una imagen (multipart) |
| `GET` | `/ml/history` | Historial paginado del usuario |
| `DELETE` | `/ml/history/{id}` | Elimina un registro propio |
| `GET` | `/ml/stats` | Métricas de uso del servicio |
| `GET` | `/ml/model` | Métricas del modelo |
| `GET` | `/health` | Sonda de salud (sin prefijo) |

### Ejemplo

```bash
TOKEN=$(curl -s -X POST https://<dominio>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"usuario@ejemplo.com","password":"..."}' | jq -r .access_token)

curl -X POST https://<dominio>/ml/predict \
  -H "Authorization: Bearer $TOKEN" \
  -F "imagen=@laptop.jpg"
```

```json
{
  "id": "3f2b1c8e-5d4a-4b6c-9e8f-1a2b3c4d5e6f",
  "clase": "laptop",
  "confianza": 0.9412,
  "incierta": false,
  "probabilidades": {
    "impresora": 0.0021, "laptop": 0.9412, "monitor": 0.0385,
    "mouse": 0.0009, "silla": 0.0031, "teclado": 0.0142
  },
  "latencia_ms": 2.31,
  "creado_en": "2026-09-20T15:04:05Z"
}
```

### Validaciones

| Situación | Respuesta |
|---|---|
| Tipo distinto de JPEG, PNG o WebP | `415` |
| Archivo corrupto o que no es imagen | `400` |
| Lado menor a 32 px | `400` |
| Archivo mayor a 5 MB | `413` |
| Token ausente o inválido | `401` |
| Modelo no cargado | `503` |

---

## Ejecución local

Requiere PostgreSQL accesible. Se puede levantar con el `docker-compose.yml`
del repositorio de `inventory-be`.

```bash
cp .env.example .env     # ajusta SECRET_KEY al mismo valor de inventory-be
pip install -r requirements.txt
uvicorn app.main:app --port 8100
```

`SECRET_KEY` **debe coincidir** con la de `inventory-be`: este servicio no
emite tokens, solo valida los que aquel firma.

Con `BUCKET_IMAGENES` vacío las imágenes no se almacenan y la predicción
funciona igual; el historial queda sin vista previa.

### Con Docker

```bash
docker build -t inventory-ml .
docker run -p 8100:8000 --env-file .env inventory-ml
```

---

## Despliegue en AWS

El servicio corre en **ECS Fargate**, detrás del mismo Application Load
Balancer que `inventory-be` pero con su propio *target group*. CloudFront
enruta `/ml/*` hacia él.

```
CloudFront
  ├── /api/*  →  ALB  →  inventory-be   (Fargate)
  ├── /ml/*   →  ALB  →  inventory-ml   (Fargate)  ← este servicio
  └── resto   →  S3 (frontend)
                          │
                    RDS PostgreSQL (privada)
                    S3 (imágenes subidas)
```

### 1. Publicar la imagen

La estación de desarrollo es Apple Silicon (ARM) y Fargate ejecuta `x86_64`.
Sin declarar la plataforma la imagen falla al desplegarse con un error poco
descriptivo.

```bash
aws ecr create-repository --repository-name inventory-ml \
    --image-scanning-configuration scanOnPush=true

aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin <cuenta>.dkr.ecr.us-east-1.amazonaws.com

docker build --platform linux/amd64 \
  -t <cuenta>.dkr.ecr.us-east-1.amazonaws.com/inventory-ml:latest .
docker push <cuenta>.dkr.ecr.us-east-1.amazonaws.com/inventory-ml:latest
```

### 2. Configuración

Los valores no sensibles van como variables de entorno en la definición de
tarea. Los secretos se referencian **por ARN** desde SSM Parameter Store, de
modo que su valor nunca aparece en la configuración del servicio.

| Variable | Origen |
|---|---|
| `ENVIRONMENT` | `production` |
| `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER` | variable de entorno |
| `ALLOWED_ORIGINS` | dominio de CloudFront |
| `BUCKET_IMAGENES` | bucket de imágenes subidas |
| `POSTGRES_PASSWORD` | SSM `/inventory/prod/db/password` (SecureString) |
| `SECRET_KEY` | SSM `/inventory/prod/jwt/secret` (SecureString) |

En producción el contenedor **se niega a arrancar** si falta `SECRET_KEY`, si
`ALLOWED_ORIGINS` es `*` o si no hay bucket configurado. Fallar de forma
ruidosa es preferible a operar inseguro en silencio.

### 3. Permisos

El rol de ejecución de la tarea necesita leer `/inventory/prod/*` en SSM y
descifrar con KMS. El rol de la tarea necesita `s3:PutObject` y `s3:GetObject`
limitados al bucket de imágenes.

### 4. Recursos

0,5 vCPU y 1 GB de memoria. La imagen pesa 164 MB y el modelo ocupa unos 60 MB
en memoria; el resto es margen para `onnxruntime` y las peticiones
concurrentes.

### 5. Operación

La carpeta `infra/` contiene los guiones con que se opera el sistema completo
(inventario y clasificador). Leen los identificadores de
`infra/aws-resources.env`, que no se versiona; `aws-resources.env.example`
lista las variables que espera.

| Guion | Efecto |
|---|---|
| `encender.sh` | Restaura la base desde el snapshot, recrea el balanceador con las reglas `/api` y `/ml`, levanta ambos servicios, reapunta CloudFront y regenera el panel de monitoreo |
| `apagar.sh` | Lleva los servicios a cero, elimina el balanceador y guarda la base en un snapshot. Costo residual menor a 1 USD al mes |
| `dashboard.sh` | Crea el panel de CloudWatch y las alarmas |
| `destruir.sh` | Elimina **toda** la infraestructura de forma irreversible. Pide confirmación escrita |

El balanceador se elimina en cada apagado porque no admite pausa y factura por
hora. Como se recrea con un identificador distinto, `encender.sh` reapunta
CloudFront y regenera el panel: un dashboard fijo quedaría mostrando gráficas
vacías de un balanceador inexistente sin dar ningún error.

## Monitoreo

Panel de CloudWatch `inventory-sistema`:

- Peticiones, latencia p95 y errores 4xx/5xx por servicio (métricas del ALB)
- CPU y memoria de ambos servicios (ECS) y de la base de datos (RDS)
- Destinos saludables detrás del balanceador
- Uso del modelo: predicciones por categoría, confianza media, latencia e
  incertidumbre, calculados con Logs Insights sobre los logs JSON que emite el
  servicio. Se evitan así las métricas personalizadas, que tienen costo por
  métrica.

Alarmas: errores 5xx del clasificador, latencia media sobre 1 s y ausencia de
destinos saludables en cualquiera de los dos servicios.

---

## Decisiones de diseño

**ONNX en lugar de PyTorch para servir.** `onnxruntime` pesa unos 100 MB frente
a los ~800 MB del paquete completo de PyTorch. Reduce el tamaño de la imagen,
acelera el arranque en frío y baja el consumo de memoria. El notebook verifica
que las salidas de ONNX coincidan con las de PyTorch antes de dar por bueno el
export: sin esa comprobación, el modelo desplegado podría no ser el evaluado.

**El modelo viaja dentro de la imagen Docker.** Evita una dependencia de red al
arrancar y garantiza que la versión desplegada sea exactamente la que se
evaluó.

**Sin tabla de usuarios propia.** El servicio valida la firma del token y
extrae el identificador del claim `sub`, sin consultar la base de usuarios.
Consecuencia asumida: un usuario eliminado en `inventory-be` conserva acceso
hasta que su token expire (60 minutos). Se acepta a cambio de no acoplar este
servicio al esquema del otro.

**Si S3 falla, la predicción se devuelve igual.** Guardar la imagen es
accesorio; un problema en el bucket no debería impedir clasificar. El registro
queda sin imagen asociada y el fallo se anota en el log.

**Las imágenes se sirven con URL firmadas temporales.** El bucket es privado y
el enlace caduca, de modo que uno filtrado deja de funcionar solo.

---

## Estructura

```
app/
  core/
    config.py           configuración y validaciones de arranque
    database.py         conexión a PostgreSQL
    modelo.py           carga del ONNX y preprocesamiento
    security.py         validación del JWT
    almacenamiento.py   subida a S3 y URL firmadas
  models/               tabla de predicciones
  schemas/              contratos de la API
  routers/              endpoints
  main.py               aplicación FastAPI
modelo/                 clasificador.onnx, metadatos y gráficas
notebooks/              entrenamiento del modelo
scripts/                construcción del dataset
```
