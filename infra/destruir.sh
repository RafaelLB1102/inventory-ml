#!/bin/bash
# ELIMINA TODA la infraestructura del proyecto. Irreversible.
#
# A diferencia de apagar.sh, no conserva nada: ni la base de datos, ni las
# imagenes, ni el snapshot. Tras ejecutarlo el costo es cero.
#
# Se conservan solo los presupuestos de AWS Budgets (gratuitos) y la
# configuracion de IAM Identity Center, para poder seguir usando la cuenta.
#
# Uso: ./infra/destruir.sh
export AWS_PROFILE=inventory
cd "$(dirname "$0")/.."
source infra/aws-resources.env
ACCT=337952093572

echo "Esto eliminara DE FORMA PERMANENTE:"
echo "  - la base de datos inventory-db y su snapshot"
echo "  - las imagenes subidas y el frontend publicado"
echo "  - la distribucion de CloudFront (la URL deja de existir)"
echo "  - los servicios, imagenes Docker, red, secretos y monitoreo"
echo
read -r -p "Escribe ELIMINAR para continuar: " conf
[ "$conf" = "ELIMINAR" ] || { echo "Cancelado."; exit 1; }

paso() { echo; echo "== $1 =="; }
ok()   { echo "   $1"; }

paso "1. Servicios ECS"
for svc in inventory-be inventory-ml; do
  aws ecs update-service --cluster inventory-cluster --service $svc --desired-count 0 >/dev/null 2>&1
  aws ecs delete-service --cluster inventory-cluster --service $svc --force >/dev/null 2>&1 && ok "servicio $svc"
done
aws ecs wait services-inactive --cluster inventory-cluster --services inventory-be inventory-ml 2>/dev/null
aws ecs delete-cluster --cluster inventory-cluster >/dev/null 2>&1 && ok "cluster"
for fam in inventory-be inventory-ml; do
  for td in $(aws ecs list-task-definitions --family-prefix $fam --query 'taskDefinitionArns[]' --output text); do
    aws ecs deregister-task-definition --task-definition "$td" >/dev/null 2>&1
  done
  ok "definiciones de tarea $fam"
done

paso "2. Balanceador"
if [ -n "${ALB_ARN:-}" ]; then
  aws elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN" 2>/dev/null && ok "ALB"
  aws elbv2 wait load-balancers-deleted --load-balancer-arns "$ALB_ARN" 2>/dev/null
fi
sleep 15
for tg in "${TG_ARN:-}" "${TG_ML:-}"; do
  [ -n "$tg" ] && aws elbv2 delete-target-group --target-group-arn "$tg" 2>/dev/null && ok "target group"
done

paso "3. Base de datos (sin snapshot final)"
if aws rds describe-db-instances --db-instance-identifier inventory-db >/dev/null 2>&1; then
  aws rds delete-db-instance --db-instance-identifier inventory-db \
    --skip-final-snapshot --delete-automated-backups >/dev/null
  aws rds wait db-instance-deleted --db-instance-identifier inventory-db && ok "instancia"
fi
for snap in inventory-db-pausa inventory-db-final; do
  aws rds delete-db-snapshot --db-snapshot-identifier $snap >/dev/null 2>&1 && ok "snapshot $snap"
done
aws rds delete-db-subnet-group --db-subnet-group-name "$DB_SUBNET_GROUP" 2>/dev/null && ok "grupo de subredes"

paso "4. CloudFront (primero se deshabilita; tarda ~15 min)"
if aws cloudfront get-distribution --id "$CF_ID" >/dev/null 2>&1; then
  aws cloudfront get-distribution-config --id "$CF_ID" > /tmp/cf-d.json
  python3 -c "
import json; d=json.load(open('/tmp/cf-d.json')); c=d['DistributionConfig']; c['Enabled']=False
json.dump(c,open('/tmp/cf-d-new.json','w')); open('/tmp/cf-d-etag','w').write(d['ETag'])"
  aws cloudfront update-distribution --id "$CF_ID" --distribution-config file:///tmp/cf-d-new.json \
    --if-match "$(cat /tmp/cf-d-etag)" >/dev/null
  aws cloudfront wait distribution-deployed --id "$CF_ID"
  ETAG=$(aws cloudfront get-distribution --id "$CF_ID" --query 'ETag' --output text)
  aws cloudfront delete-distribution --id "$CF_ID" --if-match "$ETAG" && ok "distribucion"
  rm -f /tmp/cf-d.json /tmp/cf-d-new.json /tmp/cf-d-etag
