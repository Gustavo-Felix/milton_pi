"""Aplica infra/init.sql no banco do DATABASE_URL (equivalente ao infra/db_setup.sh, sem precisar de psql/Docker).

Espera até ~15 min o banco aceitar conexões: com o liga/desliga do RDS (lambda.tf), a tarefa pode
começar enquanto o banco ainda está iniciando.
"""
import os
import re
import sys
import time
from pathlib import Path

import psycopg2

TENTATIVAS = 30
INTERVALO_S = 30

url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL não definida")

url = re.sub(r"^postgres(?:ql)?(?:\+psycopg2)?://", "postgresql://", url)
if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"

sql = (Path(__file__).resolve().parent / "init.sql").read_text(encoding="utf-8")

conn = None
for tentativa in range(1, TENTATIVAS + 1):
    try:
        conn = psycopg2.connect(url, connect_timeout=15)
        break
    except psycopg2.OperationalError as exc:
        print(f"Banco indisponível ({tentativa}/{TENTATIVAS}): {str(exc).strip()[:120]}", flush=True)
        time.sleep(INTERVALO_S)
if conn is None:
    sys.exit("Banco não ficou disponível a tempo")

try:
    with conn, conn.cursor() as cur:  # uma transação; commit ao sair do with
        cur.execute("SET statement_timeout = 0")
        cur.execute(sql)
    print("Schema aplicado.")
finally:
    conn.close()
