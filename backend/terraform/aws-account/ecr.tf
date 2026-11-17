resource "aws_ecr_repository" "pipeline" {
  name                 = "${var.project}-pipeline"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # permite terraform destroy mesmo com imagens

  image_scanning_configuration {
    scan_on_push = false
  }
}

# Mantém só as 3 imagens mais recentes (armazenamento custa US$ 0,10/GB-mês).
resource "aws_ecr_lifecycle_policy" "pipeline" {
  repository = aws_ecr_repository.pipeline.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "manter 3 imagens"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}
