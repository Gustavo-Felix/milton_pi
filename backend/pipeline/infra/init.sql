-- Schema da pipeline (DER v3 do cliente: docs/miltonpiv3.drawio(1).png).
-- Instalação nova: Docker (docker-entrypoint-initdb.d) ou banco remoto, como o RDS (bash infra/db_setup.sh).
-- Idempotente. Bancos criados antes desta versão: aplicar infra/migrations/ em ordem.

-- 1. Estado
CREATE TABLE IF NOT EXISTS estado (
    id BIGINT PRIMARY KEY,
    codigo_ibge VARCHAR(2),
    sigla VARCHAR(2),
    nome VARCHAR(100),
    regiao VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 3. Série Temporal Anual (antes de município, por causa da FK id_serie_anual)
CREATE TABLE IF NOT EXISTS serie_temporal_anual (
    id BIGINT PRIMARY KEY,
    ano SMALLINT
);

-- 2. Município
CREATE TABLE IF NOT EXISTS municipio (
    id BIGINT PRIMARY KEY,
    codigo_ibge VARCHAR(7),
    nome VARCHAR(150),
    id_estado BIGINT REFERENCES estado(id),
    latitude NUMERIC(9,6),
    longitude NUMERIC(9,6),
    populacao_estimada INTEGER,                                 -- IBGE SIDRA 6579, último ano publicado
    id_serie_anual BIGINT REFERENCES serie_temporal_anual(id),  -- DER "vigência": ano da população
    renda_media_per_capita NUMERIC(12,2),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 4. Série Temporal Mensal
CREATE TABLE IF NOT EXISTS serie_temporal_mensal (
    id BIGINT PRIMARY KEY,
    id_serie_anual BIGINT REFERENCES serie_temporal_anual(id),
    ano SMALLINT,
    mes SMALLINT,
    competencia VARCHAR(7)
);

-- 5. Operadora
CREATE TABLE IF NOT EXISTS operadora (
    id BIGINT PRIMARY KEY,
    registro_ans VARCHAR(6),
    cnpj VARCHAR(14),
    razao_social VARCHAR(200),
    nome_fantasia VARCHAR(200),
    modalidade VARCHAR(60),
    uf_sede VARCHAR(2),
    situacao VARCHAR(20),
    data_registro DATE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 6. Produto Saúde (Planos)
CREATE TABLE IF NOT EXISTS produto_saude (
    id BIGINT PRIMARY KEY,
    codigo_plano_ans VARCHAR(12),
    nome_plano VARCHAR(200),
    id_operadora BIGINT REFERENCES operadora(id),
    tipo_contratacao VARCHAR(30),
    segmentacao VARCHAR(50),
    cobertura VARCHAR(30),
    abrangencia_geografica VARCHAR(60),        -- ABRANGENCIA_COBERTURA do PDA 008
    possui_copart BOOLEAN,
    ativo BOOLEAN,
    nota_sinistralidade_media NUMERIC(7,4),    -- PCT_DESP_ASS do PDA 031
    data_inicio_venda DATE,
    data_fim_venda DATE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 7. Abrangência Plano
CREATE TABLE IF NOT EXISTS abrangencia_plano (
    id_produto_saude BIGINT REFERENCES produto_saude(id),
    id_municipio BIGINT REFERENCES municipio(id),
    tipo_abrangencia VARCHAR(30),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_produto_saude, id_municipio)
);

-- 8. Preço Faixa Etária
CREATE TABLE IF NOT EXISTS preco_faixa_etaria (
    id BIGINT PRIMARY KEY,
    id_produto_saude BIGINT REFERENCES produto_saude(id),
    id_serie_mensal BIGINT REFERENCES serie_temporal_mensal(id),
    faixa_etaria VARCHAR(20),
    faixa_etaria_inicial SMALLINT,
    faixa_etaria_final SMALLINT,
    valor_mensalidade NUMERIC(12,2),
    reajuste_percentual NUMERIC(5,4),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 9. Market Share
CREATE TABLE IF NOT EXISTS market_share (
    id BIGINT PRIMARY KEY,
    id_municipio BIGINT REFERENCES municipio(id),
    id_serie_mensal BIGINT REFERENCES serie_temporal_mensal(id),
    id_operadora BIGINT REFERENCES operadora(id),
    total_beneficiarios INTEGER,
    percentual_market_share NUMERIC(5,4),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 10. Rede Credenciada Sintética (formato do DER: uma linha por plano, município e mês)
CREATE TABLE IF NOT EXISTS rede_credenciada_sintetica (
    id BIGINT PRIMARY KEY,
    id_produto_saude BIGINT REFERENCES produto_saude(id),
    id_municipio BIGINT REFERENCES municipio(id),
    id_serie_mensal BIGINT REFERENCES serie_temporal_mensal(id),
    qtd_hospitais INTEGER,
    qtd_pronto_atendimento INTEGER,
    qtd_laboratorios INTEGER,
    qtd_consultorios INTEGER,
    fonte_estimativa VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 11. Perfil Saúde Pública
CREATE TABLE IF NOT EXISTS perfil_saude_publica (
    id BIGINT PRIMARY KEY,
    id_municipio BIGINT REFERENCES municipio(id),
    id_serie_anual BIGINT REFERENCES serie_temporal_anual(id),
    populacao_estimada INTEGER,                -- série histórica do SIDRA 6579
    idh NUMERIC(5,3),
    taxa_cobertura_plano_saude NUMERIC(5,4),
    renda_per_capita NUMERIC(12,2),
    taxa_mortalidade_infantil NUMERIC(6,2),
    indice_gini NUMERIC(5,3),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 12. Usuário
CREATE TABLE IF NOT EXISTS usuario (
    id BIGINT PRIMARY KEY,
    email VARCHAR(255),
    nome VARCHAR(150),
    senha_hash TEXT,
    perfil_busca JSONB,
    ativo BOOLEAN,
    ultimo_acesso TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 13. Histórico Busca
CREATE TABLE IF NOT EXISTS historico_busca (
    id BIGINT PRIMARY KEY,
    id_usuario BIGINT REFERENCES usuario(id),
    id_municipio BIGINT REFERENCES municipio(id),
    filtros_utilizados JSONB,
    resultado_ids JSONB,
    canal_acesso VARCHAR(30),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 14. Feedback Usuário
CREATE TABLE IF NOT EXISTS feedback_usuario (
    id BIGINT PRIMARY KEY,
    id_historico BIGINT REFERENCES historico_busca(id),
    id_produto_saude BIGINT REFERENCES produto_saude(id),
    nota SMALLINT,
    comentario TEXT,
    util BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
