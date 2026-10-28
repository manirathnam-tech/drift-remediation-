data "aws_partition" "collector" {}

data "aws_caller_identity" "collector" {}

resource "aws_dynamodb_table" "collector_idempotency" {
  name         = "drift-thesis-collector-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"

  attribute {
    name = "event_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = local.common_tags
}

resource "aws_sns_topic" "drift_events" {
  name = "drift-thesis-events"

  tags = local.common_tags
}

resource "aws_sqs_queue" "collector_dlq" {
  name                      = "drift-thesis-collector-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = local.common_tags
}

resource "aws_security_group" "drift_collector_lambda" {
  name        = "${local.eks_cluster_name}-collector-lambda"
  description = "Private egress for drift collector to EKS API and AWS services"
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Lambda outbound connections constrained by IAM and VPC routing"
  }

  tags = merge(local.common_tags, { Name = "${local.eks_cluster_name}-collector-lambda" })
}

resource "aws_vpc_security_group_ingress_rule" "collector_to_eks_api" {
  security_group_id            = module.eks.cluster_security_group_id
  referenced_security_group_id = aws_security_group.drift_collector_lambda.id
  description                  = "Allow drift collector access to EKS API for the Argo CD service proxy"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  tags                         = local.common_tags
}

resource "aws_iam_role" "drift_collector" {
  name = "drift-thesis-collector-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.collector.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.collector.partition}:lambda:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:function:drift-thesis-collector"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "collector_basic_execution" {
  role       = aws_iam_role.drift_collector.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "collector_vpc_access" {
  role       = aws_iam_role.drift_collector.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "collector_runtime" {
  name = "drift-thesis-collector-runtime"
  role = aws_iam_role.drift_collector.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["eks:DescribeCluster"]
        Resource = "arn:${data.aws_partition.collector.partition}:eks:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:cluster/${local.eks_cluster_name}"
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          "arn:${data.aws_partition.collector.partition}:secretsmanager:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:secret:drift-thesis-argocd-webhook-*",
          "arn:${data.aws_partition.collector.partition}:secretsmanager:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:secret:drift-thesis-argocd-collector-api-token-*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
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
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "drift_collector" {
  name              = "/aws/lambda/drift-thesis-collector"
  retention_in_days = 14
  tags              = local.common_tags
}

data "archive_file" "drift_collector" {
  type        = "zip"
  source_dir  = "${path.module}/collector/src"
  output_path = "${path.module}/collector/collector.zip"
}

resource "aws_lambda_function" "drift_collector" {
  function_name    = "drift-thesis-collector"
  description      = "Authenticate Argo CD drift notifications, enrich DriftEvent v1, suppress duplicates and publish"
  role             = aws_iam_role.drift_collector.arn
  runtime          = "python3.12"
  handler          = "collector.handler"
  filename         = data.archive_file.drift_collector.output_path
  source_code_hash = data.archive_file.drift_collector.output_base64sha256
  timeout          = 30
  memory_size      = 256

  reserved_concurrent_executions = 2

  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.drift_collector_lambda.id]
  }

  environment {
    variables = {
      AWS_REGION_NAME         = "ap-southeast-2"
      EKS_CLUSTER_NAME        = module.eks.cluster_name
      EKS_CLUSTER_ENDPOINT    = module.eks.cluster_endpoint
      ARGOCD_APP_NAME         = "online-boutique"
      ARGOCD_NAMESPACE        = "argocd"
      ARGOCD_SERVICE_NAME     = "argocd-server"
      WEBHOOK_SECRET_ARN      = "arn:${data.aws_partition.collector.partition}:secretsmanager:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:secret:drift-thesis-argocd-webhook-*"
      ARGOCD_TOKEN_SECRET_ARN = "arn:${data.aws_partition.collector.partition}:secretsmanager:ap-southeast-2:${data.aws_caller_identity.collector.account_id}:secret:drift-thesis-argocd-collector-api-token-*"
      IDEMPOTENCY_TABLE       = aws_dynamodb_table.collector_idempotency.name
      EVENT_TOPIC_ARN         = aws_sns_topic.drift_events.arn
      FAILURE_QUEUE_URL       = aws_sqs_queue.collector_dlq.url
    }
  }

  depends_on = [
    module.eks,
    aws_cloudwatch_log_group.drift_collector,
    aws_iam_role_policy_attachment.collector_basic_execution,
    aws_iam_role_policy_attachment.collector_vpc_access,
    aws_iam_role_policy.collector_runtime
  ]

  tags = local.common_tags
}

resource "aws_apigatewayv2_api" "drift_collector" {
  name          = "drift-thesis-collector"
  protocol_type = "HTTP"

  tags = local.common_tags
}

resource "aws_apigatewayv2_integration" "drift_collector" {
  api_id                 = aws_apigatewayv2_api.drift_collector.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.drift_collector.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "drift_collector" {
  api_id    = aws_apigatewayv2_api.drift_collector.id
  route_key = "POST /events"
  target    = "integrations/${aws_apigatewayv2_integration.drift_collector.id}"
}

resource "aws_apigatewayv2_stage" "drift_collector" {
  api_id      = aws_apigatewayv2_api.drift_collector.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 5
    throttling_rate_limit  = 2
  }

  tags = local.common_tags
}

resource "aws_lambda_permission" "collector_from_apigateway" {
  statement_id  = "AllowApiGatewayInvokeCollector"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.drift_collector.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.drift_collector.execution_arn}/*/POST/events"
}

output "drift_collector_api_url" {
  description = "HTTPS endpoint receiving authenticated Argo CD drift notifications"
  value       = "${aws_apigatewayv2_api.drift_collector.api_endpoint}/events"
}

output "drift_collector_dlq_url" {
  description = "SQS URL holding authenticated collector events that failed enrichment or publishing"
  value       = aws_sqs_queue.collector_dlq.url
}

output "drift_collector_event_topic_arn" {
  description = "SNS topic publishing validated DriftEvent v1 messages"
  value       = aws_sns_topic.drift_events.arn
}
