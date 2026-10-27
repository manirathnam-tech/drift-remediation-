data "aws_dynamodb_table" "injector_runtime" {
  name = "drift-thesis-experiment-ledger"
}

resource "aws_ecr_repository" "injector_runtime" {
  name                 = "drift-thesis-injector"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.common_tags, {
    Name = "drift-thesis-injector"
  })
}

resource "aws_iam_role" "injector_pod_identity" {
  name = "drift-thesis-injector-pod"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Condition = {
        StringEquals = {
          "aws:RequestTag/kubernetes-namespace"       = "online-boutique"
          "aws:RequestTag/kubernetes-service-account" = "drift-injector"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "injector_pod_ledger" {
  name = "write-drift-injection-ledger"
  role = aws_iam_role.injector_pod_identity.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
      Resource = data.aws_dynamodb_table.injector_runtime.arn
    }]
  })
}

resource "aws_eks_pod_identity_association" "injector_runtime" {
  cluster_name    = module.eks.cluster_name
  namespace       = "online-boutique"
  service_account = "drift-injector"
  role_arn        = aws_iam_role.injector_pod_identity.arn

  tags = local.common_tags

  depends_on = [module.eks]
}

output "drift_injector_repository_url" {
  description = "ECR repository for the in-cluster drift injector"
  value       = aws_ecr_repository.injector_runtime.repository_url
}

output "drift_injector_pod_role_arn" {
  description = "Least-privilege Pod Identity role for writing injector ledger records"
  value       = aws_iam_role.injector_pod_identity.arn
}
