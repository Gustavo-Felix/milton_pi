# Fase 1: Planejamento e Modelagem Relacional

## Contexto Inicial (Pré-requisito)
Início do desenvolvimento do pipeline ETL (Extract, Transform, Load) para o Projeto Integrador, substituindo a abordagem de dados mockados pela integração com dados reais (Ministério da Saúde e ANS).

## Objetivo
Provisionar a infraestrutura do banco de dados e modelar o esquema relacional fisicamente.

## Requisitos Funcionais (RF)
- RF01: O sistema deve possuir tabelas relacionais criadas exatamente conforme o documento de modelagem (`correlacao_apis_der.pdf`).
- RF02: As chaves primárias e estrangeiras devem refletir e garantir os relacionamentos entre os dados do MS e da ANS.

## Requisitos Não Funcionais (RNF)
- RNF01: O Sistema Gerenciador de Banco de Dados (SGBD) utilizado deve ser o PostgreSQL.
- RNF02: O armazenamento do servidor deve ser dimensionado previamente, baseando-se no `Calculo_Tamanho_Bytes_PI.pdf`.

## Instruções de Implementação (Enxutas)
1. Instalar e configurar a instância do PostgreSQL.
2. Executar script DDL (Data Definition Language) com `CREATE TABLE`, tipagens e restrições.
3. Configurar permissões de usuário (role) restritas apenas para as operações do pipeline.

## Diretriz de Otimização (Economia de Tokens em IA)
**Como e Por quê:** Ao utilizar IA para resolver problemas de banco de dados, nunca envie o script DDL inteiro. Envie *apenas* a instrução `CREATE TABLE` da tabela com erro. Isso preserva a janela de contexto da IA (reduzindo tokens) e garante que ela não "esqueça" o foco do problema, resultando em respostas mais rápidas e sem alucinações.
