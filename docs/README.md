# BBB Post-Stream Extraction Pipeline - Documentation

Comprehensive documentation for deploying, configuring, and operating the BBB post-stream extraction pipeline.

## Overview

The BBB post-stream extraction pipeline is a production-grade system for automatically extracting, analyzing, rendering, and posting viral clips from 1-hour BiggerBetterBraver livestreams with 4-8 multicam sources.

**Pipeline Phases:**
1. **Ingestion** - Smart file detection and validation
2. **Transcription** - GPU-accelerated Whisper transcription
3. **Analysis** - LLM-powered topic scoring and coherence detection
4. **Rendering** - FFMPEG multicam composition with AI-generated thumbnails
5. **Posting** - Multi-platform social media scheduling via Late API
6. **Archival** - S3/Glacier long-term storage

## Documentation Index

### Setup & Installation
- **[Architecture Overview](architecture.md)** - System design, components, and data flow
- **[Distributed Cluster Setup](cluster-setup.md)** - Kubernetes deployment on multi-node cluster
- **[Solo Node Setup](solo-setup.md)** - Single-server deployment for testing or small-scale use
- **[Configuration Guide](configuration.md)** - Complete reference for config.yaml and environment variables

### Operations & Maintenance
- **[Operations Guide](operations.md)** - Day-to-day operations, monitoring, and maintenance
- **[Troubleshooting Guide](troubleshooting.md)** - Common issues and solutions
- **[Monitoring & Observability](monitoring.md)** - Metrics, logs, alerts, and dashboards

### Reference
- **[API Reference](api-reference.md)** - Internal APIs and job queue structure
- **[Database Schema](database-schema.md)** - PostgreSQL tables and relationships
- **[Cost Analysis](cost-analysis.md)** - LLM cascade costs and optimization strategies

## Quick Start

### Prerequisites
- Kubernetes 1.28+ (distributed) or Docker + Python 3.11 (solo node)
- PostgreSQL 16+
- Redis 7+
- NVIDIA GPU with 8GB+ VRAM (for transcription)
- S3-compatible storage (AWS S3, Backblaze B2, etc.)

### Distributed Cluster (Recommended for Production)
```bash
# 1. Clone repository
git clone https://github.com/designerpros/obs-cc.git
cd obs-cc

# 2. Configure environment
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your settings

# 3. Deploy infrastructure
kubectl apply -f infrastructure/k8s/00-namespace.yaml
kubectl apply -f infrastructure/k8s/01-postgres.yaml
kubectl apply -f infrastructure/k8s/02-redis.yaml

# 4. Deploy pipeline services
kubectl apply -f infrastructure/k8s/03-shared.yaml
kubectl apply -f infrastructure/k8s/04-ingestion.yaml
kubectl apply -f infrastructure/k8s/05-transcription.yaml
kubectl apply -f infrastructure/k8s/06-analysis.yaml
kubectl apply -f infrastructure/k8s/07-rendering.yaml
kubectl apply -f infrastructure/k8s/08-posting.yaml
kubectl apply -f infrastructure/k8s/09-archival.yaml

# 5. Verify deployment
kubectl get pods -n bbb-pipeline
```

See **[Cluster Setup Guide](cluster-setup.md)** for detailed instructions.

### Solo Node (For Testing/Development)
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start infrastructure
docker-compose up -d postgres redis

# 3. Configure environment
cp config/config.example.yaml config/config.yaml
# Edit config with your settings

# 4. Run workers (separate terminals)
python -m pipeline.ingestion.worker
python -m pipeline.transcription.worker
python -m pipeline.analysis.worker
python -m pipeline.rendering.worker
python -m pipeline.posting.worker
python -m pipeline.archival.worker
```

See **[Solo Node Setup Guide](solo-setup.md)** for detailed instructions.

## System Requirements

### Distributed Cluster
- **Control Plane:** 3 nodes, 4 CPU, 8GB RAM each
- **Worker Nodes:**
  - 3+ CPU-only nodes: 8 CPU, 16GB RAM each
  - 1+ GPU nodes: 8 CPU, 32GB RAM, NVIDIA T4/A10/A100
- **Storage:** 1TB+ SSD for working directory, S3 for archival
- **Network:** 10Gbps+ internal, 1Gbps+ external

### Solo Node
- **CPU:** 16+ cores (32+ recommended)
- **RAM:** 64GB+ (128GB recommended)
- **GPU:** NVIDIA GPU with 8GB+ VRAM (T4, RTX 3090, A10, etc.)
- **Storage:** 2TB+ NVMe SSD
- **Network:** 1Gbps+ connection

## Architecture Highlights

### Job Queue System
- Redis-based priority queue with sorted sets
- Automatic retries with exponential backoff
- Circuit breaker pattern for external API failures
- Dead letter queue for failed jobs

### LLM Cascade Optimization
- Stage 1: Local Llama 8B pre-filter (FREE)
- Stage 2: Grok cheap screening ($0.01/call)
- Stage 3: Claude mid-tier validation ($0.015/call)
- Stage 4: GPT-4o + Perplexity consensus ($0.06/call combined)
- 95% cost reduction vs. all-GPT-4o approach

### Storage Tiers
- **Hot:** Local NVMe (working files, active jobs)
- **Warm:** S3 Standard (recent extractions)
- **Cold:** S3 Glacier (5-year retention for extractions)
- **Archive:** S3 Deep Glacier (perpetual retention for sources)

## Support & Contribution

### Getting Help
1. Check the **[Troubleshooting Guide](troubleshooting.md)**
2. Review logs: `kubectl logs -n bbb-pipeline <pod-name>`
3. Open GitHub issue with logs and reproduction steps

### Contributing
Contributions welcome! Please:
1. Follow existing code style and patterns
2. Add tests for new features
3. Update documentation
4. Submit PR with clear description

## License

Proprietary - DesignerPros © 2025
