plugin "terraform" {
  enabled = true
  version = "0.13.0"
  source  = "github.com/terraform-linters/tflint-ruleset-terraform"
}

# These are Terraform root configurations with several purpose-specific files,
# not reusable modules with a single main.tf entrypoint.
rule "terraform_standard_module_structure" {
  enabled = false
}
