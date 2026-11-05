variable "treatment_workflow_enabled" {
  description = "Run the EventBridge Pipe for the treatment workflow. Keep disabled until an approved test window."
  type        = bool
  default     = false
}

resource "aws_sqs_queue" "treatment_workflow_failures" {
  name                      = "drift-thesis-treatment-workflow-failures"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = merge(local.common_tags, {
    Name     = "drift-thesis-treatment-workflow-failures"
    Consumer = "treatment-failure-and-escalation-review"
  })
}

resource "aws_sqs_queue" "treatment_proposals" {
  name                      = "drift-thesis-treatment-proposals"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = merge(local.common_tags, {
    Name     = "drift-thesis-treatment-proposals"
    Consumer = "human-review"
  })
}

resource "aws_iam_role" "treatment_workflow" {
  name = "drift-thesis-treatment-workflow"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "treatment_workflow" {
  name = "run-single-treatment-task"
  role = aws_iam_role.treatment_workflow.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = aws_ecs_task_definition.treatment_worker.arn
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.treatment.arn
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks", "ecs:StopTask"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.treatment_workflow_failures.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.treatment_proposals.arn
      },
      {
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.treatment_execution.arn,
          aws_iam_role.treatment_task.arn,
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:aws:events:ap-southeast-2:${data.aws_caller_identity.treatment_knowledge.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"
      },
    ]
  })
}

resource "aws_sfn_state_machine" "treatment" {
  name     = "drift-thesis-treatment"
  role_arn = aws_iam_role.treatment_workflow.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Run separately checkpointed treatment stages using S3 references."
    StartAt = "Classify"
    States = {
      Classify = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.waitForTaskToken"
        Parameters = {
          Cluster        = aws_ecs_cluster.treatment.arn
          TaskDefinition = aws_ecs_task_definition.treatment_worker.arn
          LaunchType     = "FARGATE"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = module.vpc.public_subnets
              SecurityGroups = [aws_security_group.treatment_worker.id]
              AssignPublicIp = "ENABLED"
            }
          }
          Overrides = { ContainerOverrides = [{
            Name    = "treatment-worker"
            Command = ["--workflow-stage", "Classify"]
            Environment = [
              { Name = "TASK_TOKEN", "Value.$" = "$$.Task.Token" },
              { Name = "WORKFLOW_INPUT_KEY", "Value.$" = "$.input_key" },
              { Name = "WORKFLOW_STATE_KEY", "Value.$" = "$.state_key" },
              { Name = "WORKFLOW_INPUT_SHA256", "Value.$" = "$.input_sha256" },
            ]
          }] }
        }
        TimeoutSeconds = 900
        ResultPath     = "$.classification_result"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed", "States.Timeout"]
          IntervalSeconds = 5
          BackoffRate     = 2
          MaxAttempts     = 1
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "SendTreatmentFailure" }]
        Next  = "ClassificationChoice"
      }
      ClassificationChoice = {
        Type = "Choice"
        Choices = [{
          Variable     = "$.classification_result.status"
          StringEquals = "READY"
          Next         = "Synthesize"
        }]
        Default = "Escalate"
      }
      Synthesize = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.waitForTaskToken"
        Parameters = {
          Cluster        = aws_ecs_cluster.treatment.arn
          TaskDefinition = aws_ecs_task_definition.treatment_worker.arn
          LaunchType     = "FARGATE"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = module.vpc.public_subnets
              SecurityGroups = [aws_security_group.treatment_worker.id]
              AssignPublicIp = "ENABLED"
            }
          }
          Overrides = { ContainerOverrides = [{
            Name    = "treatment-worker"
            Command = ["--workflow-stage", "Synthesize"]
            Environment = [
              { Name = "TASK_TOKEN", "Value.$" = "$$.Task.Token" },
              { Name = "WORKFLOW_INPUT_KEY", "Value.$" = "$.input_key" },
              { Name = "WORKFLOW_STATE_KEY", "Value.$" = "$.state_key" },
              { Name = "WORKFLOW_INPUT_SHA256", "Value.$" = "$.input_sha256" },
            ]
          }] }
        }
        TimeoutSeconds = 900
        ResultPath     = "$.synthesis_result"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed", "States.Timeout"]
          IntervalSeconds = 5
          BackoffRate     = 2
          MaxAttempts     = 1
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "SendTreatmentFailure" }]
        Next  = "Validate"
      }
      Validate = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.waitForTaskToken"
        Parameters = {
          Cluster        = aws_ecs_cluster.treatment.arn
          TaskDefinition = aws_ecs_task_definition.treatment_worker.arn
          LaunchType     = "FARGATE"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = module.vpc.public_subnets
              SecurityGroups = [aws_security_group.treatment_worker.id]
              AssignPublicIp = "ENABLED"
            }
          }
          Overrides = { ContainerOverrides = [{
            Name    = "treatment-worker"
            Command = ["--workflow-stage", "Validate"]
            Environment = [
              { Name = "TASK_TOKEN", "Value.$" = "$$.Task.Token" },
              { Name = "WORKFLOW_INPUT_KEY", "Value.$" = "$.input_key" },
              { Name = "WORKFLOW_STATE_KEY", "Value.$" = "$.state_key" },
              { Name = "WORKFLOW_INPUT_SHA256", "Value.$" = "$.input_sha256" },
            ]
          }] }
        }
        TimeoutSeconds = 900
        ResultPath     = "$.validation_result"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed", "States.Timeout"]
          IntervalSeconds = 5
          BackoffRate     = 2
          MaxAttempts     = 1
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "SendTreatmentFailure" }]
        Next  = "ValidationChoice"
      }
      ValidationChoice = {
        Type = "Choice"
        Choices = [{
          Variable     = "$.validation_result.status"
          StringEquals = "VALIDATED"
          Next         = "Route"
          }, {
          And = [
            { Variable = "$.validation_result.status", StringEquals = "INVALID" },
            { Variable = "$.validation_result.retry_count", NumericLessThanEquals = 2 },
          ]
          Next = "Synthesize"
        }]
        Default = "Escalate"
      }
      Route = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.waitForTaskToken"
        Parameters = {
          Cluster        = aws_ecs_cluster.treatment.arn
          TaskDefinition = aws_ecs_task_definition.treatment_worker.arn
          LaunchType     = "FARGATE"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = module.vpc.public_subnets
              SecurityGroups = [aws_security_group.treatment_worker.id]
              AssignPublicIp = "ENABLED"
            }
          }
          Overrides = { ContainerOverrides = [{
            Name    = "treatment-worker"
            Command = ["--workflow-stage", "Route"]
            Environment = [
              { Name = "TASK_TOKEN", "Value.$" = "$$.Task.Token" },
              { Name = "WORKFLOW_INPUT_KEY", "Value.$" = "$.input_key" },
              { Name = "WORKFLOW_STATE_KEY", "Value.$" = "$.state_key" },
              { Name = "WORKFLOW_INPUT_SHA256", "Value.$" = "$.input_sha256" },
            ]
          }] }
        }
        TimeoutSeconds = 900
        ResultPath     = "$.route_result"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed", "States.Timeout"]
          IntervalSeconds = 5
          BackoffRate     = 2
          MaxAttempts     = 1
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "SendTreatmentFailure" }]
        Next  = "RouteProposal"
      }
      RouteProposal = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Parameters = {
          QueueUrl        = aws_sqs_queue.treatment_proposals.url
          "MessageBody.$" = "States.JsonToString($.route_result)"
        }
        ResultPath = null
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "SendTreatmentFailure" }]
        Next       = "TreatmentComplete"
      }
      Escalate = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Parameters = {
          QueueUrl        = aws_sqs_queue.treatment_workflow_failures.url
          "MessageBody.$" = "States.JsonToString($)"
        }
        ResultPath = null
        Catch      = [{ ErrorEquals = ["States.ALL"], Next = "FailureHandoffFailed" }]
        Next       = "TreatmentEscalated"
      }
      SendTreatmentFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Parameters = {
          QueueUrl        = aws_sqs_queue.treatment_workflow_failures.url
          "MessageBody.$" = "States.JsonToString($)"
        }
        ResultPath = null
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "FailureHandoffFailed"
        }]
        Next = "TreatmentFailed"
      }
      TreatmentComplete = {
        Type = "Succeed"
      }
      TreatmentEscalated = {
        Type = "Succeed"
      }
      TreatmentFailed = {
        Type  = "Fail"
        Error = "TreatmentTaskFailed"
        Cause = "Failure details were sent to the workflow failure queue."
      }
      FailureHandoffFailed = {
        Type  = "Fail"
        Error = "TreatmentFailureHandoffFailed"
        Cause = "The treatment task and failure queue handoff both failed. Inspect the execution history."
      }
    }
  })

  depends_on = [aws_iam_role_policy.treatment_workflow]

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-workflow"
  })
}

