data "aws_caller_identity" "golden_manifests" {}

resource "aws_s3_bucket" "golden_manifests" {
  # A future Terraform destroy permanently deletes every object in this bucket.
  force_destroy = true
  bucket        = "drift-thesis-golden-${data.aws_caller_identity.golden_manifests.account_id}"

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

output "golden_manifests_bucket_name" {
  description = "Private, versioned S3 bucket for rendered manifests indexed by Git revision."
  value       = aws_s3_bucket.golden_manifests.bucket
}
