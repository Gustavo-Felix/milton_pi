# Milton PI — Pipeline de Engenharia de Dados ANS

Pipeline Python para consumir os arquivos do [Portal de Dados Abertos da ANS](https://dadosabertos.ans.gov.br/FTP/PDA/), transformá-los e carregá-los em PostgreSQL.

---

## Arquitetura da Pipeline

```
config.json
    ↓
Downloader (FASE 03/04)    ← baixa 1 arquivo por vez para tempdir; lê CSV direto do ZIP
    ↓ chunks de 1000 linhas (só as colunas usadas)
Transforms (FASE 05/06)    ← sanitização, agregação sintética, De-Para
    ↓
Loader (FASE 07)           ← INSERT ... ON CONFLICT em lotes de 1000 (execute_values)
    ↓
PostgreSQL (FASE 02)       ← schema definido em infra/init.sql
```

### Módulos

| Arquivo | Responsabilidade |
|---|---|
| `src/database.py` | Conexão PostgreSQL, leitura do `.env`, validação de schema |
| `src/downloader.py` | Lista o diretório ANS, seleciona arquivos (regex do `config.json`), baixa em stream e entrega cada CSV (inclusive cada membro do ZIP) em chunks |
| `src/transforms.py` | Transformações por dataset + `Aggregator` (agregação incremental com memória limitada) + `stable_id` (ids determinísticos) |
| `src/loader.py` | Upsert idempotente em lotes, truncamento ao tamanho do VARCHAR, filtro de FKs, criação silenciosa de municípios, séries temporais |
| `src/orchestrator.py` | Orquestrador com CLI; executa os datasets em ordem de dependência dentro de uma transação por competência |
| `src/tests.py` | Testes unitários (pytest), sem rede e sem banco |

---

## Pré-requisitos

- Docker & Docker Compose
- Python 3.14 (a mesma do CI; as versões das bibliotecas são fixas no `requirements.txt` porque os ids vêm do hash do pandas)
- `.env` nesta pasta, copiado de `.env.example`

Todos os comandos abaixo rodam em `backend/pipeline`. As regras do cliente citadas (`instrucoes.md`, prompts das fases) ficam em `docs/` na raiz do repositório.

---

## Execução com Docker

```bash
# Subir banco com schema inicializado
docker-compose up db -d

# Aguardar healthcheck (banco pronto)
docker-compose ps

# Executar pipeline (competência atual)
docker-compose run --rm app sh -c "pip install -r requirements.txt -q && python -m src.orchestrator"

# Ou especificar competência e datasets
docker-compose run --rm app sh -c "pip install -r requirements.txt -q && python -m src.orchestrator 2024 3 operadora_ativa produto_saude"
```

---

## Execução local

```bash
# Instalar dependências
python -m venv .venv
.venv\Scripts\activate     # Windows
pip install -r requirements.txt

# Executar testes (sem banco)
python -m pytest src/tests.py -v

# Executar pipeline (porta definida no .env; o Compose expõe 55432)
python -m src.orchestrator
python -m src.orchestrator 2024 3
python -m src.orchestrator 2024 3 operadora_ativa produto_saude
python -m src.orchestrator 2024 3 --transacao-por-dataset   # commit por dataset (depuração)
python -m src.orchestrator 2024 3 -v                         # log DEBUG
```

---

## Regras de Negócio Implementadas

### Chunks de 1000 registros
Todos os CSVs são lidos em chunks de 1000 registros (sobrescrevível via `PIPELINE_CHUNK_SIZE`) e gravados em lotes de 1000 (`PIPELINE_BATCH_SIZE`; o CI usa 10 mil, porque o RDS fica do outro lado da internet). Os ZIPs são lidos em streaming direto do arquivo compactado — sem extração para disco — e **todos** os CSVs do ZIP são processados (o de prestadores tem um CSV por UF). Cada arquivo é apagado antes do download do próximo.

### Transação por competência
Por padrão a competência inteira roda em um único `BEGIN ... COMMIT`; qualquer falha (download, ZIP, carga) faz `ROLLBACK` de tudo e o status é `ERROR`. Com `--transacao-por-dataset` cada dataset tem sua própria transação e o status pode ser `PARTIAL`.

### Séries Temporais
Cada arquivo baixado registra em `serie_temporal_mensal` o mês do próprio dado (instrucoes.md, regra 1; ver "Períodos"). Não se cria série para a competência da linha de comando se não houver dado dela.

### Sanitização de Faixas Etárias
Os códigos `CD_FAIXA_ETARIA` 1..10 do PDA 054 são mapeados para as faixas padrão da RN 63/2003 (`0-18` … `59+`), e uma Regex quebra a faixa em `faixa_etaria_inicial` e `faixa_etaria_final` (instrucoes.md, regra 3; `59+` fica sem fim).

### Preço por faixa etária
O VCM do PDA 054 é publicado por município; como `preco_faixa_etaria` não tem município, grava-se a média do VCM por `(plano, faixa)` da competência. É usado o `YYYYMM.zip` mais recente que não ultrapassa a competência.

### Rede Credenciada Sintética
O arquivo de prestadores hospitalares é agregado por `(id_plano, cd_municipio, id_serie_mensal)`, no formato do DER: uma linha com `qtd_hospitais`, `qtd_pronto_atendimento`, `qtd_laboratorios` e `qtd_consultorios` (SELECT do instrucoes.md). Nunca salva os registros brutos. Cada UF é agregada inteira antes da gravação; só o snapshot do mesmo mês é substituído, e os meses anteriores ficam como histórico.

### Market share
Contagem de beneficiários ativos (`sib_ativo_[UF].zip`) por `(operadora, município)`; `percentual_market_share` = beneficiários da operadora ÷ total do município (0..1).

### Taxa de cobertura
O PDA 047 é por município/ano, sem operadora, então vai para `perfil_saude_publica.taxa_cobertura_plano_saude` (= Σ beneficiários ÷ Σ população) e `populacao_estimada`.

### Idempotência
As tabelas fato só têm `id` como PK, então o `id` é derivado (hash determinístico) da chave natural e as cargas usam `INSERT ... ON CONFLICT`. Snapshots (`preco_faixa_etaria`, `market_share` e `rede_credenciada_sintetica` por mês; `abrangencia_plano` inteira, porque o arquivo é a área de comercialização ativa completa) são apagados e regravados na mesma transação. O upsert só reescreve a linha quando algum valor mudou, então `updated_at` marca mudança de verdade. Executar novamente produz o mesmo estado final.

### Integridade referencial
Linhas que referenciam plano/operadora inexistente são descartadas (contagem no log em `-v`). Operadoras canceladas citadas no PDA 008 são criadas com os dados do próprio arquivo. Municípios ausentes são criados apenas com o código IBGE (instrucoes.md, regra 4); o dataset `geografia` (PDA 054) preenche nome e UF.

---

## Variáveis de Ambiente (`.env`)

Modelo em `.env.example` (copiar para `.env`, que fica fora do git). `DATABASE_URL`, se definida, tem prioridade sobre `POSTGRES_*`. Para hosts remotos a pipeline liga `sslmode=require` e desliga `statement_timeout` e `idle_in_transaction_session_timeout` na sessão, porque a carga baixa arquivos dentro da transação.

---

## AWS (RDS PostgreSQL)

1. **Custo:** `db.t4g.micro` + 20 GB gp3 (o banco tem ~4 GB). Contas novas ganham até US$ 200 de crédito por 6 meses no plano Free; depois disso, ~US$ 14/mês em us-east-1 ou ~US$ 30/mês em sa-east-1 (estimativa; confirmar no AWS Pricing Calculator).
2. **Criar o banco** (precisa da VPC padrão da conta; `AllowedCidr` = seu IP, em checkip.amazonaws.com):
   ```bash
   aws cloudformation deploy --stack-name milton-pi --template-file infra/aws/rds.yml      --capabilities CAPABILITY_IAM      --parameter-overrides DBPassword=<16+ letras e números> AllowedCidr=<seu-ip>/32
   aws cloudformation describe-stacks --stack-name milton-pi --query "Stacks[0].Outputs"
   ```
   Se a conta já tiver o provedor OIDC do GitHub, acrescente `CreateGitHubOidcProvider=false`. O banco é Postgres 17, com SSL obrigatório, criptografado, backup de 7 dias, proteção contra exclusão e snapshot se a stack for apagada.
3. **Conexão:** a saída `DatabaseUrl`, com a senha, vai para o `.env` como `DATABASE_URL` (modelo em `.env.example`).
4. **Schema e dados:** `bash infra/db_setup.sh` aplica `infra/init.sql` (idempotente). Para levar o banco local já carregado: `bash infra/db_setup.sh --copiar` (só para destino vazio; uma transação; ~1 h para 3,9 GB numa base local, mais pela internet). Outra opção é rodar a pipeline direto no RDS: `python -m src.orchestrator 2026 10` (1h30–2h).
5. **Agendamento (RNF03):** `.github/workflows/etl.yml` roda todo dia à meia-noite de Brasília. No GitHub (*Settings → Secrets and variables → Actions*): secret `DATABASE_URL`; variáveis `AWS_ROLE_ARN` (saída `CiRoleArn`), `RDS_SECURITY_GROUP_ID` (saída `SecurityGroupId`) e `AWS_REGION`. O job libera o IP do runner no security group só durante a carga e revoga no fim. Sem o secret, só roda os testes. O GitHub só agenda workflows do branch padrão, então o agendamento vale a partir de quando o `etl.yml` estiver nele.

---

## Datasets Suportados

| dataset_id | Fonte ANS | Tabela PostgreSQL |
|---|---|---|
| `geografia` | `valor_comercial_medio_por_municipio_NTRP-054/pda-054-geografia.csv` | `estado`, `municipio` |
| `ibge_localidades` | API IBGE `localidades/estados` e `localidades/municipios` + SIDRA 6579 | `estado`, `municipio` (código IBGE de 7 dígitos, nome, população) |
| `operadora_cancelada` | `operadoras_de_plano_de_saude_canceladas/` | `operadora` (situação CANCELADA) |
| `operadora_ativa` | `operadoras_de_plano_de_saude_ativas/` | `operadora` |
| `produto_saude` | `caracteristicas_produtos_saude_suplementar-008/` | `produto_saude` (+ `operadora`) |
| `abrangencia_plano` | `area_comercializacao_planos_ntrp/` | `abrangencia_plano` |
| `valor_comercial_medio_municipio` | `valor_comercial_medio_por_municipio_NTRP-054/YYYYMM.zip` | `preco_faixa_etaria` |
| `rede_credenciada_hospitalar` | `produtos_e_prestadores_hospitalares/` | `rede_credenciada_sintetica` |
| `beneficiarios_operadora` | `dados_de_beneficiarios_por_operadora/sib_ativo_*.zip` | `market_share` |
| `painel_precificacao` | `painel_precificacao-031/` | `produto_saude.nota_sinistralidade_media` (média de PCT_DESP_ASS) |
| `taxa_cobertura` | `taxa_de_cobertura_de_planos_de_saude-047/` + população SIDRA do mesmo ano | `perfil_saude_publica` |

`operadora_acreditada`, `preco_faixa_etaria` (`rel_faixa_de_preco.csv` não tem `ID_PLANO`), `beneficiarios_vinculo_contratacao` e `servicos_opcionais` ficam como `SKIPPED`, com o motivo no campo `motivo` do config.json (os três primeiros não têm tabela no DER). Nada é baixado para eles.

---

## Logs

Os logs vão para `logs/pipeline.log` (rotativo, 5 MB × 3) e para o stdout.

## Períodos

Cada fato é gravado na série temporal do **próprio dado**, não na competência passada na linha de comando: o market share usa o `ID_TEMPO_COMPETENCIA` do SIB, o preço usa o `YYYYMM` do arquivo do PDA 054 e a rede credenciada usa o mês do `DT_ATUALIZACAO` (data do snapshot; o arquivo não tem competência).

A população vem do SIDRA 6579 com a série histórica inteira (2001–2026, sem 2007, 2010, 2022 e 2023, que o IBGE não estima nessa tabela). Cada ano fica em `perfil_saude_publica.populacao_estimada` por `(id_municipio, id_serie_anual)`, e `municipio.populacao_estimada` guarda o último ano, indicado em `municipio.id_serie_anual`. O SIDRA responde HTTP 500 a partir de 9 períodos por chamada, por isso os pedidos vão em lotes de 8.

## Migrações

`infra/migrations/001_conformidade_der.sql` adiciona as colunas do DER v3 do cliente (faixa etária inicial/final, tipo de abrangência, população e renda do município, sinistralidade do plano). Ela é idempotente e já está incluída no `infra/init.sql` para instalações novas. Em um banco existente:

```bash
docker exec -i milton_postgres psql -U postgres -d milton_pi < infra/migrations/001_conformidade_der.sql
```

`infra/migrations/002_der_produto_rede_populacao.sql` traz a segunda leva: `produto_saude.abrangencia_geografica` (← `ABRANGENCIA_COBERTURA` do PDA 008), as colunas do DER na rede credenciada (`id_serie_mensal` e as quatro `qtd_*`) e `municipio.id_serie_anual`. ```bash
docker exec -i milton_postgres psql -U postgres -d milton_pi < infra/migrations/002_der_produto_rede_populacao.sql
```

Ela também apaga as linhas da rede no formato antigo e remove as colunas `tipo_servico`, `especialidade` e `quantidade_estimada`, que não estão no DER (rodar a carga da rede logo depois). Instalações novas não precisam de migrações: o `infra/init.sql` já traz o schema final.
