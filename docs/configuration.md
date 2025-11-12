# Configuration Guide

Complete reference for configuring the BBB post-stream extraction pipeline.

## Table of Contents
- [Configuration File Location](#configuration-file-location)
- [Environment Variables](#environment-variables)
- [Infrastructure](#infrastructure)
- [Ingestion](#ingestion)
- [Transcription](#transcription)
- [Analysis](#analysis)
- [Rendering](#rendering)
- [Posting](#posting)
- [Archival](#archival)
- [Monitoring](#monitoring)
- [Performance Tuning](#performance-tuning)

## Configuration File Location

**Primary config file:** `/opt/obs-cc/config/pipeline.yaml`

**Environment-specific overrides:**
- Development: `config/pipeline.dev.yaml`
- Staging: `config/pipeline.staging.yaml`
- Production: `config/pipeline.prod.yaml`

**Load priority:**
1. Environment variables
2. Environment-specific YAML file
3. Default `pipeline.yaml`

## Environment Variables

### Required Environment Variables

```bash
# Database
POSTGRES_HOST=localhost
POSTGRES_USER=pipeline_worker
POSTGRES_PASSWORD=<secure_password>

# Redis
REDIS_HOST=localhost
REDIS_PASSWORD=<secure_password>

# S3 Storage
S3_ENDPOINT=s3.amazonaws.com
S3_ACCESS_KEY=<your_access_key>
S3_SECRET_KEY=<your_secret_key>

# LLM APIs
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROK_API_KEY=xai-...
PERPLEXITY_API_KEY=pplx-...

# Posting
LATE_API_ENDPOINT=https://api.late.dev
LATE_API_KEY=<your_late_api_key>

# Monitoring (optional)
TELEGRAM_BOT_TOKEN=<your_bot_token>
TELEGRAM_CHAT_ID=<your_chat_id>
DISCORD_WEBHOOK_URL=<your_webhook_url>
```

### Setting Environment Variables

**Option 1: .env file (Development)**
```bash
cp .env.example .env
nano .env
# Add your variables
```

**Option 2: Kubernetes Secrets (Production)**
```bash
kubectl create secret generic bbb-secrets \
  --from-literal=POSTGRES_PASSWORD=<password> \
  --from-literal=ANTHROPIC_API_KEY=<key> \
  -n bbb-pipeline
```

**Option 3: System Environment (Solo Node)**
```bash
# Add to ~/.bashrc or /etc/environment
export POSTGRES_PASSWORD="secure_password"
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Infrastructure

### Mode Configuration

```yaml
infrastructure:
  mode: "k3s"  # Options: 'local', 'k3s', 'k8s'
```

**Modes:**
- `local`: Docker Compose deployment on single node
- `k3s`: Lightweight Kubernetes (Rancher K3s)
- `k8s`: Full Kubernetes cluster

### Database Configuration

```yaml
infrastructure:
  database:
    host: "${POSTGRES_HOST:-localhost}"
    port: 5432
    database: "stream_extraction"
    user: "${POSTGRES_USER:-pipeline_worker}"
    password: "${POSTGRES_PASSWORD}"
    pool_size: 20              # Connection pool size
    max_overflow: 10           # Max overflow connections
```

**Tuning Guidelines:**
- **Small deployment** (<5 streams/day): `pool_size: 10`, `max_overflow: 5`
- **Medium deployment** (5-20 streams/day): `pool_size: 20`, `max_overflow: 10`
- **Large deployment** (20+ streams/day): `pool_size: 50`, `max_overflow: 20`

### Redis Configuration

```yaml
infrastructure:
  redis:
    host: "${REDIS_HOST:-localhost}"
    port: 6379
    db: 0
    password: "${REDIS_PASSWORD}"
    max_connections: 50
```

**Tuning Guidelines:**
- Each worker needs ~5 connections
- `max_connections` = (number of workers × 5) + 20% buffer

### S3 Configuration

```yaml
infrastructure:
  s3:
    endpoint: "${S3_ENDPOINT}"          # s3.amazonaws.com, s3.us-east-1.wasabisys.com, etc.
    access_key: "${S3_ACCESS_KEY}"
    secret_key: "${S3_SECRET_KEY}"
    bucket_sources: "stream-sources"    # Sources bucket name
    bucket_extractions: "stream-extractions"  # Extractions bucket name
    region: "us-east-1"

    # Lifecycle policies
    sources_storage_class: "DEEP_ARCHIVE"   # Perpetual storage
    extractions_storage_class: "GLACIER"
    extractions_delete_after_days: 1825     # 5 years
```

**Storage Class Options:**
- `STANDARD`: Hot storage, instant access ($0.023/GB/month)
- `STANDARD_IA`: Infrequent access ($0.0125/GB/month)
- `GLACIER`: Cold storage, 3-5 hour retrieval ($0.004/GB/month)
- `DEEP_ARCHIVE`: Archive storage, 12-48 hour retrieval ($0.00099/GB/month)

**Cost Estimate (per 1-hour stream, 6 cameras):**
- Sources: ~50 GB → $0.05/month (Deep Glacier)
- Extractions: ~2 GB → $0.008/month (Glacier)

## Ingestion

### File Validation

```yaml
ingestion:
  validation:
    required_files:
      - "cam_main_me.mp4"
      - "live_mix.mp4"

    optional_files:
      - "cam_guest.mp4"
      - "cam_overhead.mp4"
      - "cam_screen.mp4"
      - "cam_online_caller.mp4"

    video_codec: "hevc"               # HEVC/H.265
    min_duration_seconds: 600         # At least 10 minutes
    max_size_gb: 100                  # Per file
```

**Smart Detection:**
- If exact filenames not found, AI-powered matcher attempts to identify files
- Requires at least 2 files to proceed (main camera + mix or guest)

### Working Directory

```yaml
ingestion:
  working_dir: "/mnt/pipeline/work"
  copy_originals: true                # Always work on copies
```

**Storage Requirements:**
- Allocate 3x stream size for working directory
- Example: 50 GB stream → 150 GB working space

### Triggers

```yaml
ingestion:
  triggers:
    file_watcher:
      enabled: true
      watch_path: "${WATCH_PATH:-/mnt/obs-recordings/trigger}"
      poll_interval_seconds: 5

    webhook:
      enabled: true
      host: "0.0.0.0"
      port: 8080
      endpoint: "/trigger"
      auth_token: "${WEBHOOK_AUTH_TOKEN}"
```

**File Watcher:**
- Monitors directory for new stream folders
- Triggers ingestion when folder appears

**Webhook:**
- HTTP POST to `http://<server>:8080/trigger`
- Payload: `{"stream_path": "/path/to/stream"}`
- Requires `Authorization: Bearer <auth_token>` header

## Transcription

### Whisper Configuration

```yaml
transcription:
  engine: "whisper-large-v3"

  whisper:
    model: "large-v3"                 # Options: tiny, base, small, medium, large, large-v3
    device: "cuda"                    # cuda or cpu
    compute_type: "float16"           # float16, float32, int8
    language: "en"

    # Performance tuning
    batch_size: 16                    # Higher = faster, more VRAM
    beam_size: 5                      # Higher = better accuracy, slower
    best_of: 5
    temperature: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

    # VAD (Voice Activity Detection)
    vad_filter: true
    vad_threshold: 0.5                # 0.0-1.0, lower = more sensitive
```

**Model Selection:**
| Model | VRAM | Speed | Quality |
|-------|------|-------|---------|
| tiny | 1 GB | 30x realtime | Fair |
| base | 1 GB | 15x realtime | Good |
| small | 2 GB | 8x realtime | Better |
| medium | 5 GB | 4x realtime | Great |
| large-v3 | 10 GB | 2-3x realtime | Excellent |

**Compute Type:**
- `float32`: Best quality, highest VRAM usage
- `float16`: Balanced (recommended for GPU)
- `int8`: Lowest VRAM, slightly lower quality

**Performance Tuning:**
- **Fast:** `batch_size: 24`, `beam_size: 3`, `compute_type: int8`
- **Balanced:** `batch_size: 16`, `beam_size: 5`, `compute_type: float16`
- **Quality:** `batch_size: 8`, `beam_size: 10`, `compute_type: float32`

### Speaker Diarization

```yaml
transcription:
  diarization:
    enabled: true
    engine: "pyannote"
    model: "pyannote/speaker-diarization-3.1"
    min_speakers: 1
    max_speakers: 4                   # You, guest, caller, extra

    # Speaker identification
    known_speakers:
      - name: "Host"
        reference_audio: "${HOST_VOICE_SAMPLE}"
      - name: "Guest"
        reference_audio: null         # Auto-detect
```

**Speaker Identification:**
- Provide reference audio file (10-30 seconds) for host
- System will automatically detect other speakers

### Fallback Configuration

```yaml
transcription:
  fallback:
    enabled: true
    api_key: "${ASSEMBLYAI_API_KEY}"
    use_conditions:
      - "whisper_failure"
      - "processing_time_exceeds_600s"  # 10 min timeout
```

## Analysis

### LLM Cascade Configuration

```yaml
analysis:
  coherence:
    llm_providers:
      - name: "grok"
        api_key: "${GROK_API_KEY}"
        endpoint: "https://api.x.ai/v1"
        model: "grok-2-1212"
        weight: 1.0

      - name: "openai"
        api_key: "${OPENAI_API_KEY}"
        model: "gpt-4o"
        weight: 1.0

      - name: "anthropic"
        api_key: "${ANTHROPIC_API_KEY}"
        model: "claude-3-5-sonnet-20241022"
        weight: 1.0

      - name: "perplexity"
        api_key: "${PERPLEXITY_API_KEY}"
        endpoint: "https://api.perplexity.ai"
        model: "llama-3.1-sonar-large-128k-online"
        weight: 1.0
```

**LLM Provider Costs:**
| Provider | Model | Cost/Call | Use Case |
|----------|-------|-----------|----------|
| Grok | grok-2-1212 | $0.01 | Cheap screening |
| Claude | claude-3-5-sonnet | $0.015 | Mid-tier validation |
| GPT-4o | gpt-4o | $0.03 | Consensus |
| Perplexity | sonar-large | $0.03 | Consensus |

### Scoring Criteria

```yaml
analysis:
  coherence:
    criteria:
      - coherence                     # Does segment flow logically?
      - standalone_viability          # Can it work independently?
      - hook_strength                 # Compelling opening?
      - topic_relevance               # Clear topic focus?
      - engagement_potential          # Interesting content?

    consensus_threshold: 0.75         # 3/4 LLMs must approve
    min_score_per_llm: 80             # Each LLM must score >= 80/100

    regenerate_on_fail: true
    max_regeneration_attempts: 2
```

**Approval Logic:**
1. Each LLM scores topic 0-100 across 5 criteria
2. Average criteria scores → overall score per LLM
3. Require 3/4 LLMs to score ≥80 (or ≥70 fallback)
4. If fails, regenerate extraction boundaries and retry

### Topic Segmentation

```yaml
analysis:
  topic_segmentation:
    llm_provider: "anthropic"
    model: "claude-3-5-sonnet-20241022"
    api_key: "${ANTHROPIC_API_KEY}"

    min_topic_duration_seconds: 120   # At least 2 minutes per topic
    max_topics_per_stream: 20

    categories:
      - "crypto"
      - "finance"
      - "economics"
      - "politics"
      - "policy"
      - "monetary_policy"
      - "business"
      - "technology"
      - "markets"
      - "other"
```

## Rendering

### FFMPEG Configuration

```yaml
rendering:
  ffmpeg:
    threads: 8                        # CPU threads for encoding
    hardware_acceleration: "cuda"     # Use NVENC on RTX GPUs

    # Video encoding
    video_codec: "h264_nvenc"         # NVIDIA hardware encoder
    preset: "slow"                    # Quality over speed
    crf: 18                           # High quality (0-51, lower = better)
    pixel_format: "yuv420p"

    # Audio encoding
    audio_codec: "aac"
    audio_bitrate: "192k"
    audio_sample_rate: 48000
```

**Video Codec Options:**
- `h264_nvenc`: NVIDIA GPU encoding (5-10x faster)
- `libx264`: CPU encoding (better quality, slower)
- `hevc_nvenc`: H.265 GPU encoding (smaller files)

**Preset Options (quality vs. speed):**
- `fast`: Quick encoding, lower quality
- `medium`: Balanced
- `slow`: High quality (recommended)
- `veryslow`: Best quality, very slow

**CRF (Constant Rate Factor):**
- 0: Lossless (huge files)
- 18: Visually lossless (recommended)
- 23: High quality (default)
- 28: Medium quality
- 51: Lowest quality

### Output Formats

```yaml
rendering:
  outputs:
    landscape:
      resolution: "1920x1080"
      fps: 30
      aspect_ratio: "16:9"

    portrait:
      resolution: "1080x1920"
      fps: 30
      aspect_ratio: "9:16"
```

### Audio Processing

```yaml
rendering:
  audio:
    normalize: true
    target_loudness_lufs: -14.0       # Industry standard: -14 LUFS

    bgm:
      enabled: true
      library_path: "/mnt/pipeline/audio/bgm"
      volume_ducking_db: -20          # Duck BGM to -20dB under speech
      style_match: true               # Match B-roll style

      eq:
        high_pass_hz: 80              # Remove low rumble
        warmth_boost: true

      reverb:
        enabled: true
        preset: "subtle_studio"
```

**Target Loudness:**
- YouTube: -14 LUFS
- Spotify: -14 LUFS
- TikTok: -11 LUFS
- Instagram: -14 LUFS

### Branding

```yaml
rendering:
  branding:
    logo:
      enabled: true
      path: "/mnt/pipeline/assets/logo.png"
      position: "top-right"           # top-left, top-right, bottom-left, bottom-right
      opacity: 0.10                   # 0.0-1.0
      size_percent: 5                 # % of video width

    outro:
      enabled: true
      duration_seconds: 5
      template: "/mnt/pipeline/assets/outro_template.mp4"
      text: "Watch full stream on [channel_link]"
      clickable: true                 # Platform-specific
```

## Posting

### Platform Configuration

```yaml
posting:
  late:
    api_endpoint: "${LATE_API_ENDPOINT}"
    api_key: "${LATE_API_KEY}"
    timeout_seconds: 300

  platforms:
    enabled:
      - "youtube"
      - "x"
      - "tiktok"
      - "instagram"
      - "linkedin"
      - "facebook"
      - "reddit"
      - "pinterest"
      - "snapchat"
      - "twitch"
```

### Scheduling Strategy

```yaml
posting:
  scheduling:
    strategy: "staggered_rules_engine"

    immediate: true                   # Post ASAP after rendering
    spread_duration_days: 14          # Spread posts over 1-2 weeks

    respect_platform_limits: true
    respect_optimal_hours: true       # Post during peak engagement hours
    avoid_night_posts: true

    # Sequencing
    shorts_first: true                # Post shorts before longs
    longs_delay_hours: 24             # Post longs 24h after shorts

    max_concurrent_uploads: 5         # Max 5 simultaneous uploads
```

**Optimal Posting Hours (by platform):**
- YouTube: 2-4 PM weekdays
- TikTok: 6-10 PM all days
- Instagram: 11 AM-1 PM weekdays
- LinkedIn: 7-9 AM, 5-6 PM weekdays
- X (Twitter): 9 AM, 12 PM, 5 PM weekdays

### Analytics Tracking

```yaml
posting:
  analytics:
    enabled: true
    poll_interval_hours: 24           # Check analytics daily

    metrics:
      - "views"
      - "likes"
      - "comments"
      - "shares"
      - "ctr"
      - "avg_watch_time"
      - "retention"

    store_in_db: true
    table: "platform_posts"
```

## Archival

### Sources Configuration

```yaml
archival:
  sources:
    auto_upload: true
    upload_after: "posting_complete"  # After all extractions posted

    storage_class: "DEEP_ARCHIVE"
    lifecycle_policy: "perpetual"

    prefix_template: "sources/{year}/{month}/{stream_id}/"
```

### Extractions Configuration

```yaml
archival:
  extractions:
    auto_upload: true
    upload_after: "posted"

    storage_class: "GLACIER"
    lifecycle_policy:
      delete_after_days: 1825         # 5 years

    prefix_template: "extractions/{year}/{month}/{stream_id}/{extraction_id}/"
```

### Local Cleanup

```yaml
archival:
  local_cleanup:
    enabled: true
    cleanup_after: "s3_upload_verified"

    keep_local:
      - "metadata"                    # Always keep for search
      - "thumbnails"                  # Small files

    delete_local:
      - "source_videos"
      - "extraction_videos"
      - "intermediate_files"
      - "broll_cache"
```

## Monitoring

### Telegram Notifications

```yaml
monitoring:
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"

    notify_on:
      - "stream_ingested"
      - "transcription_complete"
      - "extractions_approved"
      - "rendering_complete"
      - "posting_complete"
      - "errors"
      - "analytics_update"

    inline_buttons: true              # Allow manual interventions
```

**Setup Telegram Bot:**
1. Create bot via @BotFather on Telegram
2. Get bot token
3. Send message to bot, get chat_id via `https://api.telegram.org/bot<TOKEN>/getUpdates`

### Prometheus Metrics

```yaml
monitoring:
  prometheus:
    enabled: true
    port: 9090

    metrics:
      - "streams_processed_total"
      - "extractions_generated_total"
      - "posts_published_total"
      - "processing_duration_seconds"
      - "llm_api_calls_total"
      - "gpu_utilization_percent"
      - "queue_depth"
```

### Health Checks

```yaml
monitoring:
  healthcheck:
    enabled: true
    endpoint: "/health"
    interval_seconds: 30
```

**Health Check Endpoints:**
- `/health/liveness`: Basic alive check
- `/health/readiness`: Dependency checks (DB, Redis, GPU)

## Performance Tuning

### GPU Optimization

**Transcription:**
```yaml
transcription:
  whisper:
    batch_size: 16                    # Increase for faster processing
    compute_type: "float16"           # Balance quality/speed
```

**Rendering:**
```yaml
rendering:
  ffmpeg:
    hardware_acceleration: "cuda"
    video_codec: "h264_nvenc"
```

### LLM Cost Optimization

```yaml
cost_optimization:
  llm:
    prefer_local: true                # Use local models when possible
    cache_responses: true
    cache_ttl_hours: 168              # 1 week

    use_haiku_for_simple_tasks: true
    use_sonnet_for_analysis: true
    avoid_opus_unless_critical: true
```

### Database Performance

```yaml
infrastructure:
  database:
    pool_size: 20                     # Increase for high concurrency
    max_overflow: 10
```

**Tuning:**
- Monitor active connections: `SELECT count(*) FROM pg_stat_activity;`
- Increase pool if hitting max connections
- Decrease if excessive idle connections

### Redis Performance

```yaml
infrastructure:
  redis:
    max_connections: 50
```

**Tuning:**
- Each worker uses ~5 connections
- Formula: `max_connections = (workers × 5) × 1.2`

## Configuration Validation

**Validate configuration:**
```bash
python -m pipeline.common.config --validate

# Or
python << 'EOF'
from pipeline.common.config import config
print("Database:", config.get('infrastructure.database.host'))
print("Redis:", config.get('infrastructure.redis.host'))
print("S3:", config.get('infrastructure.s3.bucket_sources'))
print("Config valid!")
EOF
```

## Example Configurations

### Small Deployment (Solo Node)

```yaml
infrastructure:
  mode: "local"
  database:
    pool_size: 10
  redis:
    max_connections: 25

transcription:
  whisper:
    batch_size: 8
    compute_type: "float16"

rendering:
  ffmpeg:
    threads: 4

cost_optimization:
  llm:
    prefer_local: true
```

### Large Deployment (Cluster)

```yaml
infrastructure:
  mode: "k8s"
  database:
    pool_size: 50
  redis:
    max_connections: 100

transcription:
  whisper:
    batch_size: 24
    compute_type: "float16"

rendering:
  ffmpeg:
    threads: 16

cost_optimization:
  llm:
    prefer_local: false
    cache_responses: true
```

## Next Steps

- **[Operations Guide](operations.md)** - Day-to-day operations
- **[Monitoring Guide](monitoring.md)** - Observability setup
- **[Troubleshooting Guide](troubleshooting.md)** - Problem resolution
