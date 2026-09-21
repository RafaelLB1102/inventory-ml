#!/bin/bash
# Crea o actualiza el dashboard de CloudWatch y las alarmas del sistema.
#
# Se ejecuta en cada encendido porque el ALB se recrea con un identificador
# nuevo: un dashboard fijo quedaria apuntando a un balanceador inexistente y
# mostraria graficas vacias sin dar error.
set -e
export AWS_PROFILE=${AWS_PROFILE:-inventory}
cd "$(dirname "$0")/.."
source infra/aws-resources.env

# Las metricas del ALB usan el sufijo del ARN, no el ARN completo
LB_DIM=$(echo "$ALB_ARN" | sed 's|.*:loadbalancer/||')
TG_BE_DIM=$(echo "$TG_ARN" | sed 's|.*:||')
TG_ML_DIM=$(echo "$TG_ML" | sed 's|.*:||')

python3 - "$LB_DIM" "$TG_BE_DIM" "$TG_ML_DIM" > /tmp/dashboard.json <<'PY'
import json, sys
lb, tg_be, tg_ml = sys.argv[1:4]
R = "us-east-1"

def alb(metrica, tg, etiqueta, stat="Sum", color=None):
    extra = {"label": etiqueta, "stat": stat}
    if color: extra["color"] = color
    return ["AWS/ApplicationELB", metrica, "TargetGroup", tg, "LoadBalancer", lb, extra]

def texto(md, x, y, w=24, h=2):
    return {"type": "text", "x": x, "y": y, "width": w, "height": h, "properties": {"markdown": md}}

def grafica(titulo, metricas, x, y, w=8, h=6, periodo=60, extra=None):
    p = {"title": titulo, "region": R, "metrics": metricas, "period": periodo,
         "view": "timeSeries", "stacked": False}
    if extra: p.update(extra)
    return {"type": "metric", "x": x, "y": y, "width": w, "height": h, "properties": p}

def consulta(titulo, q, x, y, w=12, h=6, vista="table"):
    return {"type": "log", "x": x, "y": y, "width": w, "height": h,
            "properties": {"title": titulo, "region": R, "view": vista,
                           "query": "SOURCE '/ecs/inventory-ml' | " + q}}

w = []
w.append(texto("# Sistema de inventario con clasificación de imágenes\n"
               "Tráfico, latencia y errores del balanceador · recursos de ECS y RDS · uso del modelo desde los logs estructurados", 0, 0))

# --- Fila 1: tráfico y salud
w.append(grafica("Peticiones por servicio", [
    alb("RequestCount", tg_be, "inventory-be", color="#1f77b4"),
    alb("RequestCount", tg_ml, "inventory-ml", color="#ff7f0e")], 0, 2))
w.append(grafica("Latencia p95 del destino (s)", [
    alb("TargetResponseTime", tg_be, "inventory-be p95", stat="p95", color="#1f77b4"),
    alb("TargetResponseTime", tg_ml, "inventory-ml p95", stat="p95", color="#ff7f0e")], 8, 2))
w.append(grafica("Errores HTTP", [
    alb("HTTPCode_Target_5XX_Count", tg_be, "be 5xx", color="#d62728"),
    alb("HTTPCode_Target_5XX_Count", tg_ml, "ml 5xx", color="#9467bd"),
    alb("HTTPCode_Target_4XX_Count", tg_ml, "ml 4xx (validaciones)", color="#bcbd22")], 16, 2))

# --- Fila 2: capacidad
w.append(grafica("CPU de los servicios (%)", [
    ["AWS/ECS", "CPUUtilization", "ClusterName", "inventory-cluster", "ServiceName", "inventory-be", {"label": "inventory-be"}],
    ["AWS/ECS", "CPUUtilization", "ClusterName", "inventory-cluster", "ServiceName", "inventory-ml", {"label": "inventory-ml"}]],
    0, 8, periodo=60, extra={"yAxis": {"left": {"min": 0, "max": 100}}}))
w.append(grafica("Memoria de los servicios (%)", [
    ["AWS/ECS", "MemoryUtilization", "ClusterName", "inventory-cluster", "ServiceName", "inventory-be", {"label": "inventory-be"}],
    ["AWS/ECS", "MemoryUtilization", "ClusterName", "inventory-cluster", "ServiceName", "inventory-ml", {"label": "inventory-ml"}]],
    8, 8, periodo=60, extra={"yAxis": {"left": {"min": 0, "max": 100}}}))
w.append(grafica("Base de datos", [
    ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "inventory-db", {"label": "CPU %"}],
    ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", "inventory-db", {"label": "conexiones", "yAxis": "right"}]],
    16, 8, periodo=60))

