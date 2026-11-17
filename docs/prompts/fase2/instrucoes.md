# Manual de Integração ANS para o Sistema de Recomendação

Este documento detalha como cada arquivo extraído da ANS se encaixa **exatamente no seu modelo de banco de dados atual** (Diagrama ER). Ele serve como um guia definitivo e um prompt de instrução para uma IA de Engenharia de Dados encarregada de manter o sistema atualizado.

---

## 1. Mapeamento de Domínios (De -> Para)

A sua base de dados possui um foco claro em **séries temporais**, **geolocalização** e **recomendação analítica**. Abaixo está a regra de transformação de cada arquivo bruto da ANS para as suas tabelas:

### Dimensões Básicas
- **`SERIE_TEMPORAL_ANUAL` / `SERIE_TEMPORAL_MENSAL`**: Populares automaticamente a partir da data de ingestão (ex: Mês/Ano extraídos dos arquivos mensais de precificação e SIB).
- **`ESTADO` / `MUNICIPIO`**: Carregados a partir do arquivo auxiliar `pda-054-geografia.csv` e `pda-008-tabela_auxiliar_de_detalhamento_de_municipios.csv`.

### Tabela: `OPERADORA`
- **Fonte ANS**: `Relatorio_cadop.csv`
- **Mapeamento**:
  - `id_operadora` <- `REGISTRO_OPERADORA`
  - `razao_social` <- `RAZAO_SOCIAL`
  - `cnpj` <- `CNPJ`
  - `modalidade` <- `MODALIDADE`

### Tabela: `PLANO_SAUDE`
- **Fonte ANS**: `pda-008-caracteristicas_produtos_saude_suplementar.csv` e Painel de Precificação.
- **Mapeamento**:
  - `id_plano` <- `ID_PLANO`
  - `id_operadora` <- `REGISTRO_OPERADORA`
  - `nome_comercial` <- `NM_PLANO`
  - `tipo_contratacao` <- `CONTRATACAO`
  - `abrangencia_geografica` <- `ABRANGENCIA_COBERTURA`
  - `nota_sinistralidade_media` <- *(Cálculo derivado da NT do arquivo `pda-031-painel_precificacao.zip`, cruzando PCT_DESP_ASS)*

### Tabela: `ABRANGENCIA_PLANO`
- **Fonte ANS**: `pda_area_comer_plano_ntrp.csv`
- **Mapeamento**:
  - `id_plano` <- `ID_PLANO`
  - `id_municipio` <- `CD_MUNICIPIO`
  - `tipo_abrangencia` <- Comercialização Ativa

### Tabela: `PRECO_FAIXA_ETARIA`
- **Fonte ANS**: `rel_faixa_de_preco.csv` e arquivos do diretório `valor_comercial_medio_por_municipio_NTRP-054/`
- **Mapeamento**:
  - `id_plano` <- `ID_PLANO`
  - O ETL da IA deverá fazer um `SPLIT` na coluna texto `FAIXA_ETARIA` para popular `faixa_etaria_inicial` e `faixa_etaria_final`.
  - `valor` <- `VCM` (Valor Comercial Médio) ou `VALOR_FINAL_FAIXA`.

### Tabela: `MARKET_SHARE`
- **Fonte ANS**: `pda-047-taxa_cobertura.csv` (Taxa por Município) + Diretório `dados_de_beneficiarios_por_operadora/`
- **Mapeamento (Agregação)**:
  - `qtd_beneficiarios` <- `BENEF_TOTAL` ou Contagem agrupada (`COUNT`) do arquivo `sib_ativo_[UF].zip` por Município e Operadora.
  - `percentual_penetracao` <- `TX_COBERT_TOTAL` extraído diretamente da base 047.

### Tabela: `REDE_CREDENCIADA_SINTETICA` (Atenção Máxima)
- **Fonte ANS**: `produtos_e_prestadores_hospitalares.zip` (Arquivo Gigante ~ 1.5GB)
- **Mapeamento (ETL Sintético Obrigatório)**: 
  Sua modelagem é extremamente inteligente pois evita salvar as centenas de milhares de hospitais. A IA deve agregar a base original agindo da seguinte forma:
  ```sql
  -- Lógica de transformação para o Agente de Dados
  SELECT 
    ID_PLANO, 
    CD_MUNICIPIO,
    SUM(CASE WHEN DE_CLAS_ESTB_SAUDE LIKE '%HOSPITAL%' THEN 1 ELSE 0 END) as qtd_hospitais,
    SUM(CASE WHEN DE_CLAS_ESTB_SAUDE LIKE '%PRONTO ATENDIMENTO%' THEN 1 ELSE 0 END) as qtd_pronto_atendimento,
    SUM(CASE WHEN DE_CLAS_ESTB_SAUDE LIKE '%LABORATORIO%' THEN 1 ELSE 0 END) as qtd_laboratorios,
    SUM(CASE WHEN DE_TIPO_PRESTADOR LIKE '%CONSULTORIO%' THEN 1 ELSE 0 END) as qtd_consultorios
  FROM prestadores_raw
  GROUP BY ID_PLANO, CD_MUNICIPIO, ID_SERIE_MENSAL;
  ```

---

## 2. Prompt de Instrução (SOP) para IAs de Manutenção

Se você plugar uma IA (Agente) para executar esse ETL no seu sistema via Node.js/Python, injete nela as seguintes instruções (System Prompt):

> **ROLE**: Você é um Agente Analítico de ETL especializado no ecossistema de saúde brasileiro.
> **TAREFA**: Manter a base de recomendação atualizada consumindo o FTP de Dados Abertos da ANS (`https://dadosabertos.ans.gov.br/FTP/PDA/`).
> 
> **REGRAS DE TRANSFORMAÇÃO E CARGA (PIPELINE)**:
> 1. **Controle de Séries Temporais**: Toda vez que baixar um arquivo novo, você **deve** criar um novo registro na tabela `SERIE_TEMPORAL_MENSAL` para garantir o tracking histórico de preços e rede credenciada.
> 
> 2. **Processamento em Chunks (Memória Limitada)**: O arquivo `produtos_e_prestadores_hospitalares.zip` é gigantesco. Você NÃO pode carregá-lo todo em RAM (DataFrame Pandas tradicional). Utilize processamento por lotes (streaming/chunks). Para cada lote, agrupe por `ID_PLANO` e `CD_MUNICIPIO`, conte as classificações do estabelecimento, e então faça um Upsert agregando os números sintéticos na tabela `REDE_CREDENCIADA_SINTETICA`.
> 
> 3. **Sanitização de Faixas Etárias**: Ao ler o CSV de precificação, a ANS traz as idades misturadas (ex: `00 a 18 anos`). Você DEVE quebrar essa string usando Regex e mapear para os campos numéricos/inteiros `faixa_etaria_inicial` e `faixa_etaria_final` na tabela `PRECO_FAIXA_ETARIA`.
> 
> 4. **Tratativa de Fato**: Ao calcular o `MARKET_SHARE`, efetue a junção (Join) das contagens do diretório `dados_de_beneficiarios_por_operadora/` com a tabela `MUNICIPIO`. Caso um município não exista, crie-o silenciosamente baseando-se no código IBGE recebido.
> 
> 5. **Proteção Transacional**: Todas as cargas de uma competência mensal devem ocorrer dentro de uma transação SQL (`BEGIN; ... COMMIT;`). Caso um ZIP falhe ao baixar ou extrair, execute `ROLLBACK` para evitar que o sistema de recomendação cruze redes credenciadas incompletas.
