data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "fargate_demo" {
  name        = "fargate-demo-sg"
  description = "Allow outbound only (pull image, send telemetry)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "App port, restricted to allowed_ingress_cidr - this demo app has no auth"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ingress_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "demo" {
  name = "fargate-demo-cluster"
}

resource "aws_cloudwatch_log_group" "demo" {
  name              = "/ecs/fargate-demo"
  retention_in_days = 7
}

resource "aws_iam_role" "ecs_execution" {
  name = "fargate-demo-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The groundcover OTLP headers (including the API key) live here, not in the
# task definition's plaintext environment. ECS resolves this at task launch
# via the execution role below and injects it as an env var inside the
# container only - it never appears in the task definition JSON, the AWS
# console, or `aws ecs describe-task-definition`.
resource "aws_secretsmanager_secret" "otlp_headers" {
  name        = "fargate-demo/otel-otlp-headers"
  description = "OTEL_EXPORTER_OTLP_HEADERS for otel-groundcover-demo on Fargate (contains the groundcover ingestion API key)"
}

resource "aws_secretsmanager_secret_version" "otlp_headers" {
  secret_id = aws_secretsmanager_secret.otlp_headers.id
  # The whole header string is the secret, not just the key - ECS can inject
  # one resolved value per env var, it can't concatenate a secret with the
  # static "x-groundcover-..." suffixes at the task-definition level.
  secret_string = "apikey=${var.groundcover_api_key},x-groundcover-service-name=otel-groundcover-demo-fargate,x-groundcover-env-name=fargate-demo"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "fargate-demo-read-otlp-headers-secret"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.otlp_headers.arn]
    }]
  })
}

resource "aws_ecs_task_definition" "demo" {
  family                   = "otel-groundcover-demo"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([
    {
      name      = "otel-groundcover-demo"
      image     = "ghcr.io/sf-matt/otel-groundcover-demo:main"
      essential = true

      portMappings = [
        { containerPort = 8080, protocol = "tcp" }
      ]

      environment = [
        { name = "OTEL_SERVICE_NAME", value = "otel-groundcover-demo-fargate" },
        { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = var.otel_endpoint },
        { name = "OTEL_EXPORTER_OTLP_PROTOCOL", value = "http/protobuf" },
        { name = "OTEL_RESOURCE_ATTRIBUTES", value = var.otel_resource_attributes },
        # Fargate has no node to run a groundcover sensor on, so this is the
        # only way logs reach groundcover here - unlike k8s, where the
        # sensor already tails pod stdout and enabling this too would
        # double-ingest every log line.
        { name = "OTEL_LOGS_ENABLED", value = "true" }
      ]

      secrets = [
        {
          name      = "OTEL_EXPORTER_OTLP_HEADERS"
          valueFrom = aws_secretsmanager_secret_version.otlp_headers.arn
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.demo.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "demo" {
  name            = "otel-groundcover-demo"
  cluster         = aws_ecs_cluster.demo.id
  task_definition = aws_ecs_task_definition.demo.arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.fargate_demo.id]
    assign_public_ip = true
  }
}
