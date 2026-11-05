resource "aws_sqs_queue" "baseline_dead_letter" {
  name                      = "drift-thesis-baseline-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = merge(local.common_tags, { Name = "drift-thesis-baseline-dlq", Consumer = "baseline-dead-letter" })
}

resource "aws_sqs_queue" "baseline_events" {
  name                       = "drift-thesis-baseline-events"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 360
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.baseline_dead_letter.arn
    maxReceiveCount     = 3
  })
  tags = merge(local.common_tags, { Name = "drift-thesis-baseline-events", Consumer = "baseline" })
}

resource "aws_sqs_queue_redrive_allow_policy" "baseline_dead_letter" {
  queue_url = aws_sqs_queue.baseline_dead_letter.url
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.baseline_events.arn]
  })
}

resource "aws_ecr_repository" "baseline_runtime" {
  name                 = "drift-thesis-baseline"
  image_tag_mutability = "IMMUTABLE"
  encryption_configuration { encryption_type = "AES256" }
  tags = merge(local.common_tags, { Name = "drift-thesis-baseline" })
}

resource "aws_iam_role" "baseline_pod_identity" {
  name = "drift-thesis-baseline-pod"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Condition = { StringEquals = {
        "aws:RequestTag/kubernetes-namespace"       = "argocd"
        "aws:RequestTag/kubernetes-service-account" = "drift-baseline"
      } }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "baseline_pod_runtime" {
  name = "drift-thesis-baseline-pod-runtime"
  role = aws_iam_role.baseline_pod_identity.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.baseline_events.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.treatment_audit.arn
      },
    ]
  })
}

resource "aws_eks_pod_identity_association" "baseline_runtime" {
  cluster_name    = module.eks.cluster_name
  namespace       = "argocd"
  service_account = "drift-baseline"
  role_arn        = aws_iam_role.baseline_pod_identity.arn
  tags            = local.common_tags
  depends_on      = [module.eks]
}

output "baseline_queue_url" {
  description = "Isolated SQS queue for baseline-arm input; it is not connected to the shared SNS event fanout."
  value       = aws_sqs_queue.baseline_events.url
}

output "baseline_repository_url" {
  description = "ECR repository for the in-cluster deterministic baseline worker."
  value       = aws_ecr_repository.baseline_runtime.repository_url
}
