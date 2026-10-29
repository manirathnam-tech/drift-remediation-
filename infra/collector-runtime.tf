resource "aws_ecr_repository" "collector_runtime" {
  name                 = "drift-thesis-collector"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.common_tags, {
    Name = "drift-thesis-collector"
  })
}

resource "aws_iam_role" "collector_pod_identity" {
  name = "drift-thesis-collector-pod"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Condition = {
        StringEquals = {
          "aws:RequestTag/kubernetes-namespace"       = "argocd"
          "aws:RequestTag/kubernetes-service-account" = "drift-collector"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "collector_pod_runtime" {
  name = "drift-thesis-collector-pod-runtime"
  role = aws_iam_role.collector_pod_identity.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = [
          "arn:${data.aws_partition.collector.partition}:secretsmanager:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:secret:drift-thesis-argocd-webhook-*",
          "arn:${data.aws_partition.collector.partition}:secretsmanager:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:secret:drift-thesis-argocd-collector-api-token-*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.collector_idempotency.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.drift_events.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.collector_dlq.arn
      },
    ]
  })
}

resource "aws_eks_access_entry" "collector_pod" {
  cluster_name      = module.eks.cluster_name
  principal_arn     = aws_iam_role.collector_pod_identity.arn
  kubernetes_groups = ["drift-collector"]
  type              = "STANDARD"

  tags = local.common_tags
}

resource "aws_eks_pod_identity_association" "collector_runtime" {
  cluster_name    = module.eks.cluster_name
  namespace       = "argocd"
  service_account = "drift-collector"
  role_arn        = aws_iam_role.collector_pod_identity.arn

  tags = local.common_tags

  depends_on = [
    module.eks,
    aws_eks_access_entry.collector_pod,
  ]
}

output "drift_collector_repository_url" {
  description = "ECR repository for the in-cluster drift collector"
  value       = aws_ecr_repository.collector_runtime.repository_url
}

output "drift_collector_pod_role_arn" {
  description = "Pod Identity role for the in-cluster drift collector"
  value       = aws_iam_role.collector_pod_identity.arn
}
