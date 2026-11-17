# Fase 2: Configuração de Mapeamento (De-Para)

## Resumo da Fase Anterior
A Fase 1 estabeleceu a estrutura física (tabelas e colunas) e o dimensionamento do banco PostgreSQL. A Fase 2 utilizará essa estrutura como o "destino" dos dados.

## Objetivo
Criar as regras de tradução padronizadas (De-Para) entre os atributos extraídos das APIs e as tabelas estruturadas na Fase 1.

## Requisitos Funcionais (RF)
- RF01: O sistema deve mapear os campos das fontes (JSON/CSV) para as colunas do banco de dados conforme as regras do documento `Detalhamento da correlação a nível de campo, por Origem-1.pdf`.
- RF02: O sistema deve centralizar a configuração de endpoints, credenciais públicas e identificadores de fontes em um arquivo estático (ex: `config_fontes.json`).

## Requisitos Não Funcionais (RNF)
- RNF01: O mapeamento deve ser implementado através de dicionários de dados nativos em Python.
- RNF02: O código de configuração deve ser estritamente desacoplado da lógica de execução da extração.

## Instruções de Implementação (Enxutas)
1. Estruturar o `config_fontes.json` com URLs base e formatos esperados.
2. Codificar dicionários Python (ex: `dict_ans_to_db` e `dict_ms_to_db`) para correlacionar e renomear as chaves de origem para as colunas destino.

## Diretriz de Otimização (Economia de Tokens em IA)
**Como e Por quê:** Manter os mapeamentos em estruturas de dados simples (dicionários) permite que a IA compreenda a arquitetura rapidamente. Caso precise de ajuda para gerar scripts de conversão, forneça apenas um "mock" minúsculo (1 objeto JSON) e as chaves do dicionário alvo. Evite despejar arquivos PDF ou CSV inteiros no prompt.
