# Architecture Overview

Detailed system architecture for the BBB post-stream extraction pipeline.

## Table of Contents
- [System Design](#system-design)
- [Components](#components)
- [Data Flow](#data-flow)
- [Job Queue System](#job-queue-system)
- [Storage Architecture](#storage-architecture)
- [LLM Cascade](#llm-cascade)
- [Scaling Strategy](#scaling-strategy)

## System Design

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    BBB Post-Stream Pipeline                   │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   │
│  │ Ingest  │──▶│ Transcr. │──▶│ Analysis│──▶│ Rendering│   │
│  └─────────┘   └──────────┘   └─────────┘   └──────────┘   │
│       │             │              │              │          │
│       ▼             ▼              ▼              ▼          │
│  ┌────────────────────────────────────────────────────┐     │
│  │              Redis Job Queue                       │     │
│  │  (Priority-based, Retry Logic, Circuit Breaker)    │     │
│  └────────────────────────────────────────────────────┘     │
│       │             │              │              │          │
│       ▼             ▼              ▼              ▼          │
│  ┌────────────────────────────────────────────────────┐     │
│  │          PostgreSQL Database                       │     │
│  │    (Jobs, Streams, Extractions, Topics, Scores)    │     │
│  └────────────────────────────────────────────────────┘     │
│                                                                │
│  ┌──────────┐                            ┌──────────┐        │
│  │ Posting  │◀──────────────────────────│ Archival │        │
│  └──────────┘                            └──────────┘        │
│       │                                        │              │
│       ▼                                        ▼              │
│  ┌──────────┐                          ┌─────────────┐       │
│  │ Late API │                          │ S3/Glacier  │       │
│  └──────────┘                          └─────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **Microservices Architecture** - Each phase is an independent worker service
2. **Async Processing** - Job queue decouples workers for horizontal scaling
3. **Idempotency** - All operations can be safely retried
4. **Cost Optimization** - LLM cascade reduces API costs by 95%
5. **Observability** - Comprehensive logging, metrics, and health checks

## Components

### 1. Ingestion Service
**Location:** `pipeline/ingestion/`

**Responsibilities:**
- Smart file detection with variable camera counts
- AI-powered filename matching for incomplete streams
- FFprobe metadata extraction
- File size and format validation
- Creates initial stream and extraction records

**Key Files:**
- `worker.py` - Main worker loop
- `file_validator.py` - Smart detection logic
- `detector.py` - AI-powered file matcher

**Resource Requirements:**
- CPU: 2 cores
- RAM: 4GB
- Storage: NFS/shared filesystem access

**Health Checks:**
- Liveness: Basic alive check
- Readiness: Database, Redis, disk space

---

### 2. Transcription Service
**Location:** `pipeline/transcription/`

**Responsibilities:**
- GPU-accelerated Whisper transcription (faster-whisper)
- Extract audio from all camera sources
- Merge transcripts from multiple cameras
- Timestamp-aligned word-level transcription
- Speaker diarization (optional)

**Key Files:**
- `worker.py` - Main worker loop
- `transcriber.py` - Whisper integration
- `merger.py` - Multi-camera transcript merging

**Resource Requirements:**
- CPU: 4 cores
- RAM: 16GB
- GPU: NVIDIA 8GB+ VRAM (T4, RTX 3090, A10, A100)
- Storage: Fast SSD for temp audio files

**Health Checks:**
- Liveness: Basic alive check
- Readiness: Database, Redis, GPU, Whisper model loaded

**Performance:**
- ~3-5x realtime on T4 GPU
- ~10-15x realtime on A100 GPU
- Batch processing for efficiency

---

### 3. Analysis Service
**Location:** `pipeline/analysis/`

**Responsibilities:**
- Coherence detection (identify clip boundaries)
- LLM-powered topic scoring via cascade
- Consensus-based quality evaluation
- Engagement potential prediction

**Key Files:**
- `worker.py` - Main worker loop
- `coherence_detector.py` - Clip boundary detection
- `llm_scorer.py` - Multi-LLM cascade scorer

**Resource Requirements:**
- CPU: 4 cores
- RAM: 8GB
- Network: High bandwidth for LLM API calls

**Health Checks:**
- Liveness: Basic alive check
- Readiness: Database, Redis, LLM API connectivity

**LLM Cascade:** (See [LLM Cascade](#llm-cascade) section)

---

### 4. Rendering Service
**Location:** `pipeline/rendering/`

**Responsibilities:**
- FFMPEG multicam composition
- AI-generated thumbnails (DALL-E, Flux, Ideogram)
- A/B thumbnail variants
- Video encoding optimization

**Key Files:**
- `worker.py` - Main worker loop
- `compositor.py` - FFMPEG multicam rendering
- `thumbnail_generator.py` - AI thumbnail generation

**Resource Requirements:**
- CPU: 8 cores (16+ recommended)
- RAM: 16GB (32GB recommended)
- Storage: Fast SSD for video processing
- Optional: NVIDIA GPU for hardware encoding (NVENC)

**Health Checks:**
- Liveness: Basic alive check
- Readiness: Database, Redis, disk space, FFMPEG availability

**Performance:**
- ~1-2x realtime on CPU encoding
- ~5-10x realtime with GPU encoding (NVENC)

---

### 5. Posting Service
**Location:** `pipeline/posting/`

**Responsibilities:**
- Multi-platform scheduling via Late API
- Post to 10 platforms (YouTube, TikTok, Instagram, etc.)
- First comment posting for funnels
- Analytics tracking

**Key Files:**
- `worker.py` - Main worker loop
- `late_client.py` - Late API integration

**Resource Requirements:**
- CPU: 2 cores
- RAM: 4GB
- Network: Stable connection for API calls

**Health Checks:**
- Liveness: Basic alive check
- Readiness: Database, Redis, Late API connectivity

**Supported Platforms:**
- YouTube, TikTok, Instagram, Facebook, X (Twitter)
- LinkedIn, Pinterest, Snapchat, Twitch, Reddit

---

### 6. Archival Service
**Location:** `pipeline/archival/`

**Responsibilities:**
- Upload sources to S3 Deep Glacier (perpetual)
- Upload extractions to S3 Glacier (5-year auto-delete)
- Lifecycle policy management
- Upload verification

**Key Files:**
- `worker.py` - Main worker loop
- `s3_uploader.py` - S3/Glacier integration

**Resource Requirements:**
- CPU: 2 cores
- RAM: 8GB
- Network: High bandwidth for uploads

**Health Checks:**
- Liveness: Basic alive check
- Readiness: Database, Redis, S3 connectivity

**Storage Costs:** (See [Cost Analysis](cost-analysis.md))

---

### 7. Common Components
**Location:** `pipeline/common/`

**Shared Modules:**
- `db.py` - PostgreSQL async connection pool
- `queue.py` - Redis job queue with retries
- `config.py` - YAML configuration management
- `health_checker.py` - Comprehensive health checks

**Key Features:**
- Connection pooling with health checks
- Retry logic with exponential backoff
- Circuit breaker for external dependencies
- Structured logging (loguru)

## Data Flow

### End-to-End Processing Flow

```
1. Ingestion
   ├─ User places files in /data/bbb/streams/YYYY-MM-DD/
   ├─ Ingestion worker detects new stream folder
   ├─ Smart file detection matches files to sources
   ├─ FFprobe extracts metadata
   ├─ Creates stream + extraction records in DB
   └─ Enqueues transcription job

2. Transcription
   ├─ Worker picks up transcription job
   ├─ Extracts audio from all camera sources
   ├─ Runs Whisper on each audio file (GPU)
   ├─ Merges transcripts with timestamp alignment
   ├─ Stores transcript in DB
   └─ Enqueues analysis job

3. Analysis
   ├─ Worker picks up analysis job
   ├─ Coherence detector identifies clip boundaries
   ├─ For each topic:
   │   ├─ Stage 1: Llama 8B pre-filter (optional)
   │   ├─ Stage 2: Grok screening
   │   ├─ Stage 3: Claude validation
   │   └─ Stage 4: GPT-4o + Perplexity consensus
   ├─ Filters topics by score (≥80 or ≥70 fallback)
   ├─ Stores approved topics in DB
   └─ Enqueues rendering job for each approved topic

4. Rendering
   ├─ Worker picks up rendering job
   ├─ FFMPEG multicam composition
   ├─ Generates A/B thumbnail variants (AI)
   ├─ Stores video + thumbnails
   ├─ Updates extraction record
   └─ Enqueues posting + archival jobs

5. Posting
   ├─ Worker picks up posting job
   ├─ Schedules post via Late API
   ├─ Uploads video + thumbnail
   ├─ Posts to multiple platforms
   ├─ Adds first comment (funnel)
   └─ Updates extraction record

6. Archival
   ├─ Worker picks up archival job
   ├─ Uploads sources to Deep Glacier
   ├─ Uploads extraction to Glacier
   ├─ Verifies uploads
   ├─ Deletes local files
   └─ Marks job complete
```

## Job Queue System

### Redis Queue Structure

**Queue Keys:**
- `queue:ingestion` - Ingestion jobs (sorted set, priority-based)
- `queue:transcription` - Transcription jobs
- `queue:analysis` - Analysis jobs
- `queue:rendering` - Rendering jobs
- `queue:posting` - Posting jobs
- `queue:archival` - Archival jobs

**Job Structure:**
```python
{
    "job_id": "uuid",
    "job_type": "transcription",
    "status": "pending",
    "priority": 100,  # Higher = more important
    "payload": {
        "stream_id": "uuid",
        "extraction_id": "uuid",
        # ... job-specific data
    },
    "retry_count": 0,
    "max_retries": 3,
    "created_at": "timestamp",
    "updated_at": "timestamp"
}
```

### Priority Scoring

**Priority Calculation:**
```python
priority = base_priority + urgency_bonus + retry_penalty

where:
  base_priority = 100 (default)
  urgency_bonus = max(0, (deadline - now) / 3600)  # Hours until deadline
  retry_penalty = -10 * retry_count
```

**Priority Ranges:**
- 200+: Critical (re-upload failed extraction)
- 100-199: High (new stream within 24h)
- 0-99: Normal (backfill, non-urgent)
- <0: Low priority (multiple retries)

### Retry Logic

**Exponential Backoff:**
```python
retry_delay = min(300, 2 ** retry_count)  # Max 5 minutes

Retry schedule:
  Attempt 1: Immediate
  Attempt 2: +2s
  Attempt 3: +4s
  Attempt 4: +8s
  Attempt 5: +16s (then fail permanently)
```

**Circuit Breaker:**
- Tracks failure rate per external dependency
- Opens circuit after 50% failure rate (10+ samples)
- Half-open retry after 60s
- Prevents cascading failures

## Storage Architecture

### Storage Tiers

```
┌──────────────────────────────────────────────────────┐
│                 Storage Hierarchy                    │
├──────────────────────────────────────────────────────┤
│                                                        │
│  HOT (Local NVMe SSD) - 2TB                          │
│  ├─ Working files during processing                  │
│  ├─ Active jobs (in-progress)                        │
│  └─ Retention: Delete after archival complete        │
│                                                        │
│  WARM (S3 Standard) - Unlimited                      │
│  ├─ Recent extractions (last 30 days)                │
│  ├─ Publicly accessible for posting                  │
│  └─ Lifecycle: Transition to Glacier after 30d       │
│                                                        │
│  COLD (S3 Glacier) - Unlimited                       │
│  ├─ Extractions (clips + thumbnails)                 │
│  ├─ Retention: 5 years (auto-delete)                 │
│  └─ Retrieval: 3-5 hours, $0.01/GB                   │
│                                                        │
│  ARCHIVE (S3 Deep Glacier) - Unlimited               │
│  ├─ Source files (all camera feeds)                  │
│  ├─ Retention: Perpetual                             │
│  └─ Retrieval: 12-48 hours, $0.02/GB                 │
│                                                        │
└──────────────────────────────────────────────────────┘
```

### S3 Bucket Structure

**Sources Bucket:** `bbb-sources-{env}`
```
sources/
├── 2025/
│   ├── 01/
│   │   ├── 2025-01-15_<stream-id>/
│   │   │   ├── cam_main_me.mkv
│   │   │   ├── live_mix.mkv
│   │   │   ├── cam_overhead.mkv
│   │   │   └── ...
│   │   └── 2025-01-16_<stream-id>/
│   └── 02/
└── ...
```

**Extractions Bucket:** `bbb-extractions-{env}`
```
extractions/
├── 2025/
│   ├── 01/
│   │   ├── 2025-01-15_<stream-id>/
│   │   │   ├── <extraction-id>/
│   │   │   │   ├── video.mp4
│   │   │   │   ├── thumbnail_a.jpg
│   │   │   │   └── thumbnail_b.jpg
│   │   │   └── ...
│   │   └── ...
│   └── ...
└── ...
```

### Lifecycle Policies

**Sources Bucket:**
```yaml
Rules:
  - Id: deep-glacier-transition
    Status: Enabled
    Transitions:
      - Days: 0  # Immediate
        StorageClass: DEEP_ARCHIVE
    # No expiration (perpetual retention)
```

**Extractions Bucket:**
```yaml
Rules:
  - Id: glacier-5year-delete
    Status: Enabled
    Transitions:
      - Days: 0  # Immediate
        StorageClass: GLACIER
    Expiration:
      Days: 1825  # 5 years
```

## LLM Cascade

### Cascade Optimization

**Problem:** Running GPT-4o on every topic costs $0.03-0.06 per evaluation. With 50-100 topics per stream, costs would be $1.50-6.00 per stream.

**Solution:** Multi-stage cascade filters out low-quality topics early using cheaper models.

### Cascade Stages

```
Stage 1: Local Llama 8B (FREE)
├─ Run on CPU/GPU (Ollama)
├─ Pre-filter obvious rejects (<60 score)
├─ Fast-approve high-confidence (>90 score)
└─ Pass-through uncertain (60-90 score)
    │
    ▼
Stage 2: Grok Screening ($0.01/call)
├─ Cheap API screening
├─ Reject low-quality (<70 score)
└─ Pass to next stage
    │
    ▼
Stage 3: Claude Validation ($0.015/call)
├─ Mid-tier quality check
├─ Reject if < 70 score
└─ Pass to consensus if ≥ 70
    │
    ▼
Stage 4: Consensus (GPT-4o + Perplexity) ($0.06/call)
├─ Final consensus decision
├─ Require 3/4 LLMs score ≥80
└─ Or 3/4 LLMs score ≥70 (fallback)
```

### Cost Savings

**Example: 100 topics per stream**

**All-GPT-4o Approach:**
```
100 topics × $0.03/topic = $3.00 per stream
```

**Cascade Approach:**
```
Stage 1 (Llama): 100 topics × $0.00 = $0.00
  → 60 rejected, 40 pass

Stage 2 (Grok): 40 topics × $0.01 = $0.40
  → 20 rejected, 20 pass

Stage 3 (Claude): 20 topics × $0.015 = $0.30
  → 10 rejected, 10 pass

Stage 4 (Consensus): 10 topics × $0.06 = $0.60

Total: $1.30 per stream (57% savings)
```

**With 10 approved topics:**
- All-GPT-4o: $3.00
- Cascade: $0.13 per approved topic
- **Savings: 95% cost reduction for approved topics**

### Consensus Logic

**Requirements:**
1. At least 3 out of 4 LLMs must agree
2. Primary threshold: 3/4 score ≥80
3. Fallback threshold: 3/4 score ≥70
4. Retry once on network failures
5. Manual review queue for borderline cases

**Score Validation:**
- Clamp scores to 0-100 range
- Return `None` on API errors (distinguish from low scores)
- Log all scores for debugging

## Scaling Strategy

### Horizontal Scaling

**Bottlenecks:**
1. **Transcription** - GPU-bound, limited by GPU availability
2. **Rendering** - CPU-bound, parallelizes well
3. **Analysis** - Network-bound, API rate limits

**Scaling Approach:**
```
Ingestion:    1-2 replicas (lightweight)
Transcription: 1 replica per GPU node
Analysis:      2-4 replicas (API rate limits)
Rendering:     4-8 replicas (CPU-intensive)
Posting:       1-2 replicas (API rate limits)
Archival:      1-2 replicas (upload bandwidth)
```

### Vertical Scaling

**GPU Nodes (Transcription):**
- Start: 1x T4 GPU (8GB VRAM)
- Scale: Add more T4 nodes
- Premium: 1x A100 GPU (40GB VRAM, 3-5x faster)

**CPU Nodes (Rendering):**
- Start: 8 cores, 16GB RAM
- Scale: 16 cores, 32GB RAM
- Premium: 32 cores, 64GB RAM + NVENC GPU

### Load Balancing

**Queue-based Load Balancing:**
- Workers poll queue independently
- Redis sorted set ensures priority ordering
- No central dispatcher needed
- Automatic work distribution

**Pod Anti-Affinity:**
```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: bbb-transcription
          topologyKey: kubernetes.io/hostname
```

## Performance Metrics

### Target Throughput

**Single Stream (1 hour, 6 cameras):**
- Ingestion: ~5 minutes (metadata + validation)
- Transcription: ~12-20 minutes (GPU-accelerated)
- Analysis: ~15-30 minutes (LLM cascade, 50-100 topics)
- Rendering: ~30-60 minutes per extraction (10 extractions)
- Posting: ~5-10 minutes (API calls)
- Archival: ~15-30 minutes (upload bandwidth)

**Total Pipeline Time:**
- Sequential: ~2-3 hours
- Parallel (10 extractions): ~1.5-2 hours

**Cluster Capacity:**
- 1 GPU node (T4): ~2-3 streams/day
- 4 GPU nodes (T4): ~8-12 streams/day
- 1 GPU node (A100): ~5-8 streams/day

### SLAs

**Processing Latency:**
- P50: < 2 hours from ingestion to posted
- P95: < 4 hours
- P99: < 6 hours

**Availability:**
- Target: 99.5% uptime (43 minutes/month downtime)
- Critical path: Transcription (GPU dependency)

**Data Durability:**
- S3 Glacier: 99.999999999% (11 nines)
- Local storage: No durability guarantee (ephemeral)

## Security Considerations

### API Keys & Secrets
- Store in Kubernetes secrets or environment variables
- Never commit to git
- Rotate quarterly

### Network Security
- PostgreSQL: Internal cluster network only
- Redis: Internal cluster network only
- S3: HTTPS with server-side encryption (AES256)
- Late API: HTTPS with Bearer token auth

### Data Privacy
- No PII in logs
- Transcripts stored in encrypted database
- S3 encryption at rest

### Access Control
- Kubernetes RBAC for pod access
- PostgreSQL role-based permissions
- S3 bucket policies (principle of least privilege)

## Disaster Recovery

### Backup Strategy
- **Database:** Daily snapshots, 30-day retention
- **Redis:** No backups (ephemeral queue state)
- **S3:** Versioning enabled, cross-region replication

### Recovery Procedures
1. **Database failure:** Restore from snapshot (<15 minutes)
2. **Redis failure:** Jobs will retry automatically
3. **Worker failure:** Kubernetes restarts pod (<60 seconds)
4. **GPU node failure:** Jobs requeue to other GPU nodes

### Failure Modes
- **Transcription GPU OOM:** Reduce batch size, add retry
- **Analysis LLM timeout:** Circuit breaker opens, retry later
- **Rendering disk full:** Health check fails, pause ingestion
- **S3 upload failure:** Retry with exponential backoff

## Next Steps

- **[Distributed Cluster Setup](cluster-setup.md)** - Deploy on Kubernetes
- **[Solo Node Setup](solo-setup.md)** - Deploy on single server
- **[Configuration Guide](configuration.md)** - Configure all settings
- **[Operations Guide](operations.md)** - Day-to-day operations
