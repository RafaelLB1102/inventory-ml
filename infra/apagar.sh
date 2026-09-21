#!/bin/bash
# Apaga el sistema completo (inventario y clasificador) para no generar costo.
# Conserva los datos: la base queda en un snapshot, no se pierde.
# Uso: ./infra/apagar.sh
set -e
export AWS_PROFILE=inventory
cd "$(dirname "$0")/.."
source infra/aws-resources.env

echo "== 1. Deteniendo tareas Fargate =="
for svc in inventory-be inventory-ml; do
  aws ecs update-service --cluster inventory-cluster --service "$svc" --desired-count 0 >/dev/null 2>&1 || true
done
aws ecs wait services-stable --cluster inventory-cluster --services inventory-be inventory-ml || true
echo "   0 tareas en ambos servicios"

echo "== 2. Eliminando el balanceador (no se puede pausar, factura por hora) =="
if [ -n "${ALB_ARN:-}" ]; then
  for L in $(aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" --query 'Listeners[].ListenerArn' --output text 2>/dev/null); do
    aws elbv2 delete-listener --listener-arn "$L" 2>/dev/null || true
  done
  aws elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN" 2>/dev/null || true
  sleep 20
  aws elbv2 delete-target-group --target-group-arn "${TG_ARN:-}" 2>/dev/null || true
  sed -i '' '/^ALB_ARN=/d;/^ALB_DNS=/d;/^TG_ARN=/d' infra/aws-resources.env
fi
# El target group del clasificador (TG_ML) se conserva: no tiene costo y el
# servicio ECS sigue apuntando a el, asi que encender.sh solo recrea la regla.
echo "   ALB eliminado"

echo "== 3. Guardando la base de datos en un snapshot y eliminando la instancia =="
# Una instancia detenida se reinicia sola a los 7 dias; un snapshot no.
# encender.sh la restaura desde aqui con el mismo nombre y endpoint.
if aws rds describe-db-instances --db-instance-identifier inventory-db >/dev/null 2>&1; then
  if aws rds describe-db-snapshots --db-snapshot-identifier inventory-db-pausa >/dev/null 2>&1; then
    aws rds delete-db-snapshot --db-snapshot-identifier inventory-db-pausa >/dev/null
    aws rds wait db-snapshot-deleted --db-snapshot-identifier inventory-db-pausa
  fi
  aws rds delete-db-instance --db-instance-identifier inventory-db \
    --final-db-snapshot-identifier inventory-db-pausa >/dev/null
  aws rds wait db-snapshot-available --db-snapshot-identifier inventory-db-pausa
  aws rds wait db-instance-deleted --db-instance-identifier inventory-db
  echo "   snapshot inventory-db-pausa listo, instancia eliminada"
else
  echo "   no hay instancia activa (ya estaba en pausa)"
fi

echo
echo "APAGADO. Coste residual: snapshot, S3, ECR y alarmas (menos de 1 USD al mes)."
