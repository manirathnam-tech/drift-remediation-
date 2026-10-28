output "vpc_id" {
  description = "ID of the VPC hosting the EKS cluster."
  value       = module.vpc.vpc_id
}

output "availability_zones" {
  description = "Availability zones used by the VPC subnets."
  value       = module.vpc.azs
}

output "public_subnet_ids" {
  description = "IDs of the public VPC subnets."
  value       = module.vpc.public_subnets
}

output "private_subnet_ids" {
  description = "IDs of the private VPC subnets."
  value       = module.vpc.private_subnets
}

output "nat_gateway_public_ips" {
  description = "Public IPv4 addresses assigned to the NAT gateways."
  value       = module.vpc.nat_public_ips
}
