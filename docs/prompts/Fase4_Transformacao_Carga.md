# Fase 4: Transformação e Carga (Transform & Load)

## Resumo da Fase Anterior
A Fase 3 trouxe os dados brutos de rede para o ambiente local/memória. A Fase 4 aplicará as regras do De-Para (Fase 2) sobre esses dados brutos e os gravará definitivamente nas tabelas (Fase 1).

## Objetivo
Higienizar, padronizar tipagens e persistir os dados de forma eficiente no banco de dados PostgreSQL.

## Requisitos Funcionais (RF)
- RF01: O sistema deve tratar anomalias dos dados, como valores nulos, strings vazias e formatação inconsistente de datas.
- RF02: O sistema deve realizar operações de *Upsert* (inserir registros novos ou atualizar existentes com base na chave primária).

## Requisitos Não Funcionais (RNF)
- RNF01: A comunicação com o banco deve ser abstraída através da biblioteca `SQLAlchemy` (integrada ao pandas) ou gerida nativamente via `psycopg2`.
- RNF02: A inserção/atualização deve ocorrer em lotes (batch operations) para não sobrecarregar o consumo de memória RAM nem bloquear a rede.

## Instruções de Implementação (Enxutas)
1. Aplicar dicionário de mapeamento via `df.rename(columns=dict_mapeamento)`.
2. Tratar inconsistências: `df.fillna()`, conversores de datetime e remoção de duplicatas (`drop_duplicates`).
3. Conectar ao PostgreSQL e executar inserção em lotes de ~1000 a 5000 linhas por bloco.

## Diretriz de Otimização (Economia de Tokens em IA)
**Como e Por quê:** Processos de limpeza (pandas) geram códigos densos. Para manter a IA assertiva e não consumir tokens desnecessários, peça funções puras e modulares. Exemplo: *"Crie uma função em pandas que receba uma Série de CPFs, remova pontuação e preencha com zeros à esquerda até 11 dígitos"*. Isso exige quase zero contexto do restante do projeto, resultando em um snippet de código perfeito.
