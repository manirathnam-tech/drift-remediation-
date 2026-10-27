variable "kubernetes_version" {
  description = "EKS Kubernetes version"
  type        = string
  default     = "1.36"
}

variable "admin_cidr" {
  description = "Your public IPv4 address in CIDR notation, for example 203.0.113.10/32"
  type        = string

  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && try(split("/", var.admin_cidr)[1] == "32", false)
    error_message = "Provide one public IPv4 address as a /32 CIDR."
  }
}

variable "admin_role_arn" {
  description = "IAM role ARN granted EKS cluster-admin access"
  type        = string
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.26.0"

  name               = local.eks_cluster_name
  kubernetes_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  endpoint_private_access      = true
  endpoint_public_access       = true
  endpoint_public_access_cidrs = [var.admin_cidr]

  authentication_mode                      = "API_AND_CONFIG_MAP"
  enable_cluster_creator_admin_permissions = false
  enable_irsa                              = false

  access_entries = {
    admin = {
      principal_arn = var.admin_role_arn

      policy_associations = {
        cluster_admin = {
          policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

          access_scope = {
            type = "cluster"
          }
        }
      }
    }

    drift_injector = {
      principal_arn     = aws_iam_role.drift_injector.arn
      kubernetes_groups = ["drift-injector"]
      type              = "STANDARD"
    }
  }

  addons = {
    coredns = {
      addon_version = "v1.14.7-eksbuild.11"
    }

    eks-pod-identity-agent = {
      before_compute = true
      addon_version  = "v1.4.0-eksbuild.4"
    }

    kube-proxy = {
      addon_version = "v1.36.0-eksbuild.48"
    }

    vpc-cni = {
      before_compute = true
      addon_version  = "v1.23.2-eksbuild.3"

      configuration_values = jsonencode({
        enableNetworkPolicy = "true"
      })
    }
  }

  eks_managed_node_groups = {
    workers = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = ["t3.small"]
      capacity_type  = "SPOT"

      min_size     = 2
      desired_size = 3
      max_size     = 3
    }
  }

  tags = local.common_tags
}

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS Kubernetes API endpoint"
  value       = module.eks.cluster_endpoint
}
