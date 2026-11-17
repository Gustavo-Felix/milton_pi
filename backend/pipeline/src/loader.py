"""
FASE 07/08 — Carga no PostgreSQL, transação e idempotência.

- `Loader` envolve UMA conexão já dentro de transação (`engine.begin()`); o
  orquestrador decide o escopo (competência inteira ou por dataset) e o
  ROLLBACK acontece automaticamente se qualquer etapa levantar exceção.
- Upsert `INSERT ... ON CONFLICT` em lotes de 1000 linhas (execute_values).
- Strings são truncadas ao tamanho do VARCHAR do DDL e só as colunas
  presentes no DataFrame são atualizadas no conflito.
- FKs: linhas que referenciam plano/operadora inexistente são descartadas
  (com contagem no log); municípios ausentes são criados silenciosamente
  pelo código IBGE (instrucoes.md regra 4).
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Literal

import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy import MetaData, String, Table, text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

# registros por INSERT (regra do orquestrador: 1000); banco remoto: mais por lote = menos idas e voltas
BATCH_SIZE = int(os.getenv("PIPELINE_BATCH_SIZE", "1000"))

ConflictMode = Literal["update", "ignore"]


def _to_rows(df: pd.DataFrame) -> list[tuple]:
    """DataFrame -> tuplas com tipos nativos Python e None no lugar de NA/NaN/NaT."""
    return list(df.astype(object).where(df.notna(), None).itertuples(index=False, name=None))


class Loader:
    """Upserts sobre uma conexão transacional, com cache de metadados e de FKs."""

    def __init__(self, conn: Connection):
        self.conn = conn
        self._meta = MetaData()
        self._known: dict[str, set[int]] = {}
        self._lookup: dict[str, pd.Index] = {}  # hashtable reaproveitada entre chunks

    # -- metadados -----------------------------------------------------------

    def table(self, name: str) -> Table:
        if name not in self._meta.tables:
            self._meta.reflect(bind=self.conn, only=[name])
        return self._meta.tables[name]

    def _prepare(self, table: Table, df: pd.DataFrame) -> pd.DataFrame:
        cols = [c for c in df.columns if c in table.c]
        df = df[cols]
        for name in cols:
            col_type = table.c[name].type
            if isinstance(col_type, String) and col_type.length:
                s = df[name].astype("string")
                df = df.assign(**{name: s.str.slice(0, col_type.length)})
        return df

    # -- escrita -------------------------------------------------------------

    def upsert(
        self,
        table_name: str,
        df: pd.DataFrame,
        conflict_cols: Iterable[str] = ("id",),
        mode: ConflictMode = "update",
    ) -> int:
        """INSERT ... ON CONFLICT em lotes de BATCH_SIZE. Retorna linhas enviadas."""
        if df is None or df.empty:
            return 0
        table = self.table(table_name)
        conflict_cols = list(conflict_cols)
        # Dentro de um mesmo INSERT a chave não pode repetir (ON CONFLICT
        # não pode afetar a mesma linha duas vezes).
        df = self._prepare(table, df).drop_duplicates(conflict_cols, keep="last")

        sql = self._upsert_sql(table, list(df.columns), conflict_cols, mode)
        # execute_values: um único INSERT multi-linha por lote (1 round-trip),
        # na mesma conexão/transação do SQLAlchemy.
        cursor = self.conn.connection.dbapi_connection.cursor()
        try:
            for start in range(0, len(df), BATCH_SIZE):
                execute_values(cursor, sql, _to_rows(df.iloc[start:start + BATCH_SIZE]), page_size=BATCH_SIZE)
        finally:
            cursor.close()
        self._remember(table_name, df)
        return len(df)

    def _upsert_sql(self, table: Table, cols: list[str], conflict_cols: list[str], mode: ConflictMode) -> str:
        q = self.conn.dialect.identifier_preparer.quote
        conflict = ", ".join(map(q, conflict_cols))
        changed = [q(c) for c in cols if c not in conflict_cols]
        updates = [f"{c} = EXCLUDED.{c}" for c in changed]
        if updates and "updated_at" in table.c and "updated_at" not in cols:
            updates.append(f"{q('updated_at')} = CURRENT_TIMESTAMP")
        if mode == "update" and updates:
            # Sem mudança, não reescreve a linha (nem WAL, nem linha morta, nem updated_at novo).
            action = (f"DO UPDATE SET {', '.join(updates)} WHERE ({', '.join(f'cur.{c}' for c in changed)}) "
                      f"IS DISTINCT FROM ({', '.join(f'EXCLUDED.{c}' for c in changed)})")
        else:
            action = "DO NOTHING"
        return (
            f"INSERT INTO {q(table.name)} AS cur ({', '.join(map(q, cols))}) VALUES %s "
            f"ON CONFLICT ({conflict}) {action}"
        )

    def delete_where(self, table_name: str, **filters) -> int:
        """DELETE parametrizado (usado para substituir o snapshot de uma fonte)."""
        table = self.table(table_name)
        stmt = table.delete()
        for col, value in filters.items():
            stmt = stmt.where(table.c[col] == value)
        return self.conn.execute(stmt).rowcount

    # -- integridade referencial ---------------------------------------------

    def _ids(self, table_name: str) -> set[int]:
        if table_name not in self._known:
            rows = self.conn.execute(text(f"SELECT id FROM {self.table(table_name).name}"))
            self._known[table_name] = {r[0] for r in rows}
        return self._known[table_name]

    def _remember(self, table_name: str, df: pd.DataFrame) -> None:
        if table_name in self._known and "id" in df.columns:
            self._known[table_name].update(int(v) for v in df["id"].dropna())
            self._lookup.pop(table_name, None)

    def keep_known(self, df: pd.DataFrame, col: str, table_name: str, label: str = "") -> pd.DataFrame:
        """Descarta linhas cuja FK `col` não existe em `table_name`."""
        if df.empty:
            return df
        if table_name not in self._lookup:
            self._lookup[table_name] = pd.Index(sorted(self._ids(table_name)), dtype="int64")
        mask = self._lookup[table_name].get_indexer(df[col].astype("int64")) >= 0
        dropped = int(len(mask) - mask.sum())
        if dropped:
            logger.debug("%s: %d linhas sem %s cadastrado descartadas", label or table_name, dropped, table_name)
        return df[mask]

    def ensure_municipios(self, codes: pd.Series) -> None:
        """Cria municípios ausentes apenas com o código IBGE (instrucoes.md regra 4)."""
        known = self._ids("municipio")
        missing = sorted({int(c) for c in codes.dropna().unique()} - known)
        if not missing:
            return
        df = pd.DataFrame({"id": missing})
        self.upsert("municipio", df, mode="ignore")
        logger.info("%d municípios criados pelo código IBGE", len(missing))

    def ensure_series_anuais(self, anos: Iterable[int]) -> None:
        df = pd.DataFrame({"id": sorted({int(a) for a in anos})})
        df["ano"] = df["id"]
        self.upsert("serie_temporal_anual", df, mode="ignore")


# ---------------------------------------------------------------------------
# Controle de Séries Temporais (instrucoes.md regra 1)
# ---------------------------------------------------------------------------

def get_or_create_serie_mensal(conn: Connection, ano: int, mes: int) -> int:
    """Retorna o id (YYYYMM) de serie_temporal_mensal, criando anual/mensal se preciso."""
    row = conn.execute(
        text("SELECT id FROM serie_temporal_mensal WHERE ano = :ano AND mes = :mes"),
        {"ano": ano, "mes": mes},
    ).fetchone()
    if row:
        return int(row[0])

    conn.execute(
        text("INSERT INTO serie_temporal_anual (id, ano) VALUES (:ano, :ano) ON CONFLICT (id) DO NOTHING"),
        {"ano": ano},
    )
    serie_id = ano * 100 + mes
    conn.execute(
        text(
            "INSERT INTO serie_temporal_mensal (id, id_serie_anual, ano, mes, competencia) "
            "VALUES (:id, :ano, :ano, :mes, :comp) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": serie_id, "ano": ano, "mes": mes, "comp": f"{ano:04d}-{mes:02d}"},
    )
    logger.info("Série temporal mensal criada: id=%d", serie_id)
    return serie_id