fi
OAC_ETAG=$(aws cloudfront get-origin-access-control --id "$OAC_ID" --query 'ETag' --output text 2>/dev/null)
[ -n "$OAC_ETAG" ] && aws cloudfront delete-origin-access-control --id "$OAC_ID" --if-match "$OAC_ETAG" && ok "origin access control"

paso "5. Buckets S3"
for b in "$BUCKET" "$BUCKET_IMG"; do
  aws s3 rb "s3://$b" --force >/dev/null 2>&1 && ok "bucket $b"
done

paso "6. Registros de imagenes"
for r in inventory-be inventory-ml; do
  aws ecr delete-repository --repository-name $r --force >/dev/null 2>&1 && ok "ECR $r"
done

paso "7. Monitoreo"
aws cloudwatch delete-dashboards --dashboard-names inventory-sistema 2>/dev/null && ok "dashboard"
ALARMAS=$(aws cloudwatch describe-alarms --alarm-name-prefix inventory- --query 'MetricAlarms[].AlarmName' --output text)
[ -n "$ALARMAS" ] && aws cloudwatch delete-alarms --alarm-names $ALARMAS && ok "alarmas"
for lg in $(aws logs describe-log-groups --query 'logGroups[?starts_with(logGroupName,`/ecs/inventory`) || starts_with(logGroupName,`/aws/apprunner/inventory`)].logGroupName' --output text); do
  aws logs delete-log-group --log-group-name "$lg" && ok "logs $lg"
done

paso "8. Secretos"
NOMBRES=$(aws ssm describe-parameters --parameter-filters Key=Name,Option=BeginsWith,Values=/inventory/ \
  --query 'Parameters[].Name' --output text)
[ -n "$NOMBRES" ] && aws ssm delete-parameters --names $NOMBRES >/dev/null && ok "parametros SSM"

paso "9. Roles IAM"
aws iam detach-role-policy --role-name ecsTaskExecutionRoleInventory \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy 2>/dev/null
aws iam delete-role-policy --role-name ecsTaskExecutionRoleInventory --policy-name LecturaParametrosInventario 2>/dev/null
aws iam delete-role --role-name ecsTaskExecutionRoleInventory 2>/dev/null && ok "rol de ejecucion"
aws iam delete-role-policy --role-name inventoryMlTaskRole --policy-name AccesoImagenes 2>/dev/null
aws iam delete-role --role-name inventoryMlTaskRole 2>/dev/null && ok "rol del clasificador"

paso "10. Red"
sleep 30   # las interfaces de red de Fargate tardan en liberarse
# Las reglas que referencian otro grupo impiden borrarlo: se revocan primero
aws ec2 revoke-security-group-ingress --group-id "$SG_RDS" --protocol tcp --port 5432 --source-group "$SG_TASK" 2>/dev/null
aws ec2 revoke-security-group-ingress --group-id "$SG_TASK" --protocol tcp --port 8000 --source-group "$SG_ALB" 2>/dev/null
for sg in "$SG_TASK" "$SG_ALB" "$SG_RDS"; do
  aws ec2 delete-security-group --group-id "$sg" 2>/dev/null && ok "security group $sg"
done
for s in $(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" --query 'Subnets[].SubnetId' --output text); do
  aws ec2 delete-subnet --subnet-id "$s" && ok "subred $s"
done
for rt in "$RTB_PUB" "$RTB_PRI"; do
  aws ec2 delete-route-table --route-table-id "$rt" 2>/dev/null && ok "tabla de ruteo $rt"
done
aws ec2 detach-internet-gateway --internet-gateway-id "$IGW_ID" --vpc-id "$VPC_ID" 2>/dev/null
aws ec2 delete-internet-gateway --internet-gateway-id "$IGW_ID" 2>/dev/null && ok "internet gateway"
aws ec2 delete-vpc --vpc-id "$VPC_ID" && ok "VPC"

paso "Verificacion: recursos restantes con la etiqueta del proyecto"
aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=inventory-actividad2 \
  --query 'ResourceTagMappingList[].ResourceARN' --output text | tr '\t' '\n' | sed 's/^/   quedan: /'
echo
echo "ELIMINADO. Revisa Cost Explorer en 48 horas para confirmar que no quedo nada facturando."
