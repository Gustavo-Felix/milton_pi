-- 002 — Segunda leva de colunas do DER v3 (miltonpiv3.drawio) e do instrucoes.md.
-- Idempotente: pode ser executada mais de uma vez.
-- Aplicar: docker exec -i milton_postgres psql -U postgres -d milton_pi < infra/migrations/002_der_produto_rede_populacao.sql

-- instrucoes.md § PLANO_SAUDE: abrangencia_geografica <- ABRANGENCIA_COBERTURA (PDA 008)
ALTER TABLE produto_saude ADD COLUMN IF NOT EXISTS abrangencia_geografica VARCHAR(60);

-- DER REDE_CREDENCIADA_SINTETICA: histórico mensal da rede e contagens por tipo
-- (instrucoes.md: GROUP BY ID_PLANO, CD_MUNICIPIO, ID_SERIE_MENSAL).
ALTER TABLE rede_credenciada_sintetica ADD COLUMN IF NOT EXISTS id_serie_mensal BIGINT REFERENCES serie_temporal_mensal(id);
ALTER TABLE rede_credenciada_sintetica ADD COLUMN IF NOT EXISTS qtd_hospitais INTEGER;
ALTER TABLE rede_credenciada_sintetica ADD COLUMN IF NOT EXISTS qtd_pronto_atendimento INTEGER;
ALTER TABLE rede_credenciada_sintetica ADD COLUMN IF NOT EXISTS qtd_laboratorios INTEGER;
ALTER TABLE rede_credenciada_sintetica ADD COLUMN IF NOT EXISTS qtd_consultorios INTEGER;

-- DER: SERIE_TEMPORAL_ANUAL "vigência" MUNICIPIO -> ano a que municipio.populacao_estimada se refere.
-- A série histórica completa (SIDRA 6579) fica em perfil_saude_publica (id_municipio, id_serie_anual).
ALTER TABLE municipio ADD COLUMN IF NOT EXISTS id_serie_anual BIGINT REFERENCES serie_temporal_anual(id);

-- Formato antigo da rede (uma linha por tipo_servico, sem série mensal): as linhas saem
-- e a próxima carga da rede regrava no formato do DER; as colunas não estão no DER.
DELETE FROM rede_credenciada_sintetica WHERE id_serie_mensal IS NULL;
ALTER TABLE rede_credenciada_sintetica DROP COLUMN IF EXISTS tipo_servico;
ALTER TABLE rede_credenciada_sintetica DROP COLUMN IF EXISTS especialidade;
ALTER TABLE rede_credenciada_sintetica DROP COLUMN IF EXISTS quantidade_estimada;
