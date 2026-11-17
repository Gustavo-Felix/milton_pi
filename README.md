# Milton PI

Sistema de recomendação de planos de saúde a partir dos dados abertos da ANS e do IBGE.

| Pasta | Conteúdo |
|---|---|
| [`backend/`](backend/) | [`pipeline/`](backend/pipeline/): ETL ANS/IBGE → PostgreSQL (local no Docker ou AWS RDS). A API do app entra aqui também. |
| [`frontend/`](frontend/) | Interface do app (a criar). |
| [`docs/`](docs/) | Documentação do cliente: DER v3, `instrucoes.md`, fontes, prompts das fases. |

Para rodar a pipeline e subir o banco na AWS: [`backend/pipeline/README.md`](backend/pipeline/README.md).
