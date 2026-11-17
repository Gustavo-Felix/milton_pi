# Fase 3: Extração de Dados (Extract)

## Resumo da Fase Anterior
A Fase 2 definiu *o que* deve ser lido e para *onde* deve ser traduzido. A Fase 3 executará a comunicação de rede para buscar esses dados nos endereços parametrizados, com base no `fontes_PI_final.pdf`.

## Objetivo
Consumir as fontes de dados reais (API REST e Portal de Dados/FTP) de maneira otimizada e trazer os dados brutos para a memória do script.

## Requisitos Funcionais (RF)
- RF01: O pipeline deve realizar requisições HTTP para a API de Dados Abertos do Ministério da Saúde.
- RF02: O pipeline deve fazer o download e, se necessário, descompactar os arquivos do PDA da ANS.

## Requisitos Não Funcionais (RNF)
- RNF01: Deve-se utilizar a biblioteca `requests` para consumo de API e `pandas` para leitura/alocação de arquivos textuais (CSV) em memória.
- RNF02: O sistema deve implementar resiliência de rede: tratamento de paginação (offset/limit) e limites de requisição (Rate Limiting / Timeout).

## Instruções de Implementação (Enxutas)
1. Criar módulo MS: função com loop de requisições `requests.get`, iterando sobre as páginas até resposta vazia.
2. Criar módulo ANS: função para download de stream (arquivos grandes) e leitura vetorial inicial via `pandas.read_csv`.

## Diretriz de Otimização (Economia de Tokens em IA)
**Como e Por quê:** Em caso de erro de rede ou estrutura JSON aninhada, **não** cole o payload inteiro recebido da API no chat. Forneça apenas o Header da resposta, o código de erro HTTP (ex: 403, 429) ou apenas 1 nível da árvore JSON. Isso poupa milhares de tokens, mantendo a resposta da IA direta e voltada para a arquitetura do código de extração.
