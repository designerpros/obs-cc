# Solo Node Setup Guide

Complete guide for deploying the BBB post-stream extraction pipeline on a single server.

## Table of Contents
- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running Workers](#running-workers)
- [Testing](#testing)
- [Production Considerations](#production-considerations)
- [Troubleshooting](#troubleshooting)

## Overview

Solo node deployment runs all pipeline workers on a single server. This is ideal for:
- **Testing and development**
- **Small-scale production** (<5 streams/day)
- **Cost-sensitive deployments**
- **Environments without Kubernetes**

**Limitations:**
- Single point of failure (no high availability)
- Limited scalability (bounded by single server resources)
- Manual process management (use systemd or supervisor)

## Prerequisites

### Required Software
- **Python 3.11+** (3.12 recommended)
- **Docker 24+** (for infrastructure services)
- **Docker Compose** 2.20+
- **NVIDIA Driver** 535+ (for GPU transcription)
- **NVIDIA Docker Runtime** (nvidia-docker2)
- **FFMPEG** 6.0+ with GPU support
- **Git**

### Required Services
- **PostgreSQL 16+** (via Docker)
- **Redis 7+** (via Docker)
- **S3-compatible storage** (AWS S3, Backblaze B2, etc.)

### API Keys Required
- **Anthropic API key** (Claude)
- **OpenAI API key** (GPT-4o)
- **Grok API key** (X.AI)
- **Perplexity API key**
- **Late API key** (multi-platform posting)
- **Ideogram API key** (thumbnail generation)

## System Requirements

### Minimum Specifications
```yaml
CPU: 16 cores (Intel Xeon, AMD EPYC, or similar)
RAM: 64 GB
GPU: NVIDIA GPU with 8GB+ VRAM (T4, RTX 3090, A10, etc.)
Storage: 2 TB NVMe SSD
Network: 1 Gbps connection
OS: Ubuntu 22.04 LTS
```

### Recommended Specifications
```yaml
CPU: 32 cores (Threadripper, EPYC, or similar)
RAM: 128 GB
GPU: NVIDIA RTX 4090, A10, or A100 (24GB+ VRAM)
Storage: 4 TB NVMe SSD (RAID 0 for performance)
Network: 10 Gbps connection
OS: Ubuntu 22.04 LTS
```

### Storage Layout
```
/data/bbb/
├── streams/          # Input stream files (2 TB)
├── working/          # Temp files during processing (500 GB)
├── extractions/      # Output clips (500 GB, cleared after upload)
└── logs/             # Application logs (10 GB)
```

## Installation

### 1. System Preparation

**Update system:**
```bash
sudo apt-get update
sudo apt-get upgrade -y
```

**Install prerequisites:**
```bash
sudo apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    build-essential \
    git \
    curl \
    wget \
    ffmpeg \
    nvidia-driver-535 \
    nvidia-docker2
```

**Verify NVIDIA GPU:**
```bash
nvidia-smi
```

### 2. Install Docker & Docker Compose

```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.23.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Verify
docker --version
docker-compose --version
```

**Configure NVIDIA Docker Runtime:**
```bash
# Edit /etc/docker/daemon.json
sudo nano /etc/docker/daemon.json
```

Add:
```json
{
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  },
  "default-runtime": "nvidia"
}
```

Restart Docker:
```bash
sudo systemctl restart docker
```

Test NVIDIA Docker:
```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

### 3. Clone Repository

```bash
cd /opt
sudo git clone https://github.com/designerpros/obs-cc.git
sudo chown -R $USER:$USER obs-cc
cd obs-cc
```

### 4. Create Python Virtual Environment

```bash
python3.11 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel
```

### 5. Install Python Dependencies

```bash
pip install -r requirements.txt
```

**If requirements.txt doesn't exist, install manually:**
```bash
pip install \
    asyncpg \
    redis \
    httpx \
    anthropic \
    openai \
    boto3 \
    loguru \
    pyyaml \
    faster-whisper \
    torch \
    torchvision \
    numpy \
    pillow
```

### 6. Set Up Storage Directories

```bash
sudo mkdir -p /data/bbb/{streams,working,extractions,logs}
sudo chown -R $USER:$USER /data/bbb
chmod -R 755 /data/bbb
```

### 7. Deploy Infrastructure (Docker Compose)

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:16-alpine
    container_name: bbb-postgres
    environment:
      POSTGRES_DB: bbb_pipeline
      POSTGRES_USER: bbb_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bbb_user"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: bbb-redis
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres-data:
  redis-data:
```

**Start infrastructure:**
```bash
docker-compose up -d

# Verify
docker-compose ps
docker-compose logs -f
```

### 8. Initialize Database

```bash
# Activate virtual environment
source venv/bin/activate

# Set DATABASE_URL
export DATABASE_URL="postgresql+asyncpg://bbb_user:changeme@localhost:5432/bbb_pipeline"

# Run migrations (if using Alembic)
alembic upgrade head

# Or create tables manually
python scripts/init_db.py
```

**If no migration script exists, create tables:**

```bash
# Create init_db.py
cat > scripts/init_db.py << 'EOF'
import asyncio
from pipeline.common.db import engine, Base
from pipeline.ingestion.models import Stream
from pipeline.analysis.models import Topic, Extraction

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database initialized!")

if __name__ == "__main__":
    asyncio.run(init_db())
EOF

python scripts/init_db.py
```

## Configuration

### 1. Create Configuration File

```bash
cp config/config.example.yaml config/config.yaml
nano config/config.yaml
```

### 2. Configure Settings

**config.yaml:**
```yaml
# Database
database:
  url: "postgresql+asyncpg://bbb_user:changeme@localhost:5432/bbb_pipeline"
  pool_size: 20
  max_overflow: 10

# Redis
redis:
  url: "redis://localhost:6379/0"

# Storage
storage:
  streams_dir: "/data/bbb/streams"
  working_dir: "/data/bbb/working"
  extractions_dir: "/data/bbb/extractions"

# Infrastructure
infrastructure:
  s3:
    endpoint: "s3.amazonaws.com"
    region: "us-east-1"
    access_key: "${S3_ACCESS_KEY}"
    secret_key: "${S3_SECRET_KEY}"
    bucket_sources: "bbb-sources-prod"
    bucket_extractions: "bbb-extractions-prod"

# LLM Configuration
analysis:
  coherence:
    llm_providers:
      - name: "anthropic"
        api_key: "${ANTHROPIC_API_KEY}"
        model: "claude-3-5-sonnet-20241022"
      - name: "openai"
        api_key: "${OPENAI_API_KEY}"
        model: "gpt-4o"
      - name: "grok"
        api_key: "${GROK_API_KEY}"
        model: "grok-2-1212"
      - name: "perplexity"
        api_key: "${PERPLEXITY_API_KEY}"
        model: "llama-3.1-sonar-large-128k-online"
    consensus_threshold: 0.75
    min_score_per_llm: 80

# Transcription
transcription:
  model: "large-v3"
  device: "cuda"
  compute_type: "float16"
  batch_size: 16

# Rendering
rendering:
  ffmpeg_threads: 8
  nvenc_enabled: true
  output_format: "mp4"
  output_codec: "h264_nvenc"
  output_bitrate: "8M"

# Posting
posting:
  late:
    api_endpoint: "https://api.late.dev"
    api_key: "${LATE_API_KEY}"
    timeout_seconds: 300

# Thumbnail generation
rendering:
  thumbnails:
    provider: "ideogram"
    api_key: "${IDEOGRAM_API_KEY}"
    variants: 2  # A/B testing
```

### 3. Set Environment Variables

Create `.env` file:

```bash
cat > .env << 'EOF'
# Database
DATABASE_URL=postgresql+asyncpg://bbb_user:changeme@localhost:5432/bbb_pipeline
POSTGRES_PASSWORD=changeme

# Redis
REDIS_URL=redis://localhost:6379/0

# S3
S3_ACCESS_KEY=your_access_key
S3_SECRET_KEY=your_secret_key
S3_REGION=us-east-1
S3_BUCKET_SOURCES=bbb-sources-prod
S3_BUCKET_EXTRACTIONS=bbb-extractions-prod

# LLM APIs
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROK_API_KEY=xai-...
PERPLEXITY_API_KEY=pplx-...

# Thumbnail Generation
IDEOGRAM_API_KEY=...

# Posting
LATE_API_KEY=...

# Logging
LOG_LEVEL=INFO
EOF

# Load environment
source .env
```

## Running Workers

### Option 1: Manual (For Testing)

Run each worker in a separate terminal:

**Terminal 1: Ingestion**
```bash
cd /opt/obs-cc
source venv/bin/activate
source .env
python -m pipeline.ingestion.worker
```

**Terminal 2: Transcription**
```bash
cd /opt/obs-cc
source venv/bin/activate
source .env
python -m pipeline.transcription.worker
```

**Terminal 3: Analysis**
```bash
cd /opt/obs-cc
source venv/bin/activate
source .env
python -m pipeline.analysis.worker
```

**Terminal 4: Rendering**
```bash
cd /opt/obs-cc
source venv/bin/activate
source .env
python -m pipeline.rendering.worker
```

**Terminal 5: Posting**
```bash
cd /opt/obs-cc
source venv/bin/activate
source .env
python -m pipeline.posting.worker
```

**Terminal 6: Archival**
```bash
cd /opt/obs-cc
source venv/bin/activate
source .env
python -m pipeline.archival.worker
```

### Option 2: Systemd Services (Production)

Create systemd service files:

**Ingestion Service:**
```bash
sudo nano /etc/systemd/system/bbb-ingestion.service
```

```ini
[Unit]
Description=BBB Pipeline - Ingestion Worker
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=bbb
WorkingDirectory=/opt/obs-cc
Environment="PATH=/opt/obs-cc/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
EnvironmentFile=/opt/obs-cc/.env
ExecStart=/opt/obs-cc/venv/bin/python -m pipeline.ingestion.worker
Restart=always
RestartSec=10
StandardOutput=append:/data/bbb/logs/ingestion.log
StandardError=append:/data/bbb/logs/ingestion.error.log

[Install]
WantedBy=multi-user.target
```

**Create similar service files for other workers:**
- `/etc/systemd/system/bbb-transcription.service`
- `/etc/systemd/system/bbb-analysis.service`
- `/etc/systemd/system/bbb-rendering.service`
- `/etc/systemd/system/bbb-posting.service`
- `/etc/systemd/system/bbb-archival.service`

**Enable and start services:**
```bash
sudo systemctl daemon-reload

# Enable services
sudo systemctl enable bbb-ingestion
sudo systemctl enable bbb-transcription
sudo systemctl enable bbb-analysis
sudo systemctl enable bbb-rendering
sudo systemctl enable bbb-posting
sudo systemctl enable bbb-archival

# Start services
sudo systemctl start bbb-ingestion
sudo systemctl start bbb-transcription
sudo systemctl start bbb-analysis
sudo systemctl start bbb-rendering
sudo systemctl start bbb-posting
sudo systemctl start bbb-archival

# Check status
sudo systemctl status bbb-*
```

**View logs:**
```bash
# Follow all logs
sudo journalctl -u bbb-* -f

# Specific worker
sudo journalctl -u bbb-transcription -f

# Or use log files
tail -f /data/bbb/logs/*.log
```

### Option 3: Supervisor (Alternative)

Install supervisor:
```bash
sudo apt-get install supervisor
```

Create supervisor config:
```bash
sudo nano /etc/supervisor/conf.d/bbb-pipeline.conf
```

```ini
[group:bbb-pipeline]
programs=ingestion,transcription,analysis,rendering,posting,archival

[program:ingestion]
command=/opt/obs-cc/venv/bin/python -m pipeline.ingestion.worker
directory=/opt/obs-cc
user=bbb
autostart=true
autorestart=true
stderr_logfile=/data/bbb/logs/ingestion.error.log
stdout_logfile=/data/bbb/logs/ingestion.log

[program:transcription]
command=/opt/obs-cc/venv/bin/python -m pipeline.transcription.worker
directory=/opt/obs-cc
user=bbb
autostart=true
autorestart=true
stderr_logfile=/data/bbb/logs/transcription.error.log
stdout_logfile=/data/bbb/logs/transcription.log

# ... (repeat for other workers)
```

**Start supervisor:**
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start bbb-pipeline:*

# Check status
sudo supervisorctl status
```

## Testing

### 1. Verify Infrastructure

**Test PostgreSQL:**
```bash
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline -c "SELECT 1;"
```

**Test Redis:**
```bash
docker exec -it bbb-redis redis-cli ping
```

### 2. Test Worker Connectivity

```bash
source venv/bin/activate
source .env

# Test database connection
python -c "import asyncio; from pipeline.common.db import test_connection; asyncio.run(test_connection())"

# Test Redis connection
python -c "import asyncio; from pipeline.common.queue import JobQueue; async def test(): q = JobQueue(); await q.initialize(); print('Redis OK'); asyncio.run(test())"
```

### 3. Test GPU Transcription

```bash
python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0))"
```

### 4. End-to-End Test

**Place test files:**
```bash
mkdir -p /data/bbb/streams/2025-01-15
cp /path/to/test/videos/*.mkv /data/bbb/streams/2025-01-15/
```

**Monitor logs:**
```bash
tail -f /data/bbb/logs/*.log
```

**Check queue:**
```bash
docker exec -it bbb-redis redis-cli

# In redis-cli:
ZCARD queue:ingestion
ZCARD queue:transcription
ZCARD queue:analysis
```

**Check database:**
```bash
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline

-- In psql:
SELECT * FROM streams ORDER BY created_at DESC LIMIT 5;
SELECT * FROM jobs ORDER BY created_at DESC LIMIT 10;
```

## Production Considerations

### Performance Optimization

**1. CPU Affinity:**
```bash
# Pin transcription worker to specific cores
taskset -c 0-7 python -m pipeline.transcription.worker

# Pin rendering workers to other cores
taskset -c 8-15 python -m pipeline.rendering.worker
```

**2. I/O Optimization:**
```bash
# Use tmpfs for working directory
sudo mount -t tmpfs -o size=100G tmpfs /data/bbb/working
```

**3. GPU Optimization:**
```bash
# Set GPU persistence mode
sudo nvidia-smi -pm 1

# Set GPU power limit (if needed)
sudo nvidia-smi -pl 300  # 300W
```

### Monitoring

**1. Install Monitoring Tools:**
```bash
sudo apt-get install htop iotop nvidia-smi
```

**2. Monitor Resources:**
```bash
# CPU & RAM
htop

# Disk I/O
sudo iotop

# GPU
watch -n 1 nvidia-smi

# Network
iftop
```

**3. Log Rotation:**
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
}
```

### Backup & Recovery

**1. Database Backup:**
```bash
# Create backup script
cat > /opt/obs-cc/scripts/backup-db.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=/data/bbb/backups
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR
docker exec bbb-postgres pg_dump -U bbb_user bbb_pipeline | gzip > $BACKUP_DIR/db_backup_$DATE.sql.gz
# Keep last 30 days
find $BACKUP_DIR -name "db_backup_*.sql.gz" -mtime +30 -delete
EOF

chmod +x /opt/obs-cc/scripts/backup-db.sh

# Add to crontab
crontab -e
# Add: 0 2 * * * /opt/obs-cc/scripts/backup-db.sh
```

**2. Configuration Backup:**
```bash
cp config/config.yaml config/config.yaml.bak
cp .env .env.bak
```

### Security Hardening

**1. Firewall:**
```bash
sudo ufw allow 22/tcp  # SSH
sudo ufw enable
```

**2. Secure Docker:**
```bash
# Don't expose PostgreSQL/Redis to public
# Use docker network isolation
```

**3. File Permissions:**
```bash
chmod 600 .env
chmod 600 config/config.yaml
```

## Troubleshooting

### Workers Not Starting

**Check logs:**
```bash
sudo journalctl -u bbb-ingestion -n 50
tail -f /data/bbb/logs/ingestion.error.log
```

**Common issues:**
- Missing dependencies: Reinstall with `pip install -r requirements.txt`
- Database connection failed: Check DATABASE_URL and docker-compose
- Redis connection failed: Check REDIS_URL and docker-compose

### GPU Not Detected

**Check NVIDIA driver:**
```bash
nvidia-smi
```

**Check CUDA:**
```bash
python -c "import torch; print(torch.cuda.is_available())"
```

**Reinstall NVIDIA driver:**
```bash
sudo apt-get purge nvidia-*
sudo apt-get install nvidia-driver-535
sudo reboot
```

### Out of Disk Space

**Check disk usage:**
```bash
df -h /data/bbb
du -sh /data/bbb/*
```

**Clean up:**
```bash
# Remove old extraction files
find /data/bbb/extractions -type f -mtime +7 -delete

# Clear working directory
rm -rf /data/bbb/working/*
```

### High Memory Usage

**Check memory:**
```bash
free -h
```

**Optimize:**
- Reduce transcription batch size in config
- Reduce rendering worker count
- Add swap space

### Transcription Slow

**Check GPU usage:**
```bash
nvidia-smi
```

**Optimize:**
- Increase batch size in config
- Use float16 compute type
- Upgrade to faster GPU (A10, A100)

## Next Steps

- **[Configuration Guide](configuration.md)** - Detailed configuration reference
- **[Operations Guide](operations.md)** - Day-to-day operations
- **[Monitoring Guide](monitoring.md)** - Set up observability
- **[Troubleshooting Guide](troubleshooting.md)** - Detailed problem resolution
