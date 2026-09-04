variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-west-1"
}

variable "otel_endpoint" {
  # Must include the /v1/traces path: app.py passes this straight to
  # OTLPSpanExporter(endpoint=...) explicitly, which - unlike the
  # env-var-only auto-resolution path - does NOT auto-append /v1/traces.
  # Confirmed directly against the installed SDK: passing endpoint="https://host"
  # results in the exporter using exactly that string, no suffix added.
  description = "groundcover OTLP traces endpoint for this workspace, including /v1/traces"
  type        = string
  default     = "https://experiments.platform-dev.grcv.io/v1/traces"
}

variable "groundcover_api_key" {
  description = "groundcover ingestion API key - set via terraform.tfvars (gitignored), never committed"
  type        = string
  sensitive   = true
}

variable "otel_resource_attributes" {
  description = "OTEL_RESOURCE_ATTRIBUTES value - comma-separated key=value custom resource tags, per groundcover's onboarding recommendation (not secret)"
  type        = string
  default     = "env=fargate-demo"
}

variable "allowed_ingress_cidr" {
  description = "CIDR allowed to reach the demo app on port 8080 (e.g. your public IP as x.x.x.x/32). This app has no auth, so keep this scoped, not 0.0.0.0/0."
  type        = string
}
