#!/usr/bin/env bash
# Prepara o banco do DATABASE_URL (AWS RDS ou outro Postgres remoto) com o psql do container milton_postgres.
#   bash infra/db_setup.sh            -> cria o schema (infra/init.sql, idempotente)
#   bash infra/db_setup.sh --copiar   -> cria o schema e copia os dados do Postgres local
#                                        (o local precisa estar com todas as migrações aplicadas)
# DATABASE_URL vem do ambiente ou do .env.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash no Windows: não trocar /tmp do container por um caminho do Windows
cd "$(dirname "$0")/.."

env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\r\"'" || true; }

url=${DATABASE_URL:-$(env_value DATABASE_URL)}
url=${url/+psycopg2/}
[ -n "$url" ] || { echo "Defina DATABASE_URL no .env" >&2; exit 1; }
case "$url" in
  *sslmode=*|*@localhost[:/]*|*@127.0.0.1[:/]*) ;;
  *\?*) url="$url&sslmode=require" ;;
  *) url="$url?sslmode=require" ;;
esac

remote() { docker exec -i -e PGOPTIONS="-c client_min_messages=warning -c statement_timeout=0" milton_postgres psql "$url" -v ON_ERROR_STOP=1 -q "$@"; }

remote < infra/init.sql
echo "Schema aplicado."

if [ "${1:-}" = "--copiar" ]; then
  n=$(remote -At -c "SELECT count(*) FROM produto_saude")
  [ "$n" = 0 ] || { echo "O destino já tem dados (produto_saude=$n); a cópia é só para banco vazio" >&2; exit 1; }
  user=${POSTGRES_USER:-$(env_value POSTGRES_USER)}; db=${POSTGRES_DB:-$(env_value POSTGRES_DB)}
  dump=/tmp/milton_dados.sql  # dentro do container: dump inteiro antes de enviar, para um pg_dump que falhe não virar cópia parcial
  trap 'docker exec milton_postgres rm -f "$dump"' EXIT
  docker exec milton_postgres pg_dump -U "${user:-postgres}" -d "${db:-milton_pi}" --data-only --no-owner --no-privileges -f "$dump"
  remote --single-transaction -f "$dump" > /dev/null
  echo "Dados copiados."
  remote -At -c "SELECT 'produto_saude', count(*) FROM produto_saude UNION ALL SELECT 'rede_credenciada_sintetica', count(*) FROM rede_credenciada_sintetica"
fi
