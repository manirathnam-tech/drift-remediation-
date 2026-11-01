resource "aws_dynamodb_table" "treatment_audit" {
  name         = "drift-thesis-treatment-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"
  range_key    = "record_id"

  attribute {
    name = "event_id"
    type = "S"
  }

  attribute {
    name = "record_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-audit"
  })
}

output "treatment_audit_table_arn" {
  description = "DynamoDB table for append-only treatment attempts and decisions."
  value       = aws_dynamodb_table.treatment_audit.arn
}
