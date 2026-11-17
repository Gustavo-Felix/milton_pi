# Milton PI — Terraform para AWS Academy Cloud Developing 2.x (Sandbox)

Local no repositório: `backend/terraform/academy/` (a pipeline fica em `backend/pipeline/`).

Ambiente **efêmero**: quando o cronômetro da sessão zera, tudo é excluído. Por isso esta versão é um
"sobe, carrega, usa, descarta" e não tem agendamento nem Lambda.

Cria: RDS PostgreSQL 17 (gp2), EC2 t3.small (Amazon Linux 2023) e o objeto `pipeline.zip` em um bucket S3
preparado pela AWS CLI. A EC2 aplica o `init.sql`, roda a pipeline em Docker (`python:3.14-slim`) e se desliga
sozinha ao terminar com sucesso. O Terraform gerencia 9 recursos; o bucket fica fora do state.

| Restrição da sandbox | Adaptação |
|---|---|
| ECS/Fargate não está na lista de serviços | A pipeline roda em EC2 (t3.small, o maior permitido) |
| Não cria papéis IAM | O EC2 usa o `LabInstanceProfile` (LabRole) |
| RDS só gp2, sem Multi-AZ, sem monitoramento aprimorado | `storage_type = "gp2"`, `monitoring_interval = 0` |
| EBS até 35 GB | volume de 30 GB |
| KMS não está na lista | RDS sem criptografia; DATABASE_URL no user-data (sem SSM SecureString) |
| Só us-east-1 / us-west-2 | variável validada |

## Preparação por sessão

O `LabRole` do Academy nega `s3:GetBucketObjectLockConfiguration`, chamada que o recurso Terraform
`aws_s3_bucket` faz durante a leitura. Para manter essa restrição, crie o bucket com a AWS CLI antes do Terraform.
O nome é `milton-pi-code-<AWS_ACCOUNT_ID>` e a região deve ser a mesma do provider. O Terraform gerencia o
`pipeline.zip`, mas não cria nem apaga o bucket.

## A cada sessão do lab
```bash
# 1. Start Lab -> AWS Details -> Show -> exporte as credenciais
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=... AWS_DEFAULT_REGION=us-east-1

# 2. A sessão anterior apagou o bucket: crie-o de novo antes do Terraform
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="milton-pi-code-${ACCOUNT_ID}"
if [ "$AWS_DEFAULT_REGION" = "us-east-1" ]; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_DEFAULT_REGION"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_DEFAULT_REGION" \
    --create-bucket-configuration "LocationConstraint=$AWS_DEFAULT_REGION"
fi

# 3. A sessão anterior apagou tudo na AWS: descarte o estado local antigo
rm -f terraform.tfstate terraform.tfstate.backup

# 4. Suba (RDS leva ~10 min; depois a carga roda ~2 h na EC2)
cp terraform.tfvars.example terraform.tfvars        # edite my_ip_cidr
cd backend/terraform/academy
terraform init
terraform apply
```
Teste rápido antes da carga completa: `terraform apply -var 'etl_args=2026 11 geografia operadora_ativa'`.

No PowerShell, prepare o bucket assim:

```powershell
$region = if ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } else { "us-east-1" }
$accountId = aws sts get-caller-identity --query Account --output text
$bucket = "milton-pi-code-$accountId"
if ($region -eq "us-east-1") {
  aws s3api create-bucket --bucket $bucket --region $region
} else {
  aws s3api create-bucket --bucket $bucket --region $region --create-bucket-configuration "LocationConstraint=$region"
}
if ($LASTEXITCODE -ne 0) { throw "Falha ao preparar o bucket S3" }
```

Se o bucket já existir, confirme que pertence a esta pipeline antes de reutilizá-lo. Não use um bucket
compartilhado: a limpeza abaixo apaga o conteúdo.

## Acompanhar a carga (SSM, sem SSH)
```bash
ID=$(terraform output -raw ec2_instance_id)
CMD=$(aws ssm send-command --instance-ids $ID --document-name AWS-RunShellScript \
  --parameters 'commands=["tail -n 40 /var/log/milton-etl.log"]' --query Command.CommandId --output text)
sleep 3
aws ssm get-command-invocation --command-id $CMD --instance-id $ID --query StandardOutputContent --output text
```
(O agente SSM leva alguns minutos para registrar após o boot. Alternativa: Console -> EC2 -> Connect -> Session Manager.)

- Sucesso: a instância desliga sozinha (`stop_after_etl`). Falha: ela continua ligada para você investigar.
- **Ligar a instância de novo roda a pipeline de novo** (`aws ec2 start-instances --instance-ids $ID`).
- Rodar à mão: `sudo /opt/milton/run_etl.sh 2026 11 produto_saude`.

## Usar o banco
`terraform output -raw database_url` (use com `?sslmode=require` no DBeaver/psql). O acesso vem do seu IP (`my_ip_cidr`).

## Antes da sessão acabar: salvar os dados (opcional, não testado)
```bash
URL="$(terraform output -raw database_url)?sslmode=require"
pg_dump "$URL" -Fc -f milton.dump                    # baixa para o seu PC
# próxima sessão, depois do terraform apply com run_etl_on_boot=false:
# pg_restore --no-owner -d "$URL_NOVO" milton.dump
```

## Custo
Só se paga enquanto os recursos existem. Estimativa para uma sessão de 4 h: RDS ~US$ 0,07 + EC2 ~US$ 0,08 + discos
e IPv4 ~US$ 0,07 = **cerca de US$ 0,25**. O orçamento de US$ 50 não é o limite deste ambiente; o limite é a persistência.

## Encerrar antes do fim da sessão

Rode `terraform destroy` para remover os 9 recursos gerenciados pelo Terraform, incluindo o objeto S3. Depois,
apague manualmente o bucket preparado pela AWS CLI (somente se ele for exclusivo desta pipeline):

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="milton-pi-code-${ACCOUNT_ID}"
aws s3 rm "s3://${BUCKET}" --recursive
aws s3api delete-bucket --bucket "$BUCKET" --region "$AWS_DEFAULT_REGION"
```
