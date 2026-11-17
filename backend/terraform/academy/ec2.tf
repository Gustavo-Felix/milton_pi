data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"] # a sandbox só aceita AMIs da Amazon

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_instance" "pipeline" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.ec2.id]
  associate_public_ip_address = true # sem NAT: precisa de IP público para sair para a internet
  iam_instance_profile        = var.instance_profile_name

  # "shutdown -h now" dentro da instância = STOP (não terminate)
  instance_initiated_shutdown_behavior = "stop"

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = var.ebs_size_gb
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region          = var.aws_region
    bucket          = local.pipeline_bucket_name
    key             = aws_s3_object.pipeline.key
    database_url    = "postgresql://${aws_db_instance.this.username}:${random_password.db.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}"
    chunk_size      = var.pipeline_chunk_size
    batch_size      = var.pipeline_batch_size
    etl_args        = var.etl_args
    run_etl_on_boot = var.run_etl_on_boot
    stop_after_etl  = var.stop_after_etl
  })
  user_data_replace_on_change = true

  tags = {
    Name = "${var.project}-pipeline"
  }
}
