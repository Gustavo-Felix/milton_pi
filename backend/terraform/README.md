# Infraestrutura como código (Terraform)

| Pasta | Quando usar |
|---|---|
| `academy/` | **AWS Academy Cloud Developing 2.x - Sandbox** (ambiente efêmero, sem ECS, sem criar papéis IAM). RDS + EC2 que roda a pipeline uma vez e se desliga. |
| `aws-account/` | **Conta AWS comum** (Free Plan/paga). RDS + ECS Fargate agendado + Lambda que liga/desliga o RDS + alerta de orçamento. |

Cada pasta tem seu próprio README com o passo a passo. Rode o Terraform de dentro da pasta escolhida.
Não commite `terraform.tfstate*` nem `terraform.tfvars` (os `.gitignore` das pastas já cobrem isso).
