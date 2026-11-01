locals {
  drift_event_consumers = {
    audit       = "drift-thesis-audit-events"
    convergence = "drift-thesis-convergence-events"
    treatment   = "drift-thesis-treatment-events"
  }
}

resource "aws_sqs_queue" "treatment_dead_letter" {
  name                      = "drift-thesis-treatment-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = merge(local.common_tags, {
    Name     = "drift-thesis-treatment-dlq"
    Consumer = "treatment-dead-letter"
  })
}

resource "aws_sqs_queue" "drift_event_consumer" {
  for_each = local.drift_event_consumers

  name                       = each.value
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = each.key == "treatment" ? 900 : 30
  redrive_policy            = each.key == "treatment" ? jsonencode({
    deadLetterTargetArn = aws_sqs_queue.treatment_dead_letter.arn
    maxReceiveCount     = 3
  }) : null

  tags = merge(local.common_tags, {
    Name     = each.value
    Consumer = each.key
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "treatment_dead_letter" {
  queue_url = aws_sqs_queue.treatment_dead_letter.url
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.drift_event_consumer["treatment"].arn]
  })
}

resource "aws_sqs_queue_policy" "drift_event_consumer" {
  for_each = aws_sqs_queue.drift_event_consumer

  queue_url = each.value.url
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowDriftEventsTopic"
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = each.value.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_sns_topic.drift_events.arn
        }
      }
    }]
  })
}

resource "aws_sns_topic_subscription" "drift_event_consumer" {
  for_each = aws_sqs_queue.drift_event_consumer

  topic_arn            = aws_sns_topic.drift_events.arn
  protocol             = "sqs"
  endpoint             = each.value.arn
  raw_message_delivery = true

  depends_on = [aws_sqs_queue_policy.drift_event_consumer]
}

output "drift_treatment_queue_url" {
  description = "Long-poll SQS queue for the treatment orchestrator."
  value       = aws_sqs_queue.drift_event_consumer["treatment"].url
}

output "drift_treatment_dead_letter_queue_url" {
  description = "Dead-letter queue for treatment messages after three receives."
  value       = aws_sqs_queue.treatment_dead_letter.url
}
