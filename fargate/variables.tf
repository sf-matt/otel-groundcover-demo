variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-west-1"
}

variable "otel_endpoint" {
  # Bare base endpoint, no /v1/... suffix. app.py lets each OTLP exporter
  # read this env var itself rather than passing endpoint= explicitly, so
  # it auto-appends the correct per-signal path (/v1/traces, /v1/metrics,
  # /v1/logs). A path baked in here would break metrics/logs, which need
  # a different suffix than traces - confirmed directly against the SDK.
  description = "groundcover OTLP base endpoint for this workspace (no /v1/... path)"
  type        = string
  default     = "https://experiments.platform-dev.grcv.io"
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
