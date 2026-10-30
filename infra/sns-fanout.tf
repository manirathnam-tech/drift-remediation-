locals {
  drift_event_consumers = {
    audit       = "drift-thesis-audit-events"
    convergence = "drift-thesis-convergence-events"
  }
}

resource "aws_sqs_queue" "drift_event_consumer" {
  for_each = local.drift_event_consumers

  name                      = each.value
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = merge(local.common_tags, {
    Name     = each.value
    Consumer = each.key
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