# --- Fila 3: salud y alarmas
w.append(grafica("Destinos saludables", [
    alb("HealthyHostCount", tg_be, "inventory-be", stat="Minimum", color="#2ca02c"),
    alb("HealthyHostCount", tg_ml, "inventory-ml", stat="Minimum", color="#17becf")],
    0, 14, periodo=60, extra={"yAxis": {"left": {"min": 0}}}))
w.append({"type": "alarm", "x": 8, "y": 14, "width": 16, "height": 6,
          "properties": {"title": "Estado de las alarmas", "alarms": [
              f"arn:aws:cloudwatch:{R}:337952093572:alarm:inventory-ml-errores-5xx",
              f"arn:aws:cloudwatch:{R}:337952093572:alarm:inventory-ml-latencia-alta",
              f"arn:aws:cloudwatch:{R}:337952093572:alarm:inventory-ml-sin-destinos",
              f"arn:aws:cloudwatch:{R}:337952093572:alarm:inventory-be-sin-destinos"]}})

# --- Fila 4: uso del modelo (desde logs estructurados, sin métricas de pago)
w.append(texto("## Uso del modelo\nCalculado desde los logs JSON que emite el servicio en cada predicción", 0, 20))
base = "filter @message like /\"evento\": \"prediccion\"/ | parse @message '\"clase\": \"*\"' as clase | parse @message '\"confianza\": *,' as confianza | parse @message '\"incierta\": *,' as incierta | parse @message '\"latencia_ms\": *,' as latencia"
w.append(consulta("Predicciones por categoría",
    base + " | stats count(*) as predicciones, avg(confianza) as confianza_media by clase | sort predicciones desc", 0, 22))
w.append(consulta("Resumen de inferencia",
    base + " | stats count(*) as total, avg(latencia) as latencia_media_ms, pct(latencia, 95) as latencia_p95_ms, sum(incierta = 'true') as inciertas", 12, 22))
w.append(consulta("Predicciones en el tiempo",
    base + " | stats count(*) as predicciones by bin(5m)", 0, 28, w=24, vista="timeSeries"))

print(json.dumps({"widgets": w}))
PY

aws cloudwatch put-dashboard --dashboard-name inventory-sistema \
  --dashboard-body file:///tmp/dashboard.json --query 'DashboardValidationMessages' --output text
rm -f /tmp/dashboard.json

# --- Alarmas ---------------------------------------------------------------
alarma() {  # nombre metrica tg estadistica umbral comparacion descripcion
  aws cloudwatch put-metric-alarm --alarm-name "$1" --namespace AWS/ApplicationELB \
    --metric-name "$2" --dimensions Name=TargetGroup,Value="$3" Name=LoadBalancer,Value="$LB_DIM" \
    --statistic "$4" --period 60 --evaluation-periods 3 --datapoints-to-alarm 2 \
    --threshold "$5" --comparison-operator "$6" --treat-missing-data notBreaching \
    --alarm-description "$7" --tags Key=Project,Value=inventory-actividad2 2>/dev/null || \
  aws cloudwatch put-metric-alarm --alarm-name "$1" --namespace AWS/ApplicationELB \
    --metric-name "$2" --dimensions Name=TargetGroup,Value="$3" Name=LoadBalancer,Value="$LB_DIM" \
    --statistic "$4" --period 60 --evaluation-periods 3 --datapoints-to-alarm 2 \
    --threshold "$5" --comparison-operator "$6" --treat-missing-data notBreaching \
    --alarm-description "$7"
}
alarma inventory-ml-errores-5xx   HTTPCode_Target_5XX_Count "$TG_ML_DIM" Sum 5 GreaterThanThreshold \
  "El clasificador devuelve errores internos: revisar /ecs/inventory-ml"
alarma inventory-ml-latencia-alta TargetResponseTime        "$TG_ML_DIM" Average 1 GreaterThanThreshold \
  "Tiempo de respuesta del clasificador sobre 1 s"

# Sin destinos saludables: aqui el dato ausente SI es un problema
for srv in ml be; do
  TG=$([ $srv = ml ] && echo "$TG_ML_DIM" || echo "$TG_BE_DIM")
  aws cloudwatch put-metric-alarm --alarm-name "inventory-$srv-sin-destinos" --namespace AWS/ApplicationELB \
    --metric-name HealthyHostCount --dimensions Name=TargetGroup,Value="$TG" Name=LoadBalancer,Value="$LB_DIM" \
    --statistic Minimum --period 60 --evaluation-periods 2 --threshold 1 \
    --comparison-operator LessThanThreshold --treat-missing-data breaching \
    --alarm-description "inventory-$srv no tiene tareas saludables detras del balanceador"
done

echo "Dashboard: https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards/dashboard/inventory-sistema"
