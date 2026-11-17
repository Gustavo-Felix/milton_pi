#!/bin/sh
set -e
# 1) garante o schema (init.sql é idempotente)  2) roda a pipeline
python infra/apply_schema.py
exec python -m src.orchestrator "$@"
