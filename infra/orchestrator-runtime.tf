variable "treatment_worker_desired_count" {
  description = "Legacy SQS polling worker count. Keep at zero; the Step Functions pipe starts one task per event."
  type        = number
  default     = 0

  validation {
    condition     = var.treatment_worker_desired_count == 0
    error_message = "Keep the legacy SQS polling service at zero; use the stopped-by-default workflow pipe instead."
  }
}

variable "treatment_worker_image_tag" {
  description = "Immutable image tag published to the treatment worker ECR repository."
  type        = string
  default     = "bootstrap"

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.treatment_worker_image_tag))
    error_message = "Provide a valid ECR image tag."
  }
}

resource "aws_ecr_repository" "treatment_worker" {
  force_delete         = true
  name                 = "drift-thesis-treatment-worker"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-worker"
  })
}

resource "aws_ecs_cluster" "treatment" {
  name = "drift-thesis-treatment"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment"
  })
}

resource "aws_cloudwatch_log_group" "treatment_worker" {
  name              = "/aws/ecs/drift-thesis-treatment-worker"
  retention_in_days = 14

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-worker"
  })
}

resource "aws_iam_role" "treatment_execution" {
  name = "drift-thesis-treatment-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "treatment_execution" {
  name = "drift-thesis-treatment-execution-runtime"
  role = aws_iam_role.treatment_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = aws_ecr_repository.treatment_worker.arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.treatment_worker.arn}:*"
      },
    ]
  })
}

resource "aws_iam_role" "treatment_task" {
  name = "drift-thesis-treatment-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "treatment_task" {
  name = "drift-thesis-treatment-runtime"
  role = aws_iam_role.treatment_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect = "Allow"
          Action = [
            "sqs:ReceiveMessage",
            "sqs:DeleteMessage",
          ]
          Resource = aws_sqs_queue.drift_event_consumer["treatment"].arn
        },
        {
          Effect   = "Allow"
          Action   = ["s3:GetObject"]
          Resource = "${aws_s3_bucket.golden_manifests.arn}/golden/*"
        },
        {
          Effect   = "Allow"
          Action   = ["s3:GetObject"]
          Resource = "${aws_s3_bucket.golden_manifests.arn}/workflow-runs/inputs/*"
        },
        {
          Effect   = "Allow"
          Action   = ["s3:GetObject", "s3:PutObject"]
          Resource = "${aws_s3_bucket.golden_manifests.arn}/workflow-runs/state/*"
        },
        {
          Effect   = "Allow"
          Action   = ["dynamodb:PutItem"]
          Resource = aws_dynamodb_table.treatment_audit.arn
        },
        {
          Effect   = "Allow"
          Action   = ["bedrock:Retrieve"]
          Resource = aws_bedrockagent_knowledge_base.treatment.arn
        },
        {
          Effect   = "Allow"
          Action   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
          Resource = "*"
        },
      ],
      [
        {
          Effect   = "Allow"
          Action   = ["bedrock:InvokeModel"]
          Resource = "arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-lite-v1:0"
        }
      ]
    )
  })
}

resource "aws_security_group" "treatment_worker" {
  name        = "drift-thesis-treatment-worker"
  description = "Outbound HTTPS for the isolated treatment worker."
  vpc_id      = module.vpc.vpc_id

  egress {
    description = "Outbound HTTPS to public IPv4 destinations"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-worker"
  })
}

resource "aws_ecs_task_definition" "treatment_worker" {
  family                   = "drift-thesis-treatment-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.treatment_execution.arn
  task_role_arn            = aws_iam_role.treatment_task.arn

  container_definitions = jsonencode([{
    name      = "treatment-worker"
    image     = "${aws_ecr_repository.treatment_worker.repository_url}:${var.treatment_worker_image_tag}"
    essential = true
    linuxParameters = {
      initProcessEnabled = true
    }
    environment = [
      { name = "AWS_REGION_NAME", value = "ap-southeast-2" },
      { name = "TREATMENT_QUEUE_URL", value = aws_sqs_queue.drift_event_consumer["treatment"].url },
      { name = "TREATMENT_AUDIT_TABLE", value = aws_dynamodb_table.treatment_audit.name },
      { name = "GOLDEN_MANIFESTS_BUCKET", value = aws_s3_bucket.golden_manifests.bucket },
      { name = "WORKFLOW_STATE_BUCKET", value = aws_s3_bucket.golden_manifests.bucket },
      { name = "BEDROCK_MODEL_ID", value = "amazon.nova-lite-v1:0" },
      { name = "TREATMENT_KNOWLEDGE_BASE_ID", value = aws_bedrockagent_knowledge_base.treatment.id },
      { name = "LOG_LEVEL", value = "INFO" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.treatment_worker.name
        "awslogs-region"        = "ap-southeast-2"
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])

  depends_on = [aws_iam_role_policy.treatment_execution]

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-worker"
  })
}

resource "aws_ecs_service" "treatment_worker" {
  name            = "drift-thesis-treatment-worker"
  cluster         = aws_ecs_cluster.treatment.id
  task_definition = aws_ecs_task_definition.treatment_worker.arn
  desired_count   = var.treatment_worker_desired_count
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = module.vpc.public_subnets
    security_groups  = [aws_security_group.treatment_worker.id]
    assign_public_ip = true
  }

  depends_on = [
    aws_iam_role_policy.treatment_task,
    aws_cloudwatch_log_group.treatment_worker,
    aws_bedrockagent_data_source.treatment,
  ]

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-worker"
  })
}

output "treatment_worker_repository_url" {
  description = "ECR repository for the treatment worker image."
  value       = aws_ecr_repository.treatment_worker.repository_url
}

output "treatment_worker_cluster_name" {
  description = "ECS cluster hosting the opt-in treatment worker."
  value       = aws_ecs_cluster.treatment.name
}

output "treatment_worker_desired_count" {
  description = "Legacy queue polling count; constrained to zero while treatment uses Step Functions."
  value       = aws_ecs_service.treatment_worker.desired_count
}