resource "aws_iam_role" "treatment_pipe" {
  name = "drift-thesis-treatment-pipe"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pipes.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.treatment_knowledge.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:pipes:ap-southeast-2:${data.aws_caller_identity.treatment_knowledge.account_id}:pipe/drift-thesis-treatment"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "treatment_pipe" {
  name = "read-treatment-queue-and-invoke-ingress"
  role = aws_iam_role.treatment_pipe.id

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
        Resource = aws_sqs_queue.drift_event_consumer["treatment"].arn
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.workflow_ingest.arn
      },
    ]
  })
}

resource "aws_pipes_pipe" "treatment" {
  name          = "drift-thesis-treatment"
  description   = "Persist each SQS treatment event and idempotently start its workflow."
  role_arn      = aws_iam_role.treatment_pipe.arn
  source        = aws_sqs_queue.drift_event_consumer["treatment"].arn
  target        = aws_lambda_function.workflow_ingest.arn
  desired_state = var.treatment_workflow_enabled ? "RUNNING" : "STOPPED"

  target_parameters {
    lambda_function_parameters {
      invocation_type = "REQUEST_RESPONSE"
    }
  }

  source_parameters {
    sqs_queue_parameters {
      batch_size                         = 1
      maximum_batching_window_in_seconds = 1
    }
  }

  lifecycle {
    precondition {
      condition     = !var.treatment_workflow_enabled || var.treatment_worker_desired_count == 0
      error_message = "Stop the legacy SQS treatment worker before enabling the workflow pipe."
    }
  }

  depends_on = [
    aws_iam_role_policy.treatment_pipe,
    aws_ecs_service.treatment_worker,
  ]

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-pipe"
  })
}

output "treatment_workflow_state_machine_arn" {
  description = "Standard Step Functions workflow for S3-backed treatment events."
  value       = aws_sfn_state_machine.treatment.arn
}

output "treatment_workflow_pipe_name" {
  description = "Stopped-by-default EventBridge Pipe from treatment SQS to idempotent ingress."
  value       = aws_pipes_pipe.treatment.name
}

output "treatment_workflow_failure_queue_url" {
  description = "Queue for treatment task failures and cases escalated for human review."
  value       = aws_sqs_queue.treatment_workflow_failures.url
}

output "treatment_proposal_review_queue_url" {
  description = "Queue containing references to validated, non-applying proposals for human review."
  value       = aws_sqs_queue.treatment_proposals.url
}
