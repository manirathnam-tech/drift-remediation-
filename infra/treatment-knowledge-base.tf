data "aws_partition" "treatment_knowledge" {}
data "aws_caller_identity" "treatment_knowledge" {}

variable "treatment_knowledge_base_embedding_dimensions" {
  description = "Titan Text Embeddings V2 dimensions used by the treatment knowledge base."
  type        = number
  default     = 1024

  validation {
    condition     = contains([256, 512, 1024], var.treatment_knowledge_base_embedding_dimensions)
    error_message = "Titan Text Embeddings V2 dimensions must be 256, 512, or 1024."
  }
}

resource "aws_s3vectors_vector_bucket" "treatment_knowledge" {
  vector_bucket_name = "drift-thesis-treatment-${data.aws_caller_identity.treatment_knowledge.account_id}"
  force_destroy      = true

  tags = merge(local.common_tags, {
    Name = "drift-thesis-treatment-knowledge"
  })
}

resource "aws_s3vectors_index" "treatment_knowledge" {
  index_name         = "drift-treatment-knowledge"
  vector_bucket_name = aws_s3vectors_vector_bucket.treatment_knowledge.vector_bucket_name
  data_type          = "float32"
  dimension          = var.treatment_knowledge_base_embedding_dimensions
  distance_metric    = "cosine"

  metadata_configuration {
    non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
  }
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
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:${data.aws_partition.treatment_knowledge.partition}:bedrock:ap-southeast-2::foundation-model/amazon.titan-embed-text-v2:0"
      },
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
          "s3vectors:PutVectors",
          "s3vectors:GetVectors",
          "s3vectors:DeleteVectors",
          "s3vectors:QueryVectors",
          "s3vectors:GetIndex",
        ]
        Resource = aws_s3vectors_index.treatment_knowledge.index_arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3vectors:ListIndexes"]
        Resource = aws_s3vectors_vector_bucket.treatment_knowledge.vector_bucket_arn
      },
    ]
  })
}

resource "aws_bedrockagent_knowledge_base" "treatment" {
  name        = "drift-thesis-treatment"
  description = "Curated DRIFT taxonomy and validation evidence."
  role_arn    = aws_iam_role.treatment_knowledge_base.arn

  knowledge_base_configuration {
    type = "VECTOR"

    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:${data.aws_partition.treatment_knowledge.partition}:bedrock:ap-southeast-2::foundation-model/amazon.titan-embed-text-v2:0"

      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = var.treatment_knowledge_base_embedding_dimensions
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"

    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.treatment_knowledge.index_arn
    }
  }

  depends_on = [aws_iam_role_policy.treatment_knowledge_base]
  tags       = local.common_tags
}

resource "aws_bedrockagent_data_source" "treatment" {
  name              = "drift-thesis-treatment-corpus"
  knowledge_base_id = aws_bedrockagent_knowledge_base.treatment.id

  data_source_configuration {
    type = "S3"

    s3_configuration {
      bucket_arn         = aws_s3_bucket.golden_manifests.arn
      inclusion_prefixes = ["knowledge-base/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"

      fixed_size_chunking_configuration {
        max_tokens         = 300
        overlap_percentage = 10
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
  description = "S3 data source that must be ingested after corpus changes."
  value       = aws_bedrockagent_data_source.treatment.data_source_id
}
