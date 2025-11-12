"""
Prometheus Metrics
Real-time performance monitoring and observability
"""
from prometheus_client import Counter, Histogram, Gauge, Info
from loguru import logger

from .config import config


# ============================================================================
# PIPELINE METRICS
# ============================================================================

# Job processing
jobs_processed_total = Counter(
    'pipeline_jobs_processed_total',
    'Total number of jobs processed',
    ['job_type', 'status']  # labels
)

job_duration_seconds = Histogram(
    'pipeline_job_duration_seconds',
    'Job processing duration in seconds',
    ['job_type'],
    buckets=[10, 30, 60, 120, 300, 600, 1800, 3600]  # 10s to 1h
)

active_jobs_gauge = Gauge(
    'pipeline_active_jobs',
    'Number of currently active jobs',
    ['job_type']
)

# Stream processing
streams_processed_total = Counter(
    'pipeline_streams_processed_total',
    'Total number of streams processed',
    ['status']  # completed, failed
)

stream_duration_seconds = Histogram(
    'pipeline_stream_duration_seconds',
    'Total stream processing duration',
    buckets=[600, 1800, 3600, 7200, 10800]  # 10min to 3h
)

# ============================================================================
# LLM METRICS
# ============================================================================

llm_requests_total = Counter(
    'pipeline_llm_requests_total',
    'Total number of LLM API requests',
    ['model', 'purpose', 'status']
)

llm_tokens_total = Counter(
    'pipeline_llm_tokens_total',
    'Total number of LLM tokens used',
    ['model', 'token_type']  # input, output
)

llm_request_duration_seconds = Histogram(
    'pipeline_llm_request_duration_seconds',
    'LLM API request duration',
    ['model'],
    buckets=[0.5, 1, 2, 5, 10, 30, 60]
)

llm_cost_usd_total = Counter(
    'pipeline_llm_cost_usd_total',
    'Total LLM cost in USD',
    ['model']
)

# ============================================================================
# B-ROLL METRICS
# ============================================================================

broll_generated_total = Counter(
    'pipeline_broll_generated_total',
    'Total number of B-roll panels generated',
    ['style']
)

broll_reused_total = Counter(
    'pipeline_broll_reused_total',
    'Total number of B-roll panels reused from library',
    ['style']
)

broll_generation_duration_seconds = Histogram(
    'pipeline_broll_generation_duration_seconds',
    'B-roll generation duration',
    buckets=[5, 10, 20, 30, 60, 120]
)

broll_library_size_gauge = Gauge(
    'pipeline_broll_library_size',
    'Number of panels in B-roll library'
)

broll_cost_savings_usd_total = Counter(
    'pipeline_broll_cost_savings_usd_total',
    'Total cost savings from B-roll library reuse'
)

# ============================================================================
# RENDERING METRICS
# ============================================================================

extractions_created_total = Counter(
    'pipeline_extractions_created_total',
    'Total number of extractions created',
    ['extraction_type', 'format']  # long/short/micro, landscape/portrait
)

rendering_duration_seconds = Histogram(
    'pipeline_rendering_duration_seconds',
    'Video rendering duration',
    ['extraction_type', 'format'],
    buckets=[10, 30, 60, 120, 300, 600]
)

# ============================================================================
# POSTING METRICS
# ============================================================================

posts_scheduled_total = Counter(
    'pipeline_posts_scheduled_total',
    'Total number of posts scheduled',
    ['platform', 'status']
)

posts_published_total = Counter(
    'pipeline_posts_published_total',
    'Total number of posts successfully published',
    ['platform']
)

post_views_total = Counter(
    'pipeline_post_views_total',
    'Total views across all platforms',
    ['platform']
)

post_engagement_total = Counter(
    'pipeline_post_engagement_total',
    'Total engagement (likes, comments, shares)',
    ['platform', 'engagement_type']
)

# ============================================================================
# SYSTEM METRICS
# ============================================================================

gpu_utilization_percent = Gauge(
    'pipeline_gpu_utilization_percent',
    'GPU utilization percentage',
    ['gpu_id', 'node']
)

gpu_memory_used_bytes = Gauge(
    'pipeline_gpu_memory_used_bytes',
    'GPU memory used in bytes',
    ['gpu_id', 'node']
)

disk_usage_bytes = Gauge(
    'pipeline_disk_usage_bytes',
    'Disk usage in bytes',
    ['mount_point', 'node']
)

database_connections_gauge = Gauge(
    'pipeline_database_connections',
    'Number of active database connections'
)

redis_queue_size_gauge = Gauge(
    'pipeline_redis_queue_size',
    'Number of jobs in Redis queue',
    ['job_type']
)

# ============================================================================
# APPLICATION INFO
# ============================================================================

pipeline_info = Info(
    'pipeline',
    'Pipeline version and configuration'
)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def track_job_start(job_type: str):
    """Track job start"""
    active_jobs_gauge.labels(job_type=job_type).inc()


def track_job_complete(job_type: str, status: str, duration: float):
    """Track job completion"""
    jobs_processed_total.labels(job_type=job_type, status=status).inc()
    job_duration_seconds.labels(job_type=job_type).observe(duration)
    active_jobs_gauge.labels(job_type=job_type).dec()


def track_llm_call(
    model: str,
    purpose: str,
    status: str,
    input_tokens: int,
    output_tokens: int,
    duration: float,
    cost: float,
):
    """Track LLM API call"""
    llm_requests_total.labels(model=model, purpose=purpose, status=status).inc()
    llm_tokens_total.labels(model=model, token_type='input').inc(input_tokens)
    llm_tokens_total.labels(model=model, token_type='output').inc(output_tokens)
    llm_request_duration_seconds.labels(model=model).observe(duration)
    llm_cost_usd_total.labels(model=model).inc(cost)


def track_broll_generation(style: str, generated: int, reused: int, duration: float, savings: float):
    """Track B-roll generation"""
    broll_generated_total.labels(style=style).inc(generated)
    broll_reused_total.labels(style=style).inc(reused)
    broll_generation_duration_seconds.observe(duration)
    broll_cost_savings_usd_total.inc(savings)


def track_extraction_created(extraction_type: str, format: str, duration: float):
    """Track extraction creation"""
    extractions_created_total.labels(extraction_type=extraction_type, format=format).inc()
    rendering_duration_seconds.labels(extraction_type=extraction_type, format=format).observe(duration)


def track_post_scheduled(platform: str, status: str):
    """Track post scheduling"""
    posts_scheduled_total.labels(platform=platform, status=status).inc()


def track_post_published(platform: str):
    """Track successful post publication"""
    posts_published_total.labels(platform=platform).inc()


def update_broll_library_size(size: int):
    """Update B-roll library size gauge"""
    broll_library_size_gauge.set(size)


def update_gpu_metrics(gpu_id: str, node: str, utilization: float, memory_used: int):
    """Update GPU metrics"""
    gpu_utilization_percent.labels(gpu_id=gpu_id, node=node).set(utilization)
    gpu_memory_used_bytes.labels(gpu_id=gpu_id, node=node).set(memory_used)


def update_queue_size(job_type: str, size: int):
    """Update Redis queue size"""
    redis_queue_size_gauge.labels(job_type=job_type).set(size)


def set_pipeline_info(version: str, environment: str):
    """Set pipeline information"""
    pipeline_info.info({
        'version': version,
        'environment': environment,
        'cluster_nodes': str(config.get('cluster.node_count', 4)),
    })


logger.info("Prometheus metrics module loaded")
