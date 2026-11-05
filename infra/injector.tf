output "drift_injector_execution_mode" {
  description = "Synthetic drift injection runs as an in-cluster Job using the injector Pod Identity role."
  value       = "kubernetes-job"
}

output "drift_injector_ledger_table" {
  description = "DynamoDB table that records successful T_inject timestamps."
  value       = data.aws_dynamodb_table.injector_runtime.name
}
