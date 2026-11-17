data "aws_caller_identity" "current" {}

locals {
  # The Academy LabRole explicitly denies GetBucketObjectLockConfiguration,
  # which aws_s3_bucket calls during refresh. The bucket is pre-created by AWS CLI.
  pipeline_bucket_name = "${var.project}-code-${data.aws_caller_identity.current.account_id}"
}

# Código da pipeline (sem .env, .venv, logs) empacotado e enviado ao S3; o EC2 baixa de lá.
data "archive_file" "pipeline" {
  type        = "zip"
  source_dir  = "${path.module}/${var.pipeline_dir}"
  output_path = "${path.module}/.build/pipeline.zip"
  excludes    = [".env", ".venv", "logs", "__pycache__", ".pytest_cache"]
}

resource "aws_s3_object" "pipeline" {
  bucket = local.pipeline_bucket_name
  key    = "pipeline.zip"
  source = data.archive_file.pipeline.output_path
  etag   = data.archive_file.pipeline.output_md5
}
