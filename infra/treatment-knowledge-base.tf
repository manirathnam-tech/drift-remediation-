data "aws_partition" "treatment_knowledge" {}
data "aws_caller_identity" "treatment_knowledge" {}

locals {
  treatment_embedding_model_arn = "arn:${data.aws_partition.treatment_knowledge.partition}:bedrock:ap-southeast-2::foundation-model/amazon.titan-embed-text-v2:0"
}

resource "aws_s3_object" "treatment_knowledge" {
  for_each = fileset("${path.module}/orchestrator/knowledge_base", "*.md")

  bucket       = aws_s3_bucket.golden_manifests.id
  key          = "knowledge-base/${each.value}"
  source       = "${path.module}/orchestrator/knowledge_base/${each.value}"
  content_type = "text/markdown"
  etag         = filemd5("${path.module}/orchestrator/knowledge_base/${each.value}")
}

resource "aws_iam_role" "treatment_knowledge_base" {
  name = "drift-thesis-treatment-knowledge-base"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.treatment_knowledge.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.treatment_knowledge.partition}:bedrock:ap-southeast-2:${data.aws_caller_identity.treatment_knowledge.account_id}:knowledge-base/*"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "treatment_knowledge_base" {
  name = "drift-thesis-treatment-knowledge-access"
  role = aws_iam_role.treatment_knowledge_base.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.golden_manifests.arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["knowledge-base/*"]
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.golden_manifests.arn}/knowledge-base/*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:ListFoundationModels",
          "bedrock:ListCustomModels",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = local.treatment_embedding_model_arn
      },
    ]
  })
}

resource "aws_bedrockagent_knowledge_base" "treatment" {
  name        = "drift-thesis-treatment"
  description = "Curated DRIFT taxonomy and validation evidence embedded with Amazon Titan Text Embeddings V2."
  role_arn    = aws_iam_role.treatment_knowledge_base.arn

  knowledge_base_configuration {
    type = "MANAGED"

    managed_knowledge_base_configuration {
      embedding_model_type = "CUSTOM"
      embedding_model_arn  = local.treatment_embedding_model_arn

      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = 1024
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  depends_on = [aws_iam_role_policy.treatment_knowledge_base]
  tags       = local.common_tags
}

resource "aws_bedrockagent_data_source" "treatment" {
  name                 = "drift-thesis-treatment-corpus"
  knowledge_base_id    = aws_bedrockagent_knowledge_base.treatment.id
  data_deletion_policy = "DELETE"

  data_source_configuration {
    type = "MANAGED_KNOWLEDGE_BASE_CONNECTOR"

    managed_knowledge_base_connector_configuration {
      connector_parameters = jsonencode({
        type    = "S3"
        version = "1"
        connectionConfiguration = {
          bucketName           = aws_s3_bucket.golden_manifests.bucket
          bucketOwnerAccountId = data.aws_caller_identity.treatment_knowledge.account_id
        }
        aclEnabled = false
        filterConfiguration = {
          inclusionPrefixes      = ["knowledge-base/"]
          maxFileSizeInMegaBytes = "500"
        }
      })

      media_extraction_configuration {
        image_extraction_configuration {
          image_extraction_status = "ENABLED"
        }
      }
    }
  }

  depends_on = [aws_s3_object.treatment_knowledge]
}

output "treatment_knowledge_base_id" {
  description = "Bedrock Knowledge Base used by the treatment worker."
  value       = aws_bedrockagent_knowledge_base.treatment.id
}

output "treatment_knowledge_base_data_source_id" {
  description = "Managed S3 connector that must be ingested after corpus changes."
  value       = aws_bedrockagent_data_source.treatment.data_source_id
}
