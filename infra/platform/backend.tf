terraform {
  backend "s3" {
    bucket       = "drift-remediation-tfstate-manirathnam-2026"
    key          = "environment/dev/platform.tfstate"
    region       = "ap-southeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
