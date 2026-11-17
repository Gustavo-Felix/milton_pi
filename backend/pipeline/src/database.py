"""
FASE 02 — Configuração e Conexão PostgreSQL.

Lê variáveis de ambiente (com fallback para .env na raiz do projeto),
constrói a URL de conexão e valida que o schema PostgreSQL existe e
bate com o DDL definido em infra/init.sql.

Regras:
- Nunca cria banco ou tabelas — isso é responsabilidade do infra/init.sql
  (Docker ou infra/db_setup.sh no banco remoto).
- Banco remoto (AWS RDS): SSL obrigatório e sem limite de tempo por comando
  nem por transação ociosa, porque a carga baixa arquivos dentro da transação.
- Falha explicitamente se a conexão ou schema estiver incorreto.
- Porta padrão: 5432 (o Compose expõe 55432 no host; ver .env).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import URL, Engine, make_url

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}  # "db" = serviço do docker-compose
SESSION_SETTINGS = "SET statement_timeout = 0; SET idle_in_transaction_session_timeout = 0"


# Conjunto mínimo de tabelas/colunas esperado, derivado de infra/init.sql
EXPECTED_SCHEMA: dict[str, set[str]] = {
    "estado": {"id", "sigla", "nome", "regiao"},
    "municipio": {"id", "codigo_ibge", "nome", "id_estado", "populacao_estimada", "id_serie_anual"},
    "serie_temporal_anual": {"id", "ano"},
    "serie_temporal_mensal": {"id", "id_serie_anual", "ano", "mes", "competencia"},
    "operadora": {"id", "registro_ans", "cnpj", "razao_social", "modalidade", "situacao"},
    "produto_saude": {"id", "codigo_plano_ans", "id_operadora", "nome_plano", "abrangencia_geografica",
                      "nota_sinistralidade_media"},
    "abrangencia_plano": {"id_produto_saude", "id_municipio", "tipo_abrangencia"},
    "preco_faixa_etaria": {"id", "id_produto_saude", "id_serie_mensal", "faixa_etaria", "valor_mensalidade",
                           "faixa_etaria_inicial", "faixa_etaria_final"},
    "market_share": {"id", "id_municipio", "id_serie_mensal", "id_operadora", "total_beneficiarios",
                     "percentual_market_share"},
    "rede_credenciada_sintetica": {"id", "id_produto_saude", "id_municipio", "id_serie_mensal", "qtd_hospitais",
                                   "qtd_pronto_atendimento", "qtd_laboratorios", "qtd_consultorios",
                                   "fonte_estimativa"},
    "perfil_saude_publica": {"id", "id_municipio", "id_serie_anual", "populacao_estimada",
                             "taxa_cobertura_plano_saude"},
}


def _load_dotenv() -> None:
    """Lê .env da raiz do projeto e exporta variáveis não definidas no ambiente."""
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(name, value)


def database_url() -> str:
    """Retorna a URL de conexão PostgreSQL ou levanta RuntimeError."""
    _load_dotenv()

    url = os.getenv("DATABASE_URL")
    if url:
        # Provedores entregam postgresql:// (ou postgres://); o driver é sempre psycopg2.
        return re.sub(r"^postgres(?:ql)?(?:\+psycopg2)?://", "postgresql+psycopg2://", url)

    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        raise RuntimeError(
            "Banco não configurado. Defina DATABASE_URL ou "
            + ", ".join(required)
            + f". Ausentes: {', '.join(missing)}"
        )

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    db = os.getenv("POSTGRES_DB")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


def engine_options(url: str) -> tuple[URL, dict]:
    """URL validada + connect_args. Banco remoto sem sslmode recebe require."""
    parsed = make_url(url)
    connect_args = {}
    if parsed.host not in LOCAL_HOSTS:
        # A transação fica minutos sem tráfego enquanto baixa arquivos; NAT/firewall derruba TCP ocioso.
        connect_args.update(keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5)
        if "sslmode" not in parsed.query:
            connect_args["sslmode"] = "require"
    return parsed, connect_args


def create_db_engine() -> Engine:
    """Cria e retorna um Engine PostgreSQL com pool_pre_ping."""
    url, connect_args = engine_options(database_url())
    # A pipeline usa uma conexão por vez; pool pequeno evita conexões ociosas.
    engine = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=1, connect_args=connect_args)
    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            "A pipeline exige PostgreSQL. Dialeto detectado: " + engine.dialect.name
        )

    @event.listens_for(engine, "connect")
    def _session_settings(dbapi_conn, _record) -> None:
        # Bancos gerenciados podem limitar o tempo por comando (parameter group); a carga não pode cair no meio.
        with dbapi_conn.cursor() as cur:
            cur.execute(SESSION_SETTINGS)
        dbapi_conn.commit()

    return engine


def validate_schema(engine: Engine) -> None:
    """Verifica se o schema do banco bate com EXPECTED_SCHEMA.

    Levanta RuntimeError descrevendo tabelas ou colunas ausentes.
    Não cria nada — apenas valida.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    missing_tables = sorted(set(EXPECTED_SCHEMA) - existing_tables)
    missing_columns: dict[str, list[str]] = {}

    for table, cols in EXPECTED_SCHEMA.items():
        if table not in existing_tables:
            continue
        db_cols = {c["name"] for c in inspector.get_columns(table)}
        absent = sorted(cols - db_cols)
        if absent:
            missing_columns[table] = absent

    if missing_tables or missing_columns:
        parts: list[str] = []
        if missing_tables:
            parts.append("tabelas ausentes: " + ", ".join(missing_tables))
        if missing_columns:
            parts.append(
                "colunas ausentes: "
                + "; ".join(
                    f"{t}({', '.join(c)})" for t, c in missing_columns.items()
                )
            )
        raise RuntimeError("Schema incompatível: " + " | ".join(parts))


def ping(engine: Engine) -> bool:
    """Testa conectividade com o banco. Retorna True se OK."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
