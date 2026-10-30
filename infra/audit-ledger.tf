resource "aws_dynamodb_table" "drift_audit_ledger" {
  name         = "drift-thesis-audit-ledger"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"

  attribute {
    name = "event_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

resource "aws_iam_role_policy" "collector_pod_audit_ledger" {
  name = "write-drift-audit-ledger"
  role = aws_iam_role.collector_pod_identity.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem"]
      Resource = aws_dynamodb_table.drift_audit_ledger.arn
    }]
  })
}
