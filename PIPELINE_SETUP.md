# BBB Post-Stream Extraction Pipeline - Setup Guide

Complete setup guide for deploying the automated stream extraction system across your 4-node GPU cluster.

## Table of Contents

- [System Overview](#system-overview)
- [Hardware Requirements](#hardware-requirements)
- [Prerequisites](#prerequisites)
- [Phase 1 Setup](#phase-1-setup)
- [Service Deployment](#service-deployment)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

## System Overview

**Architecture**: Multi-node k3s cluster with GPU scheduling
**Storage**: PostgreSQL + Redis + S3 Deep Glacier
**Processing**: Whisper + pyannote + LLM cascade + ComfyUI
**Output**: 10 platforms via Late API

**Pipeline Flow**:
```
Upload (60-90 min) → Transcribe (30 min) → Analyze (20 min) →
Extract (planning) → Render (TBD) → Post (staggered) → Archive (auto)
```

## Hardware Requirements

### Node A (Master + Orchestration)
- **CPU**: Ryzen 1600
- **GPU**: 1x RTX 2080S (8GB VRAM)
- **RAM**: 32GB
- **Storage**: 1TB SATA SSD
- **Role**: k3s master, PostgreSQL, Redis, file watcher, upload coordinator

### Node B (Transcription + Audio)
- **CPU**: Ryzen 5900X/5950X
- **GPU**: 2x RTX 2080S (16GB VRAM total)
- **RAM**: 64GB
- **Storage**: 2TB NVMe
- **Role**: Whisper large-v3, pyannote, YOLOv8, audio processing

### Node C (Computer Vision + Remixing)
- **CPU**: Ryzen 5800X
- **GPU**: 2x RTX 2080S (16GB VRAM total)
- **RAM**: 64GB
- **Storage**: 2TB NVMe
- **Role**: OpenCV, lip sync, UI patterns, FFmpeg remixing

### Node D (AI Generation)
- **CPU**: High-end Ryzen
- **GPU**: RTX 3090 (24GB) + RTX 3080 (10GB)
- **RAM**: 64GB
- **Storage**: 2TB NVMe
- **Role**: SDXL B-roll, XTTS2 hooks, pgvector embeddings

## Prerequisites

### 1. Install Tailscale on All Nodes

```bash
# On each node (Ubuntu/Debian)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Verify connectivity
tailscale status
```

**Note your Tailscale hostnames** (e.g., `nodeA.tail-xxxxx.ts.net`)

### 2. Install k3s on Master (Node A)

```bash
# On Node A only
curl -sfL https://get.k3s.io | sh -s - server \
  --disable traefik \
  --write-kubeconfig-mode 644

# Get node token for workers
sudo cat /var/lib/rancher/k3s/server/node-token
```

### 3. Install k3s on Workers (Nodes B, C, D)

```bash
# On Nodes B, C, D - replace with your values
export K3S_URL=https://nodeA.tail-xxxxx.ts.net:6443
export K3S_TOKEN=<token_from_node_a>

curl -sfL https://get.k3s.io | sh -s - agent

# Verify nodes joined
kubectl get nodes
```

### 4. Install NVIDIA GPU Operator

```bash
# On master (Node A)
helm repo add nvidia https://nvidia.github.io/gpu-operator
helm repo update

helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator \
  --create-namespace \
  --set driver.enabled=true

# Verify GPU detection
kubectl get nodes -o json | jq '.items[].status.capacity'
```

### 5. Install Required Software on Each Node

#### All Nodes:
```bash
sudo apt update && sudo apt install -y \
  python3.11 python3.11-venv python3-pip \
  ffmpeg \
  git curl wget
```

#### Nodes with GPUs (B, C, D):
```bash
# Install NVIDIA drivers if not already installed
sudo apt install -y nvidia-driver-535

# Install CUDA toolkit
wget https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_530.30.02_linux.run
sudo sh cuda_12.1.0_530.30.02_linux.run --silent --toolkit

# Verify CUDA
nvidia-smi
nvcc --version
```

### 6. Create Pipeline User & Directories

```bash
# On all nodes
sudo useradd -m -s /bin/bash pipeline
sudo usermod -aG docker pipeline

# Create shared directories
sudo mkdir -p /mnt/pipeline/{work,models,config,db}
sudo chown -R pipeline:pipeline /mnt/pipeline
```

## Phase 1 Setup

### 1. Clone Repository

```bash
# On Node A
git clone https://github.com/designerpros/obs-cc.git
cd obs-cc
git checkout claude/post-stream-extraction-011CV2mMtt2yGLVjWG3qyALU
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit with your actual API keys and credentials
nano .env
```

**Required API Keys:**
- Anthropic (Claude)
- OpenAI (GPT-4o)
- Grok (xAI)
- Perplexity
- AssemblyAI (backup)
- Hugging Face (pyannote)
- Late API
- AWS S3 credentials
- Discord bot token

### 3. Update Configuration

```bash
# Edit pipeline config
nano config/pipeline.yaml

# Update Tailscale hostnames in infrastructure section:
infrastructure:
  tailscale:
    nodes:
      - name: "nodeA"
        hostname: "YOUR_ACTUAL_HOSTNAME.ts.net"
        gpus: ["rtx2080s-1"]
      - name: "nodeB"
        hostname: "YOUR_ACTUAL_HOSTNAME.ts.net"
        gpus: ["rtx2080s-2", "rtx2080s-3"]
      # ... etc
```

### 4. Deploy Infrastructure

```bash
# Create namespace
kubectl apply -f infrastructure/k8s/00-namespace.yaml

# Create secrets from .env file
kubectl create secret generic pipeline-secrets \
  --namespace=stream-pipeline \
  --from-env-file=.env

# Deploy PostgreSQL
kubectl apply -f infrastructure/k8s/02-postgres.yaml

# Wait for PostgreSQL to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n stream-pipeline --timeout=300s

# Initialize database schema
kubectl exec -n stream-pipeline -it deployment/postgres -- \
  psql -U pipeline_worker -d stream_extraction -f /docker-entrypoint-initdb.d/01_schema.sql

# Deploy Redis
kubectl apply -f infrastructure/k8s/03-redis.yaml

# Wait for Redis
kubectl wait --for=condition=ready pod -l app=redis -n stream-pipeline --timeout=300s
```

### 5. Build Docker Images

```bash
# Build base image
docker build -t stream-pipeline-base:latest -f infrastructure/docker/Dockerfile.base .

# Build service images
docker build -t stream-pipeline-ingestion:latest -f infrastructure/docker/Dockerfile.ingestion .
docker build -t stream-pipeline-transcription:latest -f infrastructure/docker/Dockerfile.transcription .

# Push to registry (if using remote nodes)
# docker tag stream-pipeline-base:latest your-registry/stream-pipeline-base:latest
# docker push your-registry/stream-pipeline-base:latest
# (repeat for all images)
```

### 6. Deploy Services

```bash
# Deploy ingestion service
kubectl apply -f infrastructure/k8s/04-ingestion.yaml

# Deploy transcription service
kubectl apply -f infrastructure/k8s/05-transcription.yaml

# Verify deployments
kubectl get pods -n stream-pipeline
```

## Service Deployment

### Ingestion Service (Node A)

Monitors `C:\stream_recordings\` for new files and triggers pipeline.

**Verify**:
```bash
kubectl logs -n stream-pipeline deployment/ingestion -f
```

**Test webhook**:
```bash
curl -X POST http://nodeA.tail-xxxxx.ts.net:30080/trigger \
  -H "Authorization: Bearer YOUR_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/obs-recordings/test-stream"}'
```

### Transcription Service (Node B)

Whisper large-v3 + pyannote speaker diarization.

**Verify GPU allocation**:
```bash
kubectl describe pod -n stream-pipeline -l app=transcription | grep -A5 "Limits:"
```

**Test transcription**:
```bash
kubectl exec -n stream-pipeline deployment/transcription -- \
  python3 -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
```

## Testing

### 1. Test Pipeline End-to-End

```bash
# On your local machine (Windows)
# 1. Copy test stream files to C:\stream_recordings\test-2024-11-12\
#    - cam_main_me.mp4
#    - cam_overhead.mp4
#    - cam_screen.mp4
#    - live_mix.mp4
#    - .context_cache.json

# 2. Send Discord/Telegram command:
#    /process latest

# 3. Monitor progress via notifications or logs:
kubectl logs -n stream-pipeline deployment/ingestion -f
```

### 2. Monitor Jobs

```bash
# Check job queue
kubectl exec -n stream-pipeline deployment/redis -- \
  redis-cli -a $REDIS_PASSWORD ZRANGE queue:transcribe 0 -1 WITHSCORES

# Check database
kubectl exec -n stream-pipeline deployment/postgres -- \
  psql -U pipeline_worker -d stream_extraction -c \
  "SELECT id, status, created_at FROM streams ORDER BY created_at DESC LIMIT 5;"
```

### 3. Check Logs

```bash
# All pods in namespace
kubectl logs -n stream-pipeline --all-containers=true --tail=100

# Specific service
kubectl logs -n stream-pipeline deployment/transcription -f
```

## Troubleshooting

### Pipeline Not Starting

**Check file watcher**:
```bash
kubectl exec -n stream-pipeline deployment/ingestion -- \
  ls -la /mnt/obs-recordings/trigger
```

**Check permissions**:
```bash
kubectl exec -n stream-pipeline deployment/ingestion -- \
  touch /mnt/obs-recordings/trigger/test.txt
```

### Transcription Failing

**Check GPU availability**:
```bash
kubectl get nodes -o json | jq '.items[].status.allocatable'
```

**Check Whisper model**:
```bash
kubectl exec -n stream-pipeline deployment/transcription -- \
  ls -la /root/.cache/huggingface/hub
```

**Check pyannote auth**:
```bash
kubectl exec -n stream-pipeline deployment/transcription -- \
  python3 -c "from pyannote.audio import Pipeline; print('Pyannote OK')"
```

### Out of Memory

**Scale down replicas**:
```bash
kubectl scale deployment/transcription -n stream-pipeline --replicas=1
```

**Check resource limits**:
```bash
kubectl describe pod -n stream-pipeline -l app=transcription
```

### Network Issues

**Test Tailscale connectivity**:
```bash
# From Node A
ping nodeB.tail-xxxxx.ts.net
```

**Test pod networking**:
```bash
kubectl exec -n stream-pipeline deployment/ingestion -- \
  ping postgres
```

### Database Connection Issues

**Test connection**:
```bash
kubectl exec -n stream-pipeline deployment/postgres -- \
  pg_isready -U pipeline_worker
```

**Check credentials**:
```bash
kubectl get secret -n stream-pipeline pipeline-secrets -o yaml
```

## Next Steps

After Phase 1 is working:

1. **Phase 2**: Add virality engine (hooks, thumbnails, trend-jacking)
2. **Phase 3**: Add multi-camera remixing (CV + audio switching)
3. **Phase 4**: Add B-roll library reuse
4. **Phase 5**: Add thumbnail A/B testing
5. **Phase 6**: Add OBS auto-switching plugin
6. **Phase 7**: Add analytics feedback loop

## Support

**Issues**: Create GitHub issue with logs
**Logs**: Include output from `kubectl logs` and service status
**GPU**: Include `nvidia-smi` output

## Performance Targets

- **Upload**: 60-90 minutes (acceptable for 60GB)
- **Transcription**: ~30 minutes for 60-minute stream
- **Analysis**: ~20 minutes (LLM cascade)
- **Total**: 3-5 hours for complete pipeline
- **Cost**: $15-25 per stream (LLM cascade optimization)

## Security Notes

- Never commit `.env` file
- Rotate API keys every 90 days
- Use strong passwords for PostgreSQL/Redis
- Restrict webhook endpoints to Tailscale network
- Enable S3 encryption at rest (AES256)
