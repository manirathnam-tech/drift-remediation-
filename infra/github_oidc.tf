variable "github_repository" {
  description = "GitHub owner/repository allowed to publish the injector image."
  type        = string
  default     = "manirathnam-tech/drift-remediation-"
}

variable "github_deploy_branch" {
  description = "Only this branch may assume the ECR publishing role."
  type        = string
  default     = "main"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "github_ecr_publisher" {
  name = "drift-thesis-github-ecr-publisher"

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
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/${var.github_deploy_branch}"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "github_ecr_publisher" {
  name = "publish-drift-injector-image"
  role = aws_iam_role.github_ecr_publisher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeRepositories",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.injector_runtime.arn
      },
    ]
  })
}

output "github_actions_ecr_publisher_role_arn" {
  description = "Set this as the AWS_ECR_PUBLISH_ROLE_ARN GitHub Actions variable."
  value       = aws_iam_role.github_ecr_publisher.arn
}
