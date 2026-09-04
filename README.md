# OTEL Groundcover Demo

A small public-friendly Flask service for exercising GitHub Actions, GHCR, Kubernetes, and OpenTelemetry traces. It has no credentials or environment-specific integrations checked in.

Released under the [MIT License](LICENSE). See [NOTICE.md](NOTICE.md) for the Claude AI-assistance attribution.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q
python app.py
```

The app listens on `http://localhost:8080`. Without an OTLP endpoint, traces remain local. To export traces over OTLP/HTTP, set the standard variables before starting it:

```bash
export OTEL_SERVICE_NAME=otel-groundcover-demo
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
python app.py
```

## Trigger traffic and failures

```bash
curl http://localhost:8080/work?workload=checkout
curl http://localhost:8080/fail/http-500
curl http://localhost:8080/fail/exception
curl 'http://localhost:8080/fail/timeout?seconds=3'
curl http://localhost:8080/fail/downstream
```

The failure routes intentionally return errors so they are easy to find in traces and HTTP metrics. `/fail/timeout` caps the delay at ten seconds. Failure routes also log a structured JSON line (with `trace_id`/`span_id`) so groundcover can jump from a log line to the exact trace that produced it — see [Logs and traces](#logs-and-traces-rca).

## Metrics and scraping

`/metrics` exposes a Prometheus/OpenMetrics endpoint with one custom gauge, `demo_workload_queue_depth`, set via `/work?queue_depth=N`:

```bash
curl 'http://localhost:8080/work?queue_depth=500'
curl http://localhost:8080/metrics
```

HTTP request rate, error rate, and latency for this workload don't need custom instrumentation — groundcover's eBPF sensor derives those automatically for anything running as a Kubernetes workload (see `groundcover_workload_total_counter`, `groundcover_workload_error_counter`, `groundcover_workload_latency_seconds`). The custom gauge exists to demo scraping an app-level metric that eBPF can't see.

`k8s/deployment.yaml` sets the standard `prometheus.io/scrape`, `prometheus.io/port`, and `prometheus.io/path` pod annotations so groundcover's Prometheus-compatible scraper picks up `/metrics` once deployed.

## Logs and traces (RCA)

Failure routes log structured JSON to stdout with `trace_id`/`span_id` extracted from the active span, e.g.:

```json
{"timestamp": "...", "level": "ERROR", "message": "intentional HTTP 500 triggered", "logger": "otel-groundcover-demo", "trace_id": "...", "span_id": "..."}
```

groundcover correlates logs and traces on a shared `trace_id` field (case-insensitive, several accepted suffixes) — this lets you jump from a trace straight to the log line it produced, or the reverse, during an investigation.

### Linking the CI/CD deploy trace to the running app

The `groundcover-github-action` used in `export-traces.yml` supports linking a CI/CD trace to application traces via a W3C `traceparent`. This repo wires that up end-to-end:

1. The `publish` job in `ci.yml` generates a random `traceparent`, uploads it as the `deploy-traceparent` workflow artifact, and stamps it onto the pushed image as the `groundcover.traceparent` OCI label.
2. `export-traces.yml` downloads that artifact and passes it as the action's `traceparent` input (along with `workload: otel-groundcover-demo`), so the exported CI/CD trace becomes part of the same trace as whatever deploys the image.
3. To link the *running app* to that same trace, read the traceparent back off the image and pass it into the container as `TRACEPARENT`:

   ```bash
   TRACEPARENT=$(docker inspect ghcr.io/<owner>/otel-groundcover-demo:main --format '{{ index .Config.Labels "groundcover.traceparent" }}')
   kubectl set env deployment/otel-groundcover-demo TRACEPARENT="$TRACEPARENT"
   ```

   On next pod start, the app emits one `app.startup` span as a child of that trace — so from the CI/CD trace you can jump straight to the moment this rollout came up, and from there into whatever traces or logs followed. `k8s/deployment.yaml` includes an empty `TRACEPARENT` env var as a placeholder for this.

## Monitors

`monitors/` has two example monitor definitions, in groundcover's [monitor YAML format](https://docs.groundcover.com/use-groundcover/monitors/monitor-yaml-structure), that you can import to see alerting fire against this demo:

- `high-http-error-rate.yaml` — a traces-based monitor on this workload's 5xx rate. Trigger it by hitting `/fail/http-500`, `/fail/exception`, or `/fail/downstream` a few times in a row.
- `high-queue-depth.yaml` — a MetricsQL-based monitor on the custom `demo_workload_queue_depth` gauge. Trigger it with `curl '<app-url>/work?queue_depth=500'`. Double-check the label filter against how the metric actually shows up in your workspace's metrics explorer before relying on it — Prometheus-scrape label enrichment can vary by setup.

Import either file via the groundcover UI's monitor import, or apply them with the [Terraform provider](https://docs.groundcover.com/use-groundcover/monitors) for a version-controlled setup.

## GitHub Actions and GHCR

The workflow in `.github/workflows/ci.yml` runs tests for pull requests and pushes. On pushes to `main` or version tags, it publishes a container to `ghcr.io/<owner>/<repository>` using the built-in `GITHUB_TOKEN`. The package may need to be made public in the repository's GitHub package settings.

The workflow also runs Semgrep on every pull request and push. Findings are uploaded as a workflow artifact for review, but the Semgrep job is intentionally non-blocking for this test repository.

The separate `.github/workflows/export-traces.yml` workflow sends completed GitHub Actions run traces to Groundcover using `groundcover-com/groundcover-github-action@v3`. Add these repository secrets before enabling it:

- `GC_ENDPOINT`: Groundcover API endpoint
- `GC_API_KEY`: Groundcover API key

The export workflow is triggered after `Build and Push App` completes and does not expose either secret in logs or image layers.

## GitOps deploy with Argo CD

Deploys are fully automated via Argo CD rather than manual `kubectl apply`:

1. On every push to `main`, the `publish` job in `ci.yml` builds and pushes an immutable, sha-tagged image (`ghcr.io/<owner>/otel-groundcover-demo:sha-<shortsha>`), then rewrites the `image:` line in `k8s/deployment.yaml` to that tag and pushes the commit back to `main` (with `[skip ci]`, so it doesn't retrigger CI). This requires the `publish` job's `contents: write` permission, already set in `ci.yml`.
2. Argo CD watches this repo's `k8s/` path and auto-syncs (`prune` + `selfHeal`) into its own `otel-groundcover-demo` namespace, so that commit rolls out on its own.

Bootstrap the Argo CD `Application` once, against a cluster that already has Argo CD installed:

```bash
kubectl apply -f argocd/application.yaml
```

`argocd/application.yaml` hardcodes this repo's URL (`sf-matt/otel-groundcover-demo`) — update `spec.source.repoURL` if you fork it. Because the deployed tag now lives only in git (kept current by CI), don't hand-edit the `image:` line in `k8s/deployment.yaml` — a manual edit will just get overwritten by the next push, or reverted by Argo CD's `selfHeal` if the cluster and git ever disagree.

## Kubernetes and Groundcover

The manifest points `OTEL_EXPORTER_OTLP_ENDPOINT` at the groundcover sensor's in-cluster OTLP/HTTP endpoint, `groundcover-sensor.groundcover.svc.cluster.local:4318` — the standard address for a self-hosted groundcover install (adjust the namespace/service name if yours differs). If you're sending to groundcover's SaaS ingestion instead, point this at the OTLP endpoint from your workspace and add any required auth through a Kubernetes Secret referenced from the Deployment, rather than committing it.

The app's standard environment variables are compatible with collector-based setups:

- `OTEL_SERVICE_NAME`
- `OTEL_EXPORTER_OTLP_ENDPOINT`
- `DEPLOYMENT_ENVIRONMENT`
- `APP_VERSION`
