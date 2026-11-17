"""Liga e desliga o RDS do Milton PI para pagar só pelas horas em que a carga roda.

Ações (campo "action" do evento):
  start          -> liga o RDS se estiver "stopped" (disparado pelo EventBridge Scheduler antes da carga)
  stop_if_idle   -> desliga o RDS se: estiver "available", não houver tarefa ECS rodando
                    e o banco NÃO tiver a tag keep-awake=true.
                    Disparado (a) quando uma tarefa do cluster termina e (b) por uma rede de segurança diária,
                    porque o RDS parado volta a ligar sozinho após 7 dias.
"""
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

DB_ID = os.environ["DB_INSTANCE_ID"]
CLUSTER_ARN = os.environ["ECS_CLUSTER_ARN"]

rds = boto3.client("rds")
ecs = boto3.client("ecs")


def _db() -> dict:
    return rds.describe_db_instances(DBInstanceIdentifier=DB_ID)["DBInstances"][0]


def _keep_awake(db_arn: str) -> bool:
    tags = rds.list_tags_for_resource(ResourceName=db_arn)["TagList"]
    return any(t["Key"] == "keep-awake" and t["Value"].lower() == "true" for t in tags)


def _tasks_running() -> int:
    active = 0
    for status in ("RUNNING", "PENDING"):
        active += len(ecs.list_tasks(cluster=CLUSTER_ARN, desiredStatus=status)["taskArns"])
    return active


def _result(action: str, outcome: str, **extra) -> dict:
    out = {"action": action, "outcome": outcome, **extra}
    log.info(json.dumps(out))
    return out


def handler(event, context):
    action = (event or {}).get("action", "")
    db = _db()
    status = db["DBInstanceStatus"]

    if action == "start":
        if status != "stopped":
            return _result(action, "ignorado", status=status)
        try:
            rds.start_db_instance(DBInstanceIdentifier=DB_ID)
        except ClientError as exc:
            return _result(action, "erro", detalhe=str(exc))
        return _result(action, "ligando")

    if action == "stop_if_idle":
        if status != "available":
            return _result(action, "ignorado", status=status)
        if _keep_awake(db["DBInstanceArn"]):
            return _result(action, "ignorado", motivo="tag keep-awake=true")
        running = _tasks_running()
        if running:
            return _result(action, "ignorado", motivo="tarefa ECS em execucao", tarefas=running)
        try:
            rds.stop_db_instance(DBInstanceIdentifier=DB_ID)
        except ClientError as exc:
            return _result(action, "erro", detalhe=str(exc))
        return _result(action, "desligando")

    return _result(action or "?", "acao desconhecida")
