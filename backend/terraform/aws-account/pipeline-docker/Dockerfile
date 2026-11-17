# Copie este arquivo, o .dockerignore e o entrypoint.sh para backend/pipeline/ e o apply_schema.py
# para backend/pipeline/infra/ (veja o README). Build a partir de backend/pipeline.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.json ./
COPY src ./src
COPY infra/init.sql ./infra/init.sql
COPY infra/apply_schema.py ./infra/apply_schema.py
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
# Sem argumentos = competência atual, todos os datasets. Ex.: ["2026","11","operadora_ativa"]
CMD []
