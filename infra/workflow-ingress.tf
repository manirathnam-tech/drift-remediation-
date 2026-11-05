data "archive_file" "workflow_ingest" {
  type        = "zip"
  source_file = "${path.module}/orchestrator/src/workflow_ingest.py"
  output_path = "${path.module}/orchestrator/workflow-ingest.zip"
}

resource "aws_cloudwatch_log_group" "workflow_ingest" {
  name              = "/aws/lambda/drift-thesis-workflow-ingest"
  retention_in_days = 14

  tags = merge(local.common_tags, {
    Name = "drift-thesis-workflow-ingest"
  })
}

resource "aws_iam_role" "workflow_ingest" {
  name = "drift-thesis-workflow-ingest"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "workflow_ingest" {
  name = "persist-treatment-event-for-workflow"
  role = aws_iam_role.workflow_ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.golden_manifests.arn}/workflow-runs/inputs/*",
          "${aws_s3_bucket.golden_manifests.arn}/workflow-runs/state/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.treatment.arn
      },
      {
        Effect   = "Allow"
        Action   = ["states:DescribeExecution"]
        Resource = "${replace(aws_sfn_state_machine.treatment.arn, ":stateMachine:", ":execution:")}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.workflow_ingest.arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "workflow_ingest" {
  function_name    = "drift-thesis-workflow-ingest"
  description      = "Persist a validated treatment event and idempotently start its workflow."
  role             = aws_iam_role.workflow_ingest.arn
  runtime          = "python3.12"
  handler          = "workflow_ingest.handler"
  filename         = data.archive_file.workflow_ingest.output_path
  source_code_hash = data.archive_file.workflow_ingest.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      WORKFLOW_STATE_BUCKET      = aws_s3_bucket.golden_manifests.bucket
      WORKFLOW_STATE_MACHINE_ARN = aws_sfn_state_machine.treatment.arn
    }
  }

  depends_on = [
    aws_iam_role_policy.workflow_ingest,
    aws_cloudwatch_log_group.workflow_ingest,
  ]

  tags = merge(local.common_tags, {
    Name = "drift-thesis-workflow-ingest"
  })
}

output "treatment_workflow_ingest_lambda_name" {
  description = "Pipe target Lambda that stores events in S3 and starts a workflow execution."
  value       = aws_lambda_function.workflow_ingest.function_name
}
