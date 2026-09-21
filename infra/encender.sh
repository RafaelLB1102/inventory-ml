#!/bin/bash
# Levanta el sistema completo en AWS: base de datos, balanceador, los dos
# servicios (inventario y clasificador), CloudFront y el panel de monitoreo.
# Uso: ./infra/encender.sh
set -e
export AWS_PROFILE=inventory
cd "$(dirname "$0")/.."
source infra/aws-resources.env

echo "== 1. Preparando la base de datos =="
if aws rds describe-db-instances --db-instance-identifier inventory-db >/dev/null 2>&1; then
  EST=$(aws rds describe-db-instances --db-instance-identifier inventory-db --query 'DBInstances[0].DBInstanceStatus' --output text)
  if [ "$EST" = "stopped" ]; then
    aws rds start-db-instance --db-instance-identifier inventory-db >/dev/null
  fi
else
  # Pausa larga: la instancia se elimino dejando el snapshot inventory-db-pausa.
  # Se restaura con el mismo identificador, asi el endpoint no cambia y las
  # definiciones de tarea siguen siendo validas.
  echo "   restaurando desde el snapshot inventory-db-pausa (tarda ~10-15 min)"
  aws rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier inventory-db \
    --db-snapshot-identifier inventory-db-pausa \
    --db-instance-class db.t4g.micro \
    --db-subnet-group-name "$DB_SUBNET_GROUP" \
    --vpc-security-group-ids "$SG_RDS" \
    --no-publicly-accessible --no-multi-az --storage-type gp3 \
    --tags Key=Project,Value=inventory-actividad2 >/dev/null
fi
aws rds wait db-instance-available --db-instance-identifier inventory-db
echo "   RDS disponible"

echo "== 2. Recreando el balanceador =="
ALB_ARN=$(aws elbv2 create-load-balancer --name inventory-alb \
  --subnets "$SUBNET_PUB_A" "$SUBNET_PUB_B" --security-groups "$SG_ALB" \
  --scheme internet-facing --type application --ip-address-type ipv4 \
  --tags Key=Project,Value=inventory-actividad2 \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)
ALB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns "$ALB_ARN" --query 'LoadBalancers[0].DNSName' --output text)
TG_ARN=$(aws elbv2 create-target-group --name inventory-tg \
  --protocol HTTP --port 8000 --vpc-id "$VPC_ID" --target-type ip \
  --health-check-protocol HTTP --health-check-path /health \
  --health-check-interval-seconds 30 --health-check-timeout-seconds 10 \
  --healthy-threshold-count 2 --unhealthy-threshold-count 3 \
  --query 'TargetGroups[0].TargetGroupArn' --output text)
LISTENER=$(aws elbv2 create-listener --load-balancer-arn "$ALB_ARN" --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn="$TG_ARN" \
  --query 'Listeners[0].ListenerArn' --output text)
# El target group del clasificador sobrevive entre encendidos; solo hay que
# volver a enrutarle /ml/* en el balanceador nuevo.
aws elbv2 create-rule --listener-arn "$LISTENER" --priority 10 \
  --conditions Field=path-pattern,Values='/ml/*' \
  --actions Type=forward,TargetGroupArn="$TG_ML" >/dev/null
sed -i '' '/^ALB_ARN=/d;/^ALB_DNS=/d;/^TG_ARN=/d' infra/aws-resources.env
printf 'ALB_ARN=%s\nALB_DNS=%s\nTG_ARN=%s\n' "$ALB_ARN" "$ALB_DNS" "$TG_ARN" >> infra/aws-resources.env
echo "   ALB: $ALB_DNS  (/ml/* -> inventory-ml)"

echo "== 3. Levantando los servicios =="
aws ecs update-service --cluster inventory-cluster --service inventory-be \
  --desired-count 1 \
  --load-balancers "targetGroupArn=$TG_ARN,containerName=backend,containerPort=8000" \
  --health-check-grace-period-seconds 120 >/dev/null
aws ecs update-service --cluster inventory-cluster --service inventory-ml \
  --desired-count 1 >/dev/null

# Espera a que el destino quede saludable. Si Fargate no consigue capacidad
# en la zona ("Capacity is unavailable"), reintenta el despliegue en lugar de
# abortar: ECS repartira la tarea entre las dos subredes.
esperar() {  # servicio target_group
  local reintentado=0
  for i in $(seq 1 45); do
    local estado=$(aws elbv2 describe-target-health --target-group-arn "$2" \
      --query 'TargetHealthDescriptions[?TargetHealth.State==`healthy`] | length(@)' --output text 2>/dev/null)
    if [ "${estado:-0}" -ge 1 ]; then echo "   $1 saludable"; return 0; fi
    local corriendo=$(aws ecs describe-services --cluster inventory-cluster --services "$1" \
      --query 'services[0].runningCount' --output text)
    if [ "$i" -eq 18 ] && [ "$corriendo" = "0" ] && [ "$reintentado" = "0" ]; then
      echo "   $1 sin tareas tras 6 min, reintentando el despliegue"
      aws ecs update-service --cluster inventory-cluster --service "$1" --force-new-deployment >/dev/null
      reintentado=1
    fi
    sleep 20
  done
  echo "   AVISO: $1 no quedo saludable en 15 min; revisar eventos del servicio"
  return 1
}
esperar inventory-be "$TG_ARN" || true
esperar inventory-ml "$TG_ML" || true

echo "== 4. Actualizando el origen de CloudFront =="
# El ALB se recrea con un DNS distinto cada vez. Sin este paso el dominio
# publico responde 502 porque apunta al balanceador eliminado.
aws cloudfront get-distribution-config --id "$CF_ID" > /tmp/cf-enc.json
python3 - "$ALB_DNS" <<'PYEOF'
import json, sys
alb = sys.argv[1]
d = json.load(open('/tmp/cf-enc.json'))
cfg, etag = d['DistributionConfig'], d['ETag']
for o in cfg['Origins']['Items']:
    if o['Id'] == 'alb-inventory-be':
        o['DomainName'] = alb
json.dump(cfg, open('/tmp/cf-enc-new.json', 'w'))
open('/tmp/cf-enc-etag.txt', 'w').write(etag)
PYEOF
aws cloudfront update-distribution --id "$CF_ID" \
  --distribution-config file:///tmp/cf-enc-new.json \
  --if-match "$(cat /tmp/cf-enc-etag.txt)" >/dev/null
rm -f /tmp/cf-enc.json /tmp/cf-enc-new.json /tmp/cf-enc-etag.txt
aws cloudfront wait distribution-deployed --id "$CF_ID"
echo "   origen -> $ALB_DNS (propagado)"

echo "== 5. Panel de monitoreo =="
./infra/dashboard.sh >/dev/null && echo "   dashboard y alarmas apuntando al ALB nuevo"

echo "== 6. Verificacion =="
for ruta in /api/health /ml/openapi.json; do
  for i in $(seq 1 12); do
    COD=$(curl -s -o /dev/null -w "%{http_code}" "https://$CF_DOMAIN$ruta" || true)
    [ "$COD" = "200" ] && break
    sleep 10
  done
  echo "   $ruta -> $COD"
done
echo
echo "LISTO."
echo "  Aplicacion: https://$CF_DOMAIN"
echo "  API:        https://$CF_DOMAIN/api/docs"
echo "  Clasificador: https://$CF_DOMAIN/ml/docs"
echo "  Monitoreo:  https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards/dashboard/inventory-sistema"
