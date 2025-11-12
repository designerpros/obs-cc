# Monitoring & Observability Guide

Comprehensive guide for monitoring and observability of the BBB post-stream extraction pipeline.

## Table of Contents
- [Monitoring Overview](#monitoring-overview)
- [Metrics](#metrics)
- [Logging](#logging)
- [Alerting](#alerting)
- [Dashboards](#dashboards)
- [Health Checks](#health-checks)

## Monitoring Overview

### Monitoring Stack

**Recommended Stack:**
```
┌─────────────────────────────────────────┐
│         Monitoring Architecture         │
├─────────────────────────────────────────┤
│                                           │
│  ┌──────────┐    ┌──────────┐          │
│  │Prometheus│◀───│ Workers  │          │
│  └────┬─────┘    └──────────┘          │
│       │                                  │
│       ▼                                  │
│  ┌──────────┐    ┌──────────┐          │
│  │ Grafana  │    │  Loki    │◀─── Logs │
│  └──────────┘    └──────────┘          │
│       │                                  │
│       ▼                                  │
│  ┌──────────┐    ┌──────────┐          │
│  │Alertmanager   │ Telegram │          │
│  └──────────┘    └──────────┘          │
│                                           │
└─────────────────────────────────────────┘
```

### Quick Setup

**Install Monitoring Stack (Kubernetes):**
```bash
# Add Prometheus helm repo
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Install kube-prometheus-stack
helm install prometheus prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace

# Verify installation
kubectl get pods -n monitoring
```

**Access Grafana:**
```bash
# Port-forward Grafana
kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80

# Default credentials:
# Username: admin
# Password: prom-operator
```

## Metrics

### Key Metrics to Monitor

**1. Throughput Metrics:**
```
# Streams processed per day
streams_processed_total

# Extractions generated per stream
extractions_per_stream

# Posts published per platform
posts_published_total{platform="youtube"}
```

**2. Latency Metrics:**
```
# Processing time per phase (seconds)
processing_duration_seconds{phase="transcription"}
processing_duration_seconds{phase="analysis"}
processing_duration_seconds{phase="rendering"}

# End-to-end pipeline time
pipeline_total_duration_seconds
```

**3. Resource Metrics:**
```
# GPU utilization (%)
gpu_utilization_percent{gpu="0"}

# GPU memory usage (MB)
gpu_memory_used_mb{gpu="0"}

# CPU usage per worker
container_cpu_usage_seconds_total{pod="bbb-transcription-xxx"}

# Memory usage per worker
container_memory_working_set_bytes{pod="bbb-rendering-xxx"}
```

**4. Queue Metrics:**
```
# Queue depth per phase
queue_depth{queue="transcription"}

# Job processing rate (jobs/sec)
rate(jobs_processed_total[5m])

# Job failure rate
rate(jobs_failed_total[5m])
```

**5. API Metrics:**
```
# LLM API calls per provider
llm_api_calls_total{provider="anthropic"}

# LLM API errors
llm_api_errors_total{provider="openai"}

# Late API calls
late_api_calls_total{operation="post"}
```

**6. Cost Metrics:**
```
# LLM API cost (USD)
llm_api_cost_usd{provider="grok"}

# S3 storage cost (USD/month)
s3_storage_cost_usd{bucket="sources"}
```

### Prometheus Configuration

**Scrape Config:**
```yaml
# prometheus-config.yaml
scrape_configs:
  - job_name: 'bbb-workers'
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - bbb-pipeline
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        action: keep
        regex: bbb-.*
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
```

**Apply configuration:**
```bash
kubectl apply -f prometheus-config.yaml -n monitoring
```

### Metrics Endpoint

**Expose metrics in worker:**
```python
# pipeline/common/metrics.py
from prometheus_client import Counter, Histogram, Gauge

# Define metrics
JOBS_PROCESSED = Counter(
    'jobs_processed_total',
    'Total jobs processed',
    ['phase', 'status']
)

PROCESSING_DURATION = Histogram(
    'processing_duration_seconds',
    'Time spent processing job',
    ['phase']
)

QUEUE_DEPTH = Gauge(
    'queue_depth',
    'Current queue depth',
    ['queue']
)

GPU_UTILIZATION = Gauge(
    'gpu_utilization_percent',
    'GPU utilization percentage',
    ['gpu']
)

# Start HTTP server for metrics
from prometheus_client import start_http_server
start_http_server(9090)
```

## Logging

### Log Levels

```python
# Configure log levels
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Levels:
# DEBUG: Detailed info for diagnosing problems
# INFO: General informational messages
# WARNING: Warning messages for unexpected events
# ERROR: Error messages for failures
# CRITICAL: Critical errors requiring immediate attention
```

### Structured Logging

**Use loguru for structured logging:**
```python
from loguru import logger

# Configure loguru
logger.add(
    "/data/bbb/logs/transcription.log",
    rotation="500 MB",
    retention="30 days",
    compression="gz",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    serialize=True  # JSON output
)

# Log with context
logger.info(
    "Transcription started",
    stream_id=stream_id,
    duration=duration,
    gpu_id=gpu_id
)
```

### Log Aggregation (Loki)

**Install Loki:**
```bash
# Add Grafana helm repo
helm repo add grafana https://grafana.github.io/helm-charts

# Install Loki
helm install loki grafana/loki-stack \
  -n monitoring \
  --set grafana.enabled=false \
  --set promtail.enabled=true
```

**Query logs in Grafana:**
```
# All errors from transcription worker
{app="bbb-transcription"} |= "ERROR"

# Specific stream processing
{app="bbb-transcription"} |= "stream_id=abc123"

# Job failures
{app="bbb-*"} |= "failed"
```

### Log Rotation

**Configure logrotate:**
```bash
sudo nano /etc/logrotate.d/bbb-pipeline
```

```
/data/bbb/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 bbb bbb
    postrotate
        systemctl reload bbb-* > /dev/null 2>&1 || true
    endscript
}
```

**Test logrotate:**
```bash
sudo logrotate -f /etc/logrotate.d/bbb-pipeline
```

## Alerting

### Alert Rules

**Create Prometheus alert rules:**
```yaml
# alerting-rules.yaml
groups:
  - name: bbb-pipeline-alerts
    interval: 30s
    rules:
      # Queue depth alerts
      - alert: QueueDepthHigh
        expr: queue_depth > 20
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High queue depth for {{ $labels.queue }}"
          description: "Queue {{ $labels.queue }} has {{ $value }} jobs pending"

      # Worker down alerts
      - alert: WorkerDown
        expr: up{job="bbb-workers"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Worker {{ $labels.pod }} is down"
          description: "Worker has been down for more than 2 minutes"

      # GPU utilization alerts
      - alert: GPUUtilizationLow
        expr: gpu_utilization_percent < 50
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Low GPU utilization on {{ $labels.gpu }}"
          description: "GPU {{ $labels.gpu }} utilization is {{ $value }}%"

      # Disk space alerts
      - alert: DiskSpaceLow
        expr: (node_filesystem_avail_bytes{mountpoint="/data/bbb"} / node_filesystem_size_bytes{mountpoint="/data/bbb"}) < 0.2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Low disk space on /data/bbb"
          description: "Only {{ $value | humanizePercentage }} space remaining"

      # Job failure rate alerts
      - alert: JobFailureRateHigh
        expr: rate(jobs_failed_total[5m]) > 0.1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High job failure rate"
          description: "Job failure rate is {{ $value | humanize }} failures/sec"

      # LLM API errors
      - alert: LLMAPIErrorsHigh
        expr: rate(llm_api_errors_total[5m]) > 0.5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High LLM API error rate for {{ $labels.provider }}"
          description: "Error rate is {{ $value | humanize }} errors/sec"

      # Cost alerts
      - alert: DailyCostHigh
        expr: increase(llm_api_cost_usd[24h]) > 100
        labels:
          severity: warning
        annotations:
          summary: "High daily LLM API costs"
          description: "Daily LLM costs exceeded $100: ${{ $value }}"
```

**Apply alert rules:**
```bash
kubectl apply -f alerting-rules.yaml -n monitoring
```

### Alertmanager Configuration

**Configure notifications:**
```yaml
# alertmanager-config.yaml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'severity']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 12h
  receiver: 'telegram'

receivers:
  - name: 'telegram'
    telegram_configs:
      - bot_token: '${TELEGRAM_BOT_TOKEN}'
        chat_id: ${TELEGRAM_CHAT_ID}
        parse_mode: 'HTML'
        message: |
          <b>{{ .GroupLabels.alertname }}</b>
          {{ range .Alerts }}
          {{ .Annotations.summary }}
          {{ .Annotations.description }}
          {{ end }}

  - name: 'discord'
    webhook_configs:
      - url: '${DISCORD_WEBHOOK_URL}'
        send_resolved: true
```

### Telegram Notifications

**Setup Telegram bot:**
1. Create bot via @BotFather
2. Get bot token
3. Send message to bot
4. Get chat_id: `curl https://api.telegram.org/bot${BOT_TOKEN}/getUpdates`

**Configure in code:**
```python
# pipeline/common/notifications.py
import requests

def send_telegram_notification(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload)

# Usage:
send_telegram_notification(
    f"<b>Transcription Complete</b>\n"
    f"Stream: {stream_id}\n"
    f"Duration: {duration}s\n"
    f"Topics: {topic_count}"
)
```

## Dashboards

### Grafana Dashboard Setup

**Import pre-built dashboard:**
```bash
# Download dashboard JSON
curl -o bbb-pipeline-dashboard.json \
  https://raw.githubusercontent.com/designerpros/obs-cc/main/dashboards/bbb-pipeline.json

# Import via Grafana UI:
# 1. Go to Dashboards → Import
# 2. Upload bbb-pipeline-dashboard.json
# 3. Select Prometheus data source
```

### Key Dashboard Panels

**1. Pipeline Overview:**
- Streams processed today
- Total extractions generated
- Posts published
- Average processing time
- Current queue depths

**2. Resource Utilization:**
- GPU utilization over time
- CPU usage per worker
- Memory usage per worker
- Disk I/O
- Network bandwidth

**3. Performance Metrics:**
- Processing time per phase (histogram)
- Job throughput (jobs/minute)
- Job success rate
- Queue depth over time

**4. Cost Tracking:**
- LLM API costs per provider
- S3 storage costs
- Total daily/monthly costs
- Cost per extraction

**5. Error Tracking:**
- Error rate over time
- Top error types
- Failed jobs by phase
- LLM API errors

### Custom Queries

**PromQL Examples:**

```promql
# Average transcription time
rate(processing_duration_seconds_sum{phase="transcription"}[5m]) /
rate(processing_duration_seconds_count{phase="transcription"}[5m])

# Job success rate
sum(rate(jobs_processed_total{status="success"}[5m])) /
sum(rate(jobs_processed_total[5m]))

# Extractions per stream
sum(extractions_per_stream) / sum(streams_processed_total)

# GPU efficiency (% of time GPU is > 80% utilized)
avg_over_time((gpu_utilization_percent > 80)[1h:])
```

## Health Checks

### Kubernetes Health Checks

**Liveness Probe:**
```yaml
livenessProbe:
  httpGet:
    path: /health/liveness
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 30
  timeoutSeconds: 10
  failureThreshold: 3
```

**Readiness Probe:**
```yaml
readinessProbe:
  httpGet:
    path: /health/readiness
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
```

### Health Check Endpoints

**Implementation:**
```python
# pipeline/common/health_checker.py
from fastapi import FastAPI
from typing import Dict

app = FastAPI()

@app.get("/health/liveness")
async def liveness() -> Dict[str, str]:
    """Basic alive check"""
    return {"status": "ok"}

@app.get("/health/readiness")
async def readiness() -> Dict[str, any]:
    """Check all dependencies"""
    checks = {
        "database": await check_database(),
        "redis": await check_redis(),
        "gpu": await check_gpu(),
        "disk": await check_disk_space(),
    }

    all_healthy = all(checks.values())
    status_code = 200 if all_healthy else 503

    return {
        "status": "ready" if all_healthy else "not_ready",
        "checks": checks
    }

async def check_database() -> bool:
    try:
        await db.execute("SELECT 1")
        return True
    except:
        return False

async def check_redis() -> bool:
    try:
        await redis.ping()
        return True
    except:
        return False

async def check_gpu() -> bool:
    import torch
    return torch.cuda.is_available()

async def check_disk_space() -> bool:
    import shutil
    usage = shutil.disk_usage("/data/bbb")
    free_percent = usage.free / usage.total
    return free_percent > 0.2  # 20% free
```

### External Monitoring

**UptimeRobot / Pingdom:**
```bash
# Monitor health endpoint
https://pipeline.yourdomain.com/health/readiness

# Alert if:
# - Status code != 200
# - Response time > 5s
# - Downtime > 2 minutes
```

## Monitoring Checklist

### Daily Checks
- [ ] Check queue depths (should be <5)
- [ ] Review error logs
- [ ] Verify GPU utilization (>80% during transcription)
- [ ] Check disk space (>20% free)
- [ ] Review Telegram/Discord alerts

### Weekly Checks
- [ ] Review Grafana dashboards
- [ ] Check processing time trends
- [ ] Review LLM API costs
- [ ] Analyze job success rates
- [ ] Check S3 storage growth

### Monthly Checks
- [ ] Performance optimization review
- [ ] Cost analysis (LLM + S3)
- [ ] Capacity planning
- [ ] Update dashboards
- [ ] Review alert thresholds

## Best Practices

1. **Monitor proactively** - Set up alerts before problems occur
2. **Log structured data** - Use JSON logging for easy parsing
3. **Track costs** - Monitor LLM API and S3 costs daily
4. **Review dashboards regularly** - Weekly review of trends
5. **Test alerts** - Quarterly alert testing
6. **Document incidents** - Keep postmortem log
7. **Optimize queries** - Keep Prometheus queries efficient
8. **Rotate logs** - Prevent disk space issues
9. **Backup Grafana** - Export dashboards regularly
10. **Keep retention reasonable** - 30 days metrics, 90 days logs

## Next Steps

- **[Operations Guide](operations.md)** - Day-to-day operations
- **[Troubleshooting Guide](troubleshooting.md)** - Problem resolution
- **[Configuration Guide](configuration.md)** - Tune monitoring settings
