variable "injector_schedule_enabled" {
  description = "Enable the optional EventBridge Scheduler trigger. Keep disabled during ordinary development."
  type        = bool
  default     = false
}

variable "injector_schedule_expression" {
  description = "EventBridge Scheduler expression, for example rate(1 day)."
  type        = string
  default     = "rate(1 day)"
}

variable "injector_schedule_instance_id" {
  description = "Corpus record ID passed by Scheduler when the optional schedule is enabled."
  type        = string
  default     = ""
}

data "aws_caller_identity" "injector" {}

data "aws_partition" "injector" {}

data "aws_dynamodb_table" "experiment_ledger" {
  name = "drift-thesis-experiment-ledger"
}

resource "aws_security_group" "drift_injector_lambda" {
  name        = "${local.eks_cluster_name}-injector-lambda"
  description = "Egress for drift injector Lambda to the private EKS API and DynamoDB endpoint"
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Lambda outbound connections; destinations are constrained by VPC routing and IAM"
  }

  tags = merge(local.common_tags, { Name = "${local.eks_cluster_name}-injector-lambda" })
}

resource "aws_vpc_security_group_ingress_rule" "injector_to_eks_api" {
  security_group_id            = module.eks.cluster_security_group_id
  referenced_security_group_id = aws_security_group.drift_injector_lambda.id
  description                  = "Allow the drift injector Lambda to reach the Kubernetes API over HTTPS"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  tags                         = local.common_tags
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.ap-southeast-2.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids

  tags = merge(local.common_tags, { Name = "drift-thesis-dynamodb" })
}

resource "aws_iam_role" "drift_injector" {
  name = "drift-thesis-injector-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.injector.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.injector.partition}:lambda:ap-southeast-2:${data.aws_caller_identity.injector.account_id}:function:drift-thesis-injector"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "drift_injector_vpc" {
  role       = aws_iam_role.drift_injector.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "drift_injector_ledger" {
  name = "write-drift-injection-ledger"
  role = aws_iam_role.drift_injector.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem"]
      Resource = data.aws_dynamodb_table.experiment_ledger.arn
    }]
  })
}

resource "aws_cloudwatch_log_group" "drift_injector" {
  name              = "/aws/lambda/drift-thesis-injector"
  retention_in_days = 14
  tags              = local.common_tags
}

data "archive_file" "drift_injector" {
  type        = "zip"
  source_dir  = "${path.module}/injector/build/function"
  output_path = "${path.module}/injector/build/injector.zip"
}

resource "aws_lambda_layer_version" "kubernetes_client" {
  filename            = "${path.module}/injector/build/kubernetes-client-layer.zip"
  layer_name          = "drift-thesis-kubernetes-client"
  compatible_runtimes = ["python3.12"]
  source_code_hash    = filebase64sha256("${path.module}/injector/build/kubernetes-client-layer.zip")
  description         = "Pinned Kubernetes Python client dependency layer for the drift injector"
}

resource "aws_lambda_function" "drift_injector" {
  function_name    = "drift-thesis-injector"
  description      = "Apply one approved synthetic drift corpus JSON Patch to an allowlisted Online Boutique object"
  role             = aws_iam_role.drift_injector.arn
  runtime          = "python3.12"
  handler          = "injector.handler"
  filename         = data.archive_file.drift_injector.output_path
  source_code_hash = data.archive_file.drift_injector.output_base64sha256
  timeout          = 90
  memory_size      = 256
  layers           = [aws_lambda_layer_version.kubernetes_client.arn]

  reserved_concurrent_executions = 1

  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.drift_injector_lambda.id]
  }

  environment {
    variables = {
      EKS_CLUSTER_NAME = module.eks.cluster_name
      EKS_ENDPOINT     = module.eks.cluster_endpoint
      EKS_CA_DATA      = module.eks.cluster_certificate_authority_data
      AWS_REGION_NAME  = "ap-southeast-2"
      LEDGER_TABLE     = data.aws_dynamodb_table.experiment_ledger.name
      CORPUS_PATH      = "/var/task/corpus.jsonl"
    }
  }

  depends_on = [
    module.eks,
    aws_cloudwatch_log_group.drift_injector,
    aws_iam_role_policy_attachment.drift_injector_vpc,
    aws_iam_role_policy.drift_injector_ledger
  ]

  tags = local.common_tags
}

resource "aws_iam_role" "injector_scheduler" {
  name = "drift-thesis-injector-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.injector.account_id
        }
        ArnEquals = {
          "aws:SourceArn" = "arn:${data.aws_partition.injector.partition}:scheduler:ap-southeast-2:${data.aws_caller_identity.injector.account_id}:schedule/default/drift-thesis-injector"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "injector_scheduler" {
  name = "invoke-drift-injector"
  role = aws_iam_role.injector_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.drift_injector.arn
    }]
  })
}

resource "aws_scheduler_schedule" "drift_injector" {
  count = var.injector_schedule_enabled ? 1 : 0

  name                         = "drift-thesis-injector"
  description                  = "Optional controlled test trigger for one corpus instance"
  schedule_expression          = var.injector_schedule_expression
  schedule_expression_timezone = "UTC"
  state                        = "ENABLED"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_lambda_function.drift_injector.arn
    role_arn = aws_iam_role.injector_scheduler.arn
    input = jsonencode({
      instance_id = var.injector_schedule_instance_id
      trigger     = "eventbridge-scheduler"
    })
  }

  depends_on = [aws_iam_role_policy.injector_scheduler]

  lifecycle {
    precondition {
      condition     = !var.injector_schedule_enabled || length(trimspace(var.injector_schedule_instance_id)) > 0
      error_message = "Set injector_schedule_instance_id to a corpus ID before enabling the schedule."
    }
  }
}

output "drift_injector_lambda_name" {
  description = "Name of the manually invokable drift injector Lambda"
  value       = aws_lambda_function.drift_injector.function_name
}

output "drift_injector_ledger_table" {
  description = "DynamoDB table that records successful T_inject timestamps"
  value       = data.aws_dynamodb_table.experiment_ledger.name
}

output "drift_injector_role_arn" {
  description = "Lambda role ARN mapped to the drift-injector Kubernetes group"
  value       = aws_iam_role.drift_injector.arn
}
