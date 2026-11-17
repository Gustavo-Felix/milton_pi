"""
ORQUESTRADOR PRINCIPAL — Pipeline ANS de Engenharia de Dados.

  FASE 02 — Configuração e conexão PostgreSQL (database.py)
  FASE 03/04 — Download e leitura dos CSV/ZIP em chunks (downloader.py)
  FASE 05/06 — Staging e transformações (transforms.py)
  FASE 07 — Carga no PostgreSQL (loader.py)
  FASE 08 — Transação, idempotência e tratamento de erros (aqui)

Regras:
- A competência mensal inteira roda em UMA transação: qualquer falha
  (download, ZIP corrompido, carga) faz ROLLBACK de tudo
  (`--transacao-por-dataset` relaxa isso para depuração).
- Chunks de 1000 registros; arquivos grandes nunca são carregados na RAM.
- Idempotência: ids determinísticos + ON CONFLICT; snapshots por fonte/
  competência são substituídos (DELETE + INSERT) na mesma transação.
- Datasets são executados em ordem de dependência (FKs), não na ordem do config.
"""

from __future__ import annotations

import argparse
import re
import json
import logging
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import requests
from sqlalchemy.engine import Connection

from . import transforms as T
from .database import create_db_engine, ping, validate_schema
from .downloader import (
    CHUNK_SIZE,
    build_session,
    is_bad_zip_crc_error,
    iter_dataset_sources,
)
from .loader import Loader, get_or_create_serie_mensal

ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "logs" / "pipeline.log"
CONFIG_PATH = ROOT / "config.json"

logger = logging.getLogger(__name__)


