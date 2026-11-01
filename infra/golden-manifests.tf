data "aws_caller_identity" "golden_manifests" {}

variable "github_golden_repository" {
  description = "GitHub owner/repository that publishes rendered desired-state manifests."
  type        = string
  default     = "manirathnam-tech/drift-gitops"
}

resource "aws_s3_bucket" "golden_manifests" {
  bucket = "drift-thesis-golden-${data.aws_caller_identity.golden_manifests.account_id}"

  tags = merge(local.common_tags, {
    Name = "drift-thesis-golden-manifests"
  })
}

resource "aws_s3_bucket_public_access_block" "golden_manifests" {
  bucket                  = aws_s3_bucket.golden_manifests.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "golden_manifests" {
  bucket = aws_s3_bucket.golden_manifests.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "golden_manifests" {
  bucket = aws_s3_bucket.golden_manifests.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "golden_manifests" {
  bucket = aws_s3_bucket.golden_manifests.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "golden_manifests" {
  bucket = aws_s3_bucket.golden_manifests.id

  rule {
    id     = "clean-up-old-versions-and-incomplete-uploads"
    status = "Enabled"

    filter {
      prefix = "golden/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_iam_role" "github_golden_publisher" {
  name = "drift-thesis-golden-manifest-publisher"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github_actions.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_golden_repository}:ref:refs/heads/main"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "github_golden_publisher" {
  name = "publish-versioned-golden-manifests"
  role = aws_iam_role.github_golden_publisher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation"]
        Resource = aws_s3_bucket.golden_manifests.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketMultipartUploads"]
        Resource = aws_s3_bucket.golden_manifests.arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["golden/*"]
          }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
          "s3:PutObject",
        ]
        Resource = "${aws_s3_bucket.golden_manifests.arn}/golden/*"
      },
    ]
  })
}

output "golden_manifests_bucket_name" {
  description = "Private, versioned S3 bucket for rendered manifests indexed by Git revision."
  value       = aws_s3_bucket.golden_manifests.bucket
}

output "github_actions_golden_publisher_role_arn" {
  description = "Set as AWS_GOLDEN_PUBLISH_ROLE_ARN in the drift-gitops repository."
  value       = aws_iam_role.github_golden_publisher.arn
}
