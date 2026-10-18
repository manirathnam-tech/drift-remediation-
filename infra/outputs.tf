output "vpc_id" {
  value = module.vpc.vpc_id
}

output "availability_zones" {
  value = module.vpc.azs
}

output "public_subnet_ids" {
  value = module.vpc.public_subnets
}

output "private_subnet_ids" {
  value = module.vpc.private_subnets
}

output "nat_gateway_public_ips" {
  value = module.vpc.nat_public_ips
}
