# Fase 5: Automação e Logs

## Resumo da Fase Anterior
A Fase 4 garantiu que o dado, após ser extraído, chegasse tratado e pronto para uso no banco de dados. A Fase 5 visa encapsular todas as Fases (3 e 4) em um fluxo contínuo e monitorável, sem necessidade de gatilho manual.

## Objetivo
Automatizar as execuções do ETL e gerar registros (rastreabilidade) de tudo o que ocorre dentro do pipeline.

## Requisitos Funcionais (RF)
- RF01: Um script "orquestrador" deve chamar sequencialmente as rotinas de extração, transformação e carga.
- RF02: O sistema deve gerar registros de eventos constando: hora de início, quantidade de linhas inseridas, hora de fim e status (Sucesso/Erro).

## Requisitos Não Funcionais (RNF)
- RNF01: O sistema deve tratar exceções críticas sem quebrar (fechar abruptamente) processos paralelos, usando blocos nativos `try/except`.
- RNF02: Utilizar a biblioteca nativa `logging` do Python em modo *append* para arquivos texto.
- RNF03: A execução deve ser agendada no sistema operacional (via Cronjob/Task Scheduler) ou ferramenta de CI/CD.

## Instruções de Implementação (Enxutas)
1. Configurar `logging.basicConfig()` para definir formatação: `[Data] [Nível] [Mensagem]`.
2. Encapsular a função orquestradora principal em um bloco amplo de tratamento de exceções para capturar falhas gerais.
3. Criar shell script ou arquivo yaml (ex: GitHub Actions) definindo o *cron* de execução (ex: diário à meia-noite).

## Diretriz de Otimização (Economia de Tokens em IA)
**Como e Por quê:** Se a pipeline falhar na madrugada e você precisar debugar com a IA, não envie o log inteiro de execução ou o código completo. Envie estritamente as 5 últimas linhas referentes ao *Stack Trace* (o erro retornado pelo Python). Ao enviar apenas o erro específico, a IA gasta o processamento de tokens focando apenas na solução raiz daquele *Traceback*, ignorando o que funcionou bem.
