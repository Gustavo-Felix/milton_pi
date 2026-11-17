-- 001 — Alinha o banco ao DER v3 do cliente (miltonpiv3.drawio) e ao instrucoes.md.
-- Idempotente: pode ser executada mais de uma vez. Só adiciona colunas; não apaga dados.
-- Aplicar: docker exec -i milton_postgres psql -U postgres -d milton_pi < infra/migrations/001_conformidade_der.sql

-- instrucoes.md regra 3 / DER: faixa etária quebrada em início e fim (numéricos)
ALTER TABLE preco_faixa_etaria ADD COLUMN IF NOT EXISTS faixa_etaria_inicial SMALLINT;
ALTER TABLE preco_faixa_etaria ADD COLUMN IF NOT EXISTS faixa_etaria_final SMALLINT;

-- DER / instrucoes.md: tipo_abrangencia <- "Comercialização Ativa"
ALTER TABLE abrangencia_plano ADD COLUMN IF NOT EXISTS tipo_abrangencia VARCHAR(30);

-- DER: população estimada no município (IBGE SIDRA 6579) e renda média per capita
ALTER TABLE municipio ADD COLUMN IF NOT EXISTS populacao_estimada INTEGER;
ALTER TABLE municipio ADD COLUMN IF NOT EXISTS renda_media_per_capita NUMERIC(12,2);

-- DER / instrucoes.md: nota_sinistralidade_media <- PCT_DESP_ASS do PDA 031 (painel de precificação)
ALTER TABLE produto_saude ADD COLUMN IF NOT EXISTS nota_sinistralidade_media NUMERIC(7,4);
