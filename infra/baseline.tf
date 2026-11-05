variable "baseline_consumer_enabled" {
  description = "Enable SQS delivery to the deterministic baseline arm. Keep disabled until DRIFT-51 arm isolation is deployed."
  type        = bool
  default     = false
}

data "aws_partition" "baseline" {}

data "aws_caller_identity" "baseline" {}

data "aws_prefix_list" "baseline_dynamodb" {
  name = "com.amazonaws.ap-southeast-2.dynamodb"
}

resource "aws_sqs_queue" "baseline_dead_letter" {
  name                      = "drift-thesis-baseline-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = merge(local.common_tags, {
    Name     = "drift-thesis-baseline-dlq"
    Consumer = "baseline-dead-letter"
  })
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

  tags = merge(local.common_tags, {
    Name     = "drift-thesis-baseline-events"
    Consumer = "baseline"
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "baseline_dead_letter" {
  queue_url = aws_sqs_queue.baseline_dead_letter.url
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.baseline_events.arn]
  })
}

resource "aws_security_group" "baseline_lambda" {
  name        = "${local.eks_cluster_name}-baseline-lambda"
  description = "HTTPS egress from the baseline decision Lambda to the private EKS API"
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [module.vpc.vpc_cidr_block]
    description = "HTTPS to the EKS API and VPC endpoints"
  }

  egress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [data.aws_prefix_list.baseline_dynamodb.id]
    description     = "HTTPS to the DynamoDB gateway endpoint for audit writes"
  }

  tags = merge(local.common_tags, { Name = "${local.eks_cluster_name}-baseline-lambda" })
}

resource "aws_vpc_security_group_ingress_rule" "baseline_to_eks_api" {
  security_group_id            = module.eks.cluster_security_group_id
  referenced_security_group_id = aws_security_group.baseline_lambda.id
  description                  = "Allow baseline Lambda access to the Kubernetes API over HTTPS"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  tags                         = local.common_tags
}

resource "aws_iam_role" "baseline_lambda" {
  name = "drift-thesis-baseline-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.baseline.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.baseline.partition}:lambda:ap-southeast-2:${data.aws_caller_identity.baseline.account_id}:function:drift-thesis-baseline"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "baseline_lambda_vpc" {
  role       = aws_iam_role.baseline_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "baseline_lambda_runtime" {
  name = "drift-thesis-baseline-runtime"
  role = aws_iam_role.baseline_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
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

resource "aws_cloudwatch_log_group" "baseline_lambda" {
  name              = "/aws/lambda/drift-thesis-baseline"
  retention_in_days = 14
  tags              = local.common_tags
}

data "archive_file" "baseline_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/baseline/src"
  output_path = "${path.module}/baseline/baseline.zip"
}

resource "aws_lambda_function" "baseline" {
  function_name    = "drift-thesis-baseline"
  description      = "Classify drift with static rules and request a scoped Argo CD sync for the baseline arm."
  role             = aws_iam_role.baseline_lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.baseline_lambda.output_path
  source_code_hash = data.archive_file.baseline_lambda.output_base64sha256
  timeout          = 60
  memory_size      = 256

  reserved_concurrent_executions = 1

  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.baseline_lambda.id]
  }

  environment {
    variables = {
      AWS_REGION_NAME       = "ap-southeast-2"
      EKS_CLUSTER_NAME      = module.eks.cluster_name
      EKS_ENDPOINT          = module.eks.cluster_endpoint
      EKS_CA_DATA           = module.eks.cluster_certificate_authority_data
      TREATMENT_AUDIT_TABLE = aws_dynamodb_table.treatment_audit.name
    }
  }

  depends_on = [
    module.eks,
    aws_iam_role_policy_attachment.baseline_lambda_vpc,
    aws_iam_role_policy.baseline_lambda_runtime,
    aws_cloudwatch_log_group.baseline_lambda,
  ]

  tags = merge(local.common_tags, { Name = "drift-thesis-baseline" })
}

resource "aws_lambda_event_source_mapping" "baseline" {
  event_source_arn                   = aws_sqs_queue.baseline_events.arn
  function_name                      = aws_lambda_function.baseline.arn
  enabled                            = var.baseline_consumer_enabled
  batch_size                         = 1
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_batching_window_in_seconds = 0
}

output "baseline_queue_url" {
  description = "Isolated SQS queue for baseline-arm input; it is not connected to the SNS event fanout."
  value       = aws_sqs_queue.baseline_events.url
}

output "baseline_lambda_name" {
  description = "Deterministic rule-based baseline decision Lambda."
  value       = aws_lambda_function.baseline.function_name
}
