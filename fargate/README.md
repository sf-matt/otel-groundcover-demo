# Fargate + groundcover example

Runs the existing public GHCR image (ghcr.io/sf-matt/otel-groundcover-demo)
on ECS Fargate, configured to send OTel traces/metrics/logs to groundcover.

## Setup

1. Copy the example vars file and fill in your real API key:

   cp terraform.tfvars.example terraform.tfvars
   # edit terraform.tfvars, replace REPLACE_ME with the real ingestion key

   terraform.tfvars is gitignored - it will never be committed.

2. Apply:

   terraform init
   terraform apply

## Notes

- Uses the default VPC + a public subnet with assign_public_ip = true,
  since there's no NAT gateway configured. Fine for a demo, not for
  production (use private subnets + NAT there instead).
- The groundcover OTel wiring (endpoint, headers, service/env name) has
  not yet been independently validated end-to-end for this Fargate case -
  confirm traces actually land in groundcover after first apply.
- The API key never appears in the task definition. Terraform stores the
  full OTEL_EXPORTER_OTLP_HEADERS string (including the key) in AWS Secrets
  Manager, and the task definition only references that secret's ARN - ECS
  resolves it into the container's environment at launch, via the execution
  role's scoped-down `secretsmanager:GetSecretValue` permission.
  This does NOT remove the plaintext value from Terraform state, though -
  `fargate/*.tfstate*` is gitignored so it never reaches the repo, but treat
  local state as sensitive too (or move to an encrypted remote backend if
  this stops being a one-off demo).
