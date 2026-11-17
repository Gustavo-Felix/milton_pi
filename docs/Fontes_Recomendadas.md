# Fontes de Dados Recomendadas para o Projeto Integrador (API MS e FTP ANS)

Este documento centraliza os endpoints da API do Ministério da Saúde e os diretórios do FTP da Agência Nacional de Saúde Suplementar (ANS) que são **estritamente necessários** para alimentar as 14 tabelas modeladas na Fase 1 do Projeto Integrador. O objetivo é evitar a ingestão de dados não mapeados (como os arquivos massivos .dbc e denúncias do consumidor) que impactariam a performance e os custos de armazenamento.

## 1. Agência Nacional de Saúde Suplementar (ANS)
**Origem:** Portal de Dados Abertos - `https://dadosabertos.ans.gov.br/FTP/PDA/`
**Mecanismo de Ingestão:** Download e leitura vetorial de arquivos textuais (CSV) particionados.

### Bases Selecionadas para o ETL
*   **`operadoras_de_plano_de_saude_ativas/` e `operadoras_de_plano_de_saude_canceladas/`**
    *   **Tabela Destino:** `operadora`
    *   **Conteúdo:** Cadastro central (CNPJ, Razão Social, Status) de todas as empresas de saúde.
*   **`caracteristicas_produtos_saude_suplementar-008/`**
    *   **Tabela Destino:** `produto_saude`
    *   **Conteúdo:** Características centrais (código do plano, segmentação, tipo de contratação) dos planos regulamentados.
*   **`area_comercializacao_planos_ntrp/`**
    *   **Tabela Destino:** `abrangencia_plano`
    *   **Conteúdo:** Relação cruzada entre o Código do Plano e o Código IBGE do município onde a venda é autorizada. (Nota: incluir campo de `tipo_abrangencia`).
*   **`faixa_de_preco/` e `valor_comercial_medio_por_municipio_NTRP/`**
    *   **Tabela Destino:** `preco_faixa_etaria`
    *   **Conteúdo:** Matriz de precificação por idade para cada produto.
*   **`produtos_e_prestadores_hospitalares/` e `operadoras_e_prestadores_nao_hospitalares/`**
    *   **Tabela Destino:** `rede_credenciada_sintetica`
    *   **Conteúdo:** Contagem ou relação de infraestrutura (hospitais e clínicas) vinculadas aos produtos.
*   **`informacoes_consolidadas_de_beneficiarios/`**
    *   **Tabela Destino:** `market_share`
    *   **Conteúdo:** Volume de vidas ativas (mensal/anual), base para o cálculo de Market Share.

---

## 2. Ministério da Saúde (MS) - Dados Abertos
**Origem:** API REST - `https://apidadosabertos.saude.gov.br`
**Mecanismo de Ingestão:** Requisições HTTP GET (com paginação e rate limiting).

### Endpoints Selecionados para o ETL
*   **`/cnes/estabelecimentos` (Cadastro Nacional de Estabelecimentos de Saúde)**
    *   **Tabelas Destino:** `estado`, `municipio`, `perfil_saude_publica`
    *   **Conteúdo:** Localização geográfica e tipologia de estabelecimentos públicos. (A dimensão de município e estado servirá de base estrutural para todo o banco).
*   **`/siasus/procedimentos` ou `/sihsus/internacoes` (Sistemas de Informação Ambulatorial/Hospitalar)**
    *   **Tabelas Destino:** `serie_temporal_anual`, `serie_temporal_mensal`, `perfil_saude_publica`
    *   **Conteúdo:** Frequência de agravos e atendimentos na rede pública, essenciais para cruzar a demanda pública x oferta suplementar.