def _setup_logging(level: int = logging.INFO) -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s — %(message)s")
    root = logging.getLogger()
    if root.handlers:  # evita handlers duplicados em chamadas repetidas
        return
    root.setLevel(level)
    for handler in (
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def _load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _get_ans_entry(cfg: dict) -> Optional[dict]:
    for fonte in cfg.get("projeto_integrador", {}).get("fontes_de_dados", []):
        if fonte.get("id") == "ans_pda":
            return fonte
    return None


# ---------------------------------------------------------------------------
# Contexto e handlers por dataset
# ---------------------------------------------------------------------------

@dataclass
class Context:
    loader: Loader
    http: requests.Session
    base_url: str
    ano: int
    mes: int

    @property
    def competencia(self) -> int:
        return self.ano * 100 + self.mes

    def serie_from_yyyymm(self, yyyymm: int) -> int:
        return get_or_create_serie_mensal(self.loader.conn, yyyymm // 100, yyyymm % 100)

    def sources(self, ds_cfg: dict, usecols):
        return closing(iter_dataset_sources(
            self.http, self.base_url, ds_cfg,
            competencia=self.competencia, usecols=usecols, chunk_size=CHUNK_SIZE,
        ))


def _handle_geografia(ctx: Context, ds_cfg: dict) -> int:
    rows = 0
    with ctx.sources(ds_cfg, T.GEOGRAFIA_COLS) as sources:
        for _, chunks in sources:
            for chunk in chunks:
                estados, municipios = T.transform_geografia(chunk)
                ctx.loader.upsert("estado", estados)
                rows += ctx.loader.upsert("municipio", municipios)
    return rows


def _handle_operadora(ctx: Context, ds_cfg: dict) -> int:
    rows = 0
    situacao = ds_cfg.get("situacao", "ATIVA")
    with ctx.sources(ds_cfg, T.OPERADORA_COLS) as sources:
        for _, chunks in sources:
            for chunk in chunks:
                rows += ctx.loader.upsert("operadora", T.transform_operadora(chunk, situacao))
    return rows


def _get_json(ctx: Context, url: str):
    with ctx.http.get(url, timeout=(30, 300)) as resp:
        resp.raise_for_status()
        return resp.json()


SIDRA_PERIODOS_POR_CHAMADA = 8  # 6579 com N6[all]: 9+ períodos por chamada respondem HTTP 500


def _get_sidra_serie(ctx: Context, ds_cfg: dict) -> pd.DataFrame:
    """Série histórica completa do SIDRA 6579, em lotes de períodos."""
    periodos = [p["id"] for p in _get_json(ctx, ds_cfg["url_populacao_periodos"])]
    partes = []
    for i in range(0, len(periodos), SIDRA_PERIODOS_POR_CHAMADA):
        lote = "|".join(periodos[i:i + SIDRA_PERIODOS_POR_CHAMADA])
        partes.append(T.transform_sidra_populacao(_get_json(ctx, ds_cfg["url_populacao"].format(periodos=lote))))
    pop = pd.concat(partes, ignore_index=True)
    logger.info("[IBGE] SIDRA 6579: %d valores, anos %s", len(pop), sorted(pop["ano"].unique().tolist()))
    return pop


def _handle_ibge_localidades(ctx: Context, ds_cfg: dict) -> int:
    estados = T.transform_ibge_estados(_get_json(ctx, ds_cfg["url_estados"]))
    municipios = T.transform_ibge_municipios(_get_json(ctx, ds_cfg["url_municipios"]))
    pop = _get_sidra_serie(ctx, ds_cfg) if ds_cfg.get("url_populacao") else None
    if pop is not None:
        municipios = municipios.merge(T.populacao_vigente(pop), on="id", how="left")
        municipios = municipios.astype({"populacao_estimada": "Int64", "id_serie_anual": "Int64"})
        ctx.loader.ensure_series_anuais(pop["ano"])
    ctx.loader.upsert("estado", estados)
    rows = len(estados) + ctx.loader.upsert("municipio", municipios)
    if pop is not None:
        # Série histórica por ano (instrucoes: população do município por ano)
        serie = T.finalize_populacao_anual(pop)
        ctx.loader.ensure_municipios(serie["id_municipio"])
        rows += ctx.loader.upsert("perfil_saude_publica", serie)
    return rows


def _handle_painel_precificacao(ctx: Context, ds_cfg: dict) -> int:
    """Atualiza só a nota de sinistralidade de planos já cadastrados."""
    agg = T.Aggregator(["id"], ["soma", "n"])
    with ctx.sources(ds_cfg, T.PAINEL_COLS) as sources:
        for _, chunks in sources:
            for chunk in chunks:
                agg.add(T.transform_painel_chunk(chunk))
    df = T.finalize_sinistralidade(agg.result())
    df = ctx.loader.keep_known(df, "id", "produto_saude") if not df.empty else df
    return ctx.loader.upsert("produto_saude", df)


def _handle_produto_saude(ctx: Context, ds_cfg: dict) -> int:
    rows = 0
    with ctx.sources(ds_cfg, T.PRODUTO_SAUDE_COLS) as sources:
        for _, chunks in sources:
            for chunk in chunks:
                produtos, operadoras = T.transform_produto_saude(chunk)
                # Não sobrescreve os dados mais completos vindos do Relatorio_cadop
                ctx.loader.upsert("operadora", operadoras, mode="ignore")
                rows += ctx.loader.upsert("produto_saude", produtos)
    return rows


def _handle_abrangencia(ctx: Context, ds_cfg: dict) -> int:
    rows = 0
    keys = ("id_produto_saude", "id_municipio")
    # O arquivo é a área de comercialização ativa inteira: par que saiu dele deixou de ser vendido.
    ctx.loader.delete_where("abrangencia_plano")
    with ctx.sources(ds_cfg, T.ABRANGENCIA_COLS) as sources:
        for _, chunks in sources:
            for chunk in chunks:
                df = ctx.loader.keep_known(T.transform_abrangencia_plano(chunk), "id_produto_saude", "produto_saude")
                ctx.loader.ensure_municipios(df["id_municipio"])
                rows += ctx.loader.upsert("abrangencia_plano", df, conflict_cols=keys)
    return rows


def _handle_preco_faixa_etaria(ctx: Context, ds_cfg: dict) -> int:
    """O período é o do arquivo (YYYYMM.zip), não o da execução."""
    rows = 0
    # A média por (plano, faixa) vale para o mês inteiro, mesmo que o ZIP traga vários CSVs.
    agg = T.Aggregator(["id_serie_mensal", "id_produto_saude", "cd_faixa"], ["soma", "n"])
    with ctx.sources(ds_cfg, T.VCM_COLS) as sources:
        for name, chunks in sources:
            serie = ctx.serie_from_yyyymm(int(re.search(r"(\d{6})", name).group(1)))
            for chunk in chunks:
                agg.add(T.transform_vcm_chunk(chunk).assign(id_serie_mensal=serie))
            logger.info("[PRECO] %s -> série %d", name, serie)
    for serie, part in agg.result().groupby("id_serie_mensal"):
        df = T.finalize_preco_faixa_etaria(part.drop(columns="id_serie_mensal").reset_index(drop=True), int(serie))
        df = ctx.loader.keep_known(df, "id_produto_saude", "produto_saude") if not df.empty else df
        ctx.loader.delete_where("preco_faixa_etaria", id_serie_mensal=int(serie))
        rows += ctx.loader.upsert("preco_faixa_etaria", df)
    return rows


def _handle_rede_credenciada(ctx: Context, ds_cfg: dict) -> int:
    """~1.5 GB, um CSV por UF. Agrega cada UF em memória (chaves distintas,
    não linhas brutas) e grava; município pertence a uma única UF, então as
    chaves não se repetem entre membros do ZIP. Só o snapshot do mesmo mês é
    substituído: os meses anteriores ficam (histórico mensal do DER)."""
    rows = 0
    limpas: set[int] = set()
    with ctx.sources(ds_cfg, T.REDE_CRED_COLS) as sources:
        for name, chunks in sources:
            agg = T.Aggregator(list(T.REDE_KEYS), list(T.REDE_QTD_COLS))
            for chunk in chunks:
                agg.add(T.transform_rede_credenciada_chunk(chunk))
            df = T.finalize_rede_credenciada(agg.result())
            if df.empty:
                continue
            for comp in df["competencia"].unique():
                serie = ctx.serie_from_yyyymm(int(comp))
                if serie not in limpas:
                    ctx.loader.delete_where("rede_credenciada_sintetica", fonte_estimativa=T.REDE_FONTE,
                                            id_serie_mensal=serie)
                    limpas.add(serie)
            df = ctx.loader.keep_known(df, "id_produto_saude", "produto_saude", label=name)
            ctx.loader.ensure_municipios(df["id_municipio"])
            n = ctx.loader.upsert("rede_credenciada_sintetica", df)
            logger.info("[REDE] %s: %d linhas sintéticas (séries %s)", name, n, sorted(limpas))
            rows += n
    return rows


def _handle_market_share(ctx: Context, ds_cfg: dict) -> int:
    """Um sib_ativo_[UF].zip por UF de residência -> contagem por (operadora, município)."""
    rows = 0
    limpas: set[int] = set()  # séries cujo snapshot já foi apagado nesta execução
    with ctx.sources(ds_cfg, T.SIB_COLS) as sources:
        for name, chunks in sources:
            agg = T.Aggregator(["competencia", "id_operadora", "id_municipio"], ["total_beneficiarios"])
            for chunk in chunks:
                agg.add(T.transform_sib_chunk(chunk))
            df = T.finalize_market_share(agg.result())
            if df.empty:
                continue
            for comp in df["competencia"].unique():
                serie = ctx.serie_from_yyyymm(int(comp))
                if serie not in limpas:
                    ctx.loader.delete_where("market_share", id_serie_mensal=serie)
                    limpas.add(serie)
            df = ctx.loader.keep_known(df, "id_operadora", "operadora", label=name)
            ctx.loader.ensure_municipios(df["id_municipio"])
            n = ctx.loader.upsert("market_share", df)
            logger.info("[MARKET_SHARE] %s: %d linhas", name, n)
            rows += n
    return rows


def _handle_taxa_cobertura(ctx: Context, ds_cfg: dict) -> int:
    agg = T.Aggregator(["ano", "id_municipio"], ["benef", "populacao"])
    with ctx.sources(ds_cfg, T.TAXA_COBERTURA_COLS) as sources:
        for _, chunks in sources:
            for chunk in chunks:
                agg.add(T.transform_taxa_cobertura_chunk(chunk))
    agg_df = agg.result()
    pop_ibge = None
    if ds_cfg.get("url_populacao_ibge") and not agg_df.empty:
        partes = []
        for ano in sorted(agg_df["ano"].dropna().astype(int).unique()):
            try:
                partes.append(T.transform_sidra_populacao(_get_json(ctx, ds_cfg["url_populacao_ibge"].format(ano=ano))))
            except Exception as exc:  # ano ainda não publicado pelo IBGE
                logger.warning("População IBGE %d indisponível: %s", ano, exc)
        pop_ibge = pd.concat(partes, ignore_index=True) if partes else None
    df = T.finalize_perfil_saude_publica(agg_df, pop_ibge)
    if df.empty:
        return 0
    ctx.loader.ensure_series_anuais(df["id_serie_anual"])
    ctx.loader.ensure_municipios(df["id_municipio"])
    return ctx.loader.upsert("perfil_saude_publica", df)


Handler = Callable[[Context, dict], int]

# Ordem = ordem de execução (dimensões antes de fatos, por causa das FKs).
# Datasets do config sem handler aqui não têm tabela de destino no DDL.
DATASET_HANDLERS: dict[str, Handler] = {
    "geografia":                       _handle_geografia,
    "ibge_localidades":                _handle_ibge_localidades,
    "operadora_cancelada":             _handle_operadora,
    "operadora_ativa":                 _handle_operadora,
    "produto_saude":                   _handle_produto_saude,
    "abrangencia_plano":               _handle_abrangencia,
    "valor_comercial_medio_municipio": _handle_preco_faixa_etaria,
    "painel_precificacao":             _handle_painel_precificacao,
    "rede_credenciada_hospitalar":     _handle_rede_credenciada,
    "beneficiarios_operadora":         _handle_market_share,
    "taxa_cobertura":                  _handle_taxa_cobertura,
}


# ---------------------------------------------------------------------------
# PIPELINE PRINCIPAL
# ---------------------------------------------------------------------------

def _run_dataset(ctx: Context, ds_cfg: dict) -> int:
    ds_id = ds_cfg["dataset"]
    logger.info("--- Dataset %s ---", ds_id)
    started = datetime.now(timezone.utc)
    rows = DATASET_HANDLERS[ds_id](ctx, ds_cfg)
    logger.info("Dataset %s: OK (%d linhas, %.1fs)", ds_id, rows,
                (datetime.now(timezone.utc) - started).total_seconds())
    return rows


def _run_dataset_with_crc_retry(
    ctx: Context,
    ds_cfg: dict,
    new_context: Callable[[], Context],
) -> tuple[int, Context]:
    """Retries a ZIP CRC failure once with a fresh download and loader cache.

    Each attempt is isolated by a savepoint so a failed archive cannot leave
    staged rows behind in the competence-wide transaction.
    """
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            with ctx.loader.conn.begin_nested():
                rows = _run_dataset(ctx, ds_cfg)
            return rows, ctx
        except Exception as exc:
            if not is_bad_zip_crc_error(exc) or attempt == max_attempts:
                raise
            logger.warning(
                "CRC inválido no ZIP do dataset %s; refazendo download "
                "(tentativa %d/%d)",
                ds_cfg["dataset"],
                attempt + 1,
                max_attempts,
            )
            ctx = new_context()
    raise RuntimeError("Retries do dataset encerrados sem resultado")


def _new_context(conn: Connection, http: requests.Session, base_url: str, ano: int, mes: int) -> Context:
    return Context(Loader(conn), http, base_url, ano, mes)


def run_pipeline(
    competencia_ano: int,
    competencia_mes: int,
    datasets: Optional[list[str]] = None,
    transacao_unica: bool = True,
) -> dict:
    """Executa a pipeline ANS para uma competência mensal.

    transacao_unica=True (padrão): tudo em um BEGIN...COMMIT; qualquer erro
    faz ROLLBACK da competência inteira (status ERROR).
    transacao_unica=False: uma transação por dataset (status PARTIAL em falhas).
    """
    start = datetime.now(timezone.utc)
    logger.info("=== PIPELINE ANS — Competência %04d-%02d ===", competencia_ano, competencia_mes)
    summary: dict = {
        "competencia": f"{competencia_ano:04d}-{competencia_mes:02d}",
        "status": "ERROR",
        "datasets": {},
        "rows_total": 0,
        "start": start.isoformat(),
    }

    try:
        ans = _get_ans_entry(_load_config())
        if not ans:
            raise RuntimeError("Fonte 'ans_pda' não encontrada no config.json.")
    except Exception as exc:
        logger.exception("Config inválido")
        summary["error"] = str(exc)
        return summary

    todos = [d for f in _load_config()["projeto_integrador"]["fontes_de_dados"] for d in f.get("datasets", [])]
    plan = [d for d in todos if not datasets or d["dataset"] in datasets]
    for d in plan:
        if d["dataset"] not in DATASET_HANDLERS:
            summary["datasets"][d["dataset"]] = {"status": "SKIPPED", "rows": 0,
                                               "motivo": d.get("motivo", "sem handler na pipeline")}
    order = list(DATASET_HANDLERS)
    plan = sorted((d for d in plan if d["dataset"] in DATASET_HANDLERS), key=lambda d: order.index(d["dataset"]))

    engine = None
    try:
        engine = create_db_engine()
        if not ping(engine):
            raise RuntimeError("Banco inacessível (ping falhou).")
        validate_schema(engine)

        with build_session() as http:
            if transacao_unica:
                _run_single_transaction(engine, http, ans["url_base"], competencia_ano, competencia_mes, plan, summary)
            else:
                _run_per_dataset(engine, http, ans["url_base"], competencia_ano, competencia_mes, plan, summary)
    except Exception as exc:
        logger.exception("Pipeline abortada: %s", exc)
        summary["status"] = "ERROR"
        summary["error"] = str(exc)
    finally:
        if engine is not None:
            engine.dispose()

    end = datetime.now(timezone.utc)
    summary["rows_total"] = sum(d.get("rows", 0) for d in summary["datasets"].values() if d["status"] == "OK")
    summary["end"] = end.isoformat()
    summary["duration_seconds"] = round((end - start).total_seconds(), 1)
    logger.info("=== PIPELINE CONCLUÍDA — %s | %d linhas | %.1fs ===",
                summary["status"], summary["rows_total"], summary["duration_seconds"])
    return summary


def _run_single_transaction(engine, http, base_url, ano, mes, plan, summary) -> None:
    current = None
    try:
        with engine.begin() as conn:
            ctx = _new_context(conn, http, base_url, ano, mes)
            for ds_cfg in plan:
                current = ds_cfg["dataset"]
                rows, ctx = _run_dataset_with_crc_retry(
                    ctx,
                    ds_cfg,
                    lambda: _new_context(conn, http, base_url, ano, mes),
                )
                summary["datasets"][current] = {"status": "OK", "rows": rows}
            current = None
        summary["status"] = "OK"
    except Exception as exc:
        for result in summary["datasets"].values():
            if result["status"] == "OK":
                result["status"] = "ROLLBACK"
        if current:
            summary["datasets"][current] = {"status": "ERROR", "rows": 0, "error": str(exc)}
        logger.error("ROLLBACK da competência inteira (falha em %s)", current)
        raise


def _run_per_dataset(engine, http, base_url, ano, mes, plan, summary) -> None:
    all_ok = True
    for ds_cfg in plan:
        ds_id = ds_cfg["dataset"]
        try:
            with engine.begin() as conn:
                ctx = _new_context(conn, http, base_url, ano, mes)
                rows, _ = _run_dataset_with_crc_retry(
                    ctx,
                    ds_cfg,
                    lambda: _new_context(conn, http, base_url, ano, mes),
                )
            summary["datasets"][ds_id] = {"status": "OK", "rows": rows}
        except Exception as exc:
            logger.exception("Dataset %s: FALHOU (rollback do dataset)", ds_id)
            summary["datasets"][ds_id] = {"status": "ERROR", "rows": 0, "error": str(exc)}
            all_ok = False
    summary["status"] = "OK" if all_ok else "PARTIAL"


def main() -> None:
    """Uso:
        python -m src.orchestrator                      # competência atual
        python -m src.orchestrator 2026 9               # competência 2026-09
        python -m src.orchestrator 2026 9 operadora_ativa produto_saude
        python -m src.orchestrator 2026 9 --transacao-por-dataset
    """
    now = datetime.now(timezone.utc)
    parser = argparse.ArgumentParser(description="Pipeline ANS — Projeto Integrador")
    parser.add_argument("ano", nargs="?", type=int, default=now.year)
    parser.add_argument("mes", nargs="?", type=int, default=now.month)
    parser.add_argument("datasets", nargs="*", help="IDs dos datasets (padrão: todos)")
    parser.add_argument("--transacao-por-dataset", action="store_true",
                        help="commit por dataset em vez de uma transação por competência")
    parser.add_argument("-v", "--verbose", action="store_true", help="log em nível DEBUG")
    args = parser.parse_args()
    if not 1 <= args.mes <= 12:
        parser.error("mes deve estar entre 1 e 12")

    _setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    result = run_pipeline(args.ano, args.mes, args.datasets or None, not args.transacao_por_dataset)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if result["status"] in ("OK", "PARTIAL") else 1)


if __name__ == "__main__":
    main()
