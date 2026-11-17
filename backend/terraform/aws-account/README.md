# Milton PI — Terraform para conta AWS comum (RDS + pipeline em ECS Fargate)

Local no repositório: `backend/terraform/aws-account/`. NÃO funciona em sandbox AWS Academy (precisa criar papéis IAM e usar ECS).

Cria: RDS PostgreSQL 17 (db.t4g.micro), ECR, cluster ECS, tarefa Fargate da pipeline,
agendamento (EventBridge Scheduler), DATABASE_URL no SSM, logs e alerta de orçamento.

## 1. Subir a infraestrutura
```bash
cp terraform.tfvars.example terraform.tfvars   # edite my_ip_cidr e alert_email
terraform init
terraform plan
terraform apply
```

## 2. Colocar os arquivos de container no repositório
```bash
# (a partir de backend/terraform/aws-account)
cp pipeline-docker/Dockerfile pipeline-docker/.dockerignore pipeline-docker/entrypoint.sh ../../pipeline/
cp pipeline-docker/apply_schema.py ../../pipeline/infra/
```

## 3. Build e push da imagem
```bash
ECR=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${ECR%%/*}"
docker build --platform linux/amd64 -t "$ECR:latest" ../../pipeline
docker push "$ECR:latest"
```
(Se usar `task_architecture = "ARM64"`, troque para `--platform linux/arm64`.)

## 4. Rodar a pipeline manualmente (primeiro teste)
```bash
SUBNET=$(terraform output -json pipeline_subnet_ids | jq -r '.[0]')
SG=$(terraform output -raw pipeline_security_group_id)

aws ecs run-task --cluster milton-pi --task-definition milton-pi-pipeline \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=ENABLED}"

# acompanhar
aws logs tail /ecs/milton-pi-pipeline --follow
```
Rodar só alguns datasets (teste barato, minutos em vez de horas):
```bash
aws ecs run-task ... --overrides '{"containerOverrides":[{"name":"pipeline","command":["2026","11","geografia","operadora_ativa"]}]}'
```

## 5. Conectar no banco de casa
```bash
terraform output -raw database_url     # contém a senha; use em DBeaver/psql com sslmode=require
```

## 6. Desligar tudo
```bash
terraform destroy
```

## 7. Liga/desliga do RDS (lambda.tf)
O RDS só fica ligado na janela da carga: o Scheduler liga a Lambda `start` 30 min antes do ETL e,
quando a tarefa ECS termina, o EventBridge chama a Lambda `stop_if_idle`.

```bash
# Antes de um teste manual (RDS está parado):
aws rds start-db-instance --db-instance-identifier milton-pi
aws rds wait db-instance-available --db-instance-identifier milton-pi

# Trabalhar com o banco sem que a Lambda desligue (DBeaver, desenvolvimento da API):
aws rds add-tags-to-resource --resource-name $(aws rds describe-db-instances --db-instance-identifier milton-pi \
  --query 'DBInstances[0].DBInstanceArn' --output text) --tags Key=keep-awake,Value=true
# ...e para voltar ao modo econômico:
aws rds remove-tags-from-resource --resource-name <arn> --tag-keys keep-awake

# Ver o que a Lambda decidiu:
aws logs tail /aws/lambda/milton-pi-rds-power --since 1d
```
Para carga diária: `schedule_expression = "cron(0 3 * * ? *)"` e `db_start_expression = "cron(30 2 * * ? *)"`.
Com o RDS parado, `terraform apply` não consegue alterar o banco: ligue-o antes.
