# Distributed Cluster Setup Guide

Complete guide for deploying the BBB post-stream extraction pipeline on a Kubernetes cluster.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Cluster Requirements](#cluster-requirements)
- [Pre-Deployment Setup](#pre-deployment-setup)
- [Infrastructure Deployment](#infrastructure-deployment)
- [Pipeline Deployment](#pipeline-deployment)
- [Verification](#verification)
- [Scaling Configuration](#scaling-configuration)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Software
- **Kubernetes** 1.28+ (1.30+ recommended)
- **kubectl** configured with cluster access
- **Helm** 3.12+ (for optional chart-based deployment)
- **Docker** (for building custom images)
- **Git** (for cloning repository)

### Required Services
- **PostgreSQL 16+** (can deploy in-cluster or use managed service)
- **Redis 7+** (can deploy in-cluster or use managed service)
- **S3-compatible storage** (AWS S3, Backblaze B2, MinIO, etc.)

### API Keys Required
- **Anthropic API key** (Claude)
- **OpenAI API key** (GPT-4o)
- **Grok API key** (X.AI)
- **Perplexity API key**
- **Late API key** (multi-platform posting)
- **Ideogram API key** (thumbnail generation)
- **Optional:** DALL-E, Flux API keys for additional thumbnail providers

### Access Requirements
- Cluster admin access (for creating namespaces, PVs, etc.)
- S3 bucket creation permissions
- DNS configuration (if using ingress)

## Cluster Requirements

### Node Specifications

**Control Plane (3 nodes recommended):**
```yaml
CPU: 4 cores
RAM: 8 GB
Disk: 100 GB SSD
```

**Worker Nodes - CPU (3+ nodes):**
```yaml
CPU: 8 cores (16 cores recommended)
RAM: 16 GB (32 GB recommended)
Disk: 200 GB SSD
Labels:
  workload-type: cpu
```

**Worker Nodes - GPU (1+ nodes):**
```yaml
CPU: 8 cores
RAM: 32 GB (64 GB recommended)
GPU: NVIDIA T4 / A10 / A100 (8GB+ VRAM)
Disk: 500 GB NVMe SSD
Labels:
  workload-type: gpu
  nvidia.com/gpu: "true"
```

### Storage Requirements

**Persistent Volumes:**
- **PostgreSQL:** 100 GB SSD (expandable)
- **Redis:** 20 GB SSD
- **Shared Working Directory:** 2 TB NVMe SSD (NFS or Ceph)

**Total Cluster Storage:** ~2.5 TB minimum

### Network Requirements
- **Internal:** 10 Gbps+ between nodes
- **External:** 1 Gbps+ for uploads/downloads
- **Egress:** Unrestricted for LLM APIs, Late API, S3

## Pre-Deployment Setup

### 1. Clone Repository

```bash
git clone https://github.com/designerpros/obs-cc.git
cd obs-cc
```

### 2. Label Nodes

Label your worker nodes for workload placement:

```bash
# Label CPU worker nodes
kubectl label nodes <node-name-1> workload-type=cpu
kubectl label nodes <node-name-2> workload-type=cpu
kubectl label nodes <node-name-3> workload-type=cpu

# Label GPU worker nodes
kubectl label nodes <gpu-node-name-1> workload-type=gpu
kubectl label nodes <gpu-node-name-1> nvidia.com/gpu=true
```

### 3. Install NVIDIA Device Plugin (GPU Nodes)

If using NVIDIA GPUs, install the device plugin:

```bash
kubectl create -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.0/nvidia-device-plugin.yml
```

Verify GPU detection:

```bash
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu"
```

### 4. Create Namespace

```bash
kubectl apply -f infrastructure/k8s/00-namespace.yaml
```

Verify:
```bash
kubectl get namespace bbb-pipeline
```

### 5. Configure Storage

#### Option A: NFS Shared Storage (Recommended)

**Setup NFS Server:**
```bash
# On NFS server
sudo apt-get install nfs-kernel-server
sudo mkdir -p /export/bbb-data
sudo chown nobody:nogroup /export/bbb-data
sudo chmod 777 /export/bbb-data

# Add to /etc/exports
echo "/export/bbb-data *(rw,sync,no_subtree_check,no_root_squash)" | sudo tee -a /etc/exports

# Restart NFS
sudo systemctl restart nfs-kernel-server
```

**Create PersistentVolume:**
```yaml
# infrastructure/k8s/pv-nfs.yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: bbb-data-pv
spec:
  capacity:
    storage: 2Ti
  accessModes:
    - ReadWriteMany
  nfs:
    server: <NFS-SERVER-IP>
    path: "/export/bbb-data"
  persistentVolumeReclaimPolicy: Retain
```

Apply:
```bash
kubectl apply -f infrastructure/k8s/pv-nfs.yaml
```

#### Option B: Ceph RBD

```bash
# Install Ceph CSI driver
helm repo add ceph-csi https://ceph.github.io/csi-charts
helm install ceph-csi ceph-csi/ceph-csi-rbd --namespace ceph-csi --create-namespace

# Configure storage class (see Ceph documentation)
```

#### Option C: Local Storage (Solo Node)

```yaml
# infrastructure/k8s/pv-local.yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: bbb-data-pv
spec:
  capacity:
    storage: 2Ti
  accessModes:
    - ReadWriteOnce
  hostPath:
    path: /data/bbb
    type: DirectoryOrCreate
  persistentVolumeReclaimPolicy: Retain
```

### 6. Create Secrets

**Create secrets file:**
```bash
# Create from template
cp infrastructure/k8s/secrets.example.yaml infrastructure/k8s/secrets.yaml

# Edit with your credentials
nano infrastructure/k8s/secrets.yaml
```

**secrets.yaml example:**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: bbb-secrets
  namespace: bbb-pipeline
type: Opaque
stringData:
  # Database
  DATABASE_URL: "postgresql+asyncpg://bbb_user:PASSWORD@postgres:5432/bbb_pipeline"

  # Redis
  REDIS_URL: "redis://redis:6379/0"

  # S3
  S3_ACCESS_KEY: "YOUR_ACCESS_KEY"
  S3_SECRET_KEY: "YOUR_SECRET_KEY"
  S3_ENDPOINT: "s3.amazonaws.com"
  S3_REGION: "us-east-1"
  S3_BUCKET_SOURCES: "bbb-sources-prod"
  S3_BUCKET_EXTRACTIONS: "bbb-extractions-prod"

  # LLM APIs
  ANTHROPIC_API_KEY: "sk-ant-..."
  OPENAI_API_KEY: "sk-..."
  GROK_API_KEY: "xai-..."
  PERPLEXITY_API_KEY: "pplx-..."

  # Thumbnail Generation
  IDEOGRAM_API_KEY: "..."

  # Posting
  LATE_API_KEY: "..."
  LATE_API_ENDPOINT: "https://api.late.dev"
```

Apply secrets:
```bash
kubectl apply -f infrastructure/k8s/secrets.yaml
```

### 7. Create ConfigMap

```bash
# Copy config template
cp config/config.example.yaml config/config.yaml

# Edit configuration
nano config/config.yaml

# Create ConfigMap
kubectl create configmap bbb-config \
  --from-file=config.yaml=config/config.yaml \
  -n bbb-pipeline
```

Verify:
```bash
kubectl get configmap bbb-config -n bbb-pipeline
```

## Infrastructure Deployment

### 1. Deploy PostgreSQL

**Option A: In-Cluster PostgreSQL**

```bash
kubectl apply -f infrastructure/k8s/01-postgres.yaml
```

Wait for pod to be ready:
```bash
kubectl wait --for=condition=ready pod -l app=postgres -n bbb-pipeline --timeout=300s
```

**Option B: Managed PostgreSQL (AWS RDS, Google Cloud SQL, etc.)**

Skip in-cluster deployment and update secrets with managed database URL:
```bash
kubectl edit secret bbb-secrets -n bbb-pipeline
# Update DATABASE_URL to point to managed service
```

### 2. Deploy Redis

**Option A: In-Cluster Redis**

```bash
kubectl apply -f infrastructure/k8s/02-redis.yaml
```

Wait for pod to be ready:
```bash
kubectl wait --for=condition=ready pod -l app=redis -n bbb-pipeline --timeout=300s
```

**Option B: Managed Redis (AWS ElastiCache, Redis Cloud, etc.)**

Skip in-cluster deployment and update secrets:
```bash
kubectl edit secret bbb-secrets -n bbb-pipeline
# Update REDIS_URL to point to managed service
```

### 3. Initialize Database

Run database migration:

```bash
# Create migration job
kubectl apply -f infrastructure/k8s/db-migration-job.yaml

# Wait for completion
kubectl wait --for=condition=complete job/db-migration -n bbb-pipeline --timeout=300s

# Check logs
kubectl logs job/db-migration -n bbb-pipeline
```

**If migration job doesn't exist, run manually:**

```bash
# Run temporary pod
kubectl run -it --rm db-init \
  --image=python:3.11-slim \
  --env-from=secret/bbb-secrets \
  -n bbb-pipeline \
  --restart=Never \
  -- bash

# Inside pod:
pip install asyncpg alembic
git clone https://github.com/designerpros/obs-cc.git
cd obs-cc
alembic upgrade head
exit
```

Verify database:
```bash
kubectl exec -it deploy/postgres -n bbb-pipeline -- psql -U bbb_user -d bbb_pipeline -c "\dt"
```

## Pipeline Deployment

### 1. Deploy Shared Resources

```bash
kubectl apply -f infrastructure/k8s/03-shared.yaml
```

This creates:
- PersistentVolumeClaim for shared storage
- Common labels and annotations
- RBAC roles (if needed)

Verify PVC is bound:
```bash
kubectl get pvc -n bbb-pipeline
```

### 2. Deploy Ingestion Service

```bash
kubectl apply -f infrastructure/k8s/04-ingestion.yaml
```

Verify deployment:
```bash
kubectl get pods -n bbb-pipeline -l app=bbb-ingestion
kubectl logs -f deployment/bbb-ingestion -n bbb-pipeline
```

### 3. Deploy Transcription Service (GPU)

```bash
kubectl apply -f infrastructure/k8s/05-transcription.yaml
```

Verify GPU scheduling:
```bash
kubectl get pods -n bbb-pipeline -l app=bbb-transcription -o wide
kubectl describe pod -n bbb-pipeline -l app=bbb-transcription | grep -A5 "Limits:"
```

Expected output should show `nvidia.com/gpu: 1`

### 4. Deploy Analysis Service

```bash
kubectl apply -f infrastructure/k8s/06-analysis.yaml
```

Verify:
```bash
kubectl get pods -n bbb-pipeline -l app=bbb-analysis
kubectl logs -f deployment/bbb-analysis -n bbb-pipeline
```

### 5. Deploy Rendering Service

```bash
kubectl apply -f infrastructure/k8s/07-rendering.yaml
```

Verify:
```bash
kubectl get pods -n bbb-pipeline -l app=bbb-rendering
kubectl logs -f deployment/bbb-rendering -n bbb-pipeline
```

### 6. Deploy Posting Service

```bash
kubectl apply -f infrastructure/k8s/08-posting.yaml
```

Verify:
```bash
kubectl get pods -n bbb-pipeline -l app=bbb-posting
kubectl logs -f deployment/bbb-posting -n bbb-pipeline
```

### 7. Deploy Archival Service

```bash
kubectl apply -f infrastructure/k8s/09-archival.yaml
```

Verify:
```bash
kubectl get pods -n bbb-pipeline -l app=bbb-archival
kubectl logs -f deployment/bbb-archival -n bbb-pipeline
```

## Verification

### 1. Check All Pods

```bash
kubectl get pods -n bbb-pipeline
```

Expected output:
```
NAME                              READY   STATUS    RESTARTS   AGE
bbb-ingestion-xxx                 1/1     Running   0          5m
bbb-transcription-xxx             1/1     Running   0          4m
bbb-analysis-xxx                  1/1     Running   0          3m
bbb-rendering-xxx                 1/1     Running   0          2m
bbb-posting-xxx                   1/1     Running   0          1m
bbb-archival-xxx                  1/1     Running   0          30s
postgres-xxx                      1/1     Running   0          10m
redis-xxx                         1/1     Running   0          10m
```

### 2. Check Services

```bash
kubectl get svc -n bbb-pipeline
```

### 3. Test Database Connectivity

```bash
# From ingestion pod
kubectl exec -it deployment/bbb-ingestion -n bbb-pipeline -- python -c "
import asyncio
from pipeline.common.db import test_connection
asyncio.run(test_connection())
"
```

### 4. Test Redis Connectivity

```bash
# From ingestion pod
kubectl exec -it deployment/bbb-ingestion -n bbb-pipeline -- python -c "
import asyncio
from pipeline.common.queue import JobQueue
async def test():
    queue = JobQueue()
    await queue.initialize()
    print('Redis connected!')
asyncio.run(test())
"
```

### 5. Check Health Endpoints

```bash
# Port-forward to access health checks
kubectl port-forward deployment/bbb-ingestion 8080:8080 -n bbb-pipeline &

# Test liveness
curl http://localhost:8080/health/liveness

# Test readiness
curl http://localhost:8080/health/readiness
```

### 6. Test End-to-End

Create a test stream:

```bash
# Copy test files to shared storage
kubectl exec -it deployment/bbb-ingestion -n bbb-pipeline -- bash

# Inside pod:
mkdir -p /data/bbb/streams/2025-01-15
# Copy your test video files here
exit

# Monitor logs
kubectl logs -f deployment/bbb-ingestion -n bbb-pipeline
```

Check job queue:
```bash
kubectl exec -it deployment/redis -n bbb-pipeline -- redis-cli

# Inside redis-cli:
ZCARD queue:ingestion
ZCARD queue:transcription
ZCARD queue:analysis
```

## Scaling Configuration

### Horizontal Pod Autoscaling (HPA)

**Analysis Service (CPU-based):**
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: bbb-analysis-hpa
  namespace: bbb-pipeline
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: bbb-analysis
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

**Rendering Service (CPU-based):**
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: bbb-rendering-hpa
  namespace: bbb-pipeline
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: bbb-rendering
  minReplicas: 4
  maxReplicas: 16
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 80
```

Apply:
```bash
kubectl apply -f infrastructure/k8s/hpa.yaml
```

### Manual Scaling

```bash
# Scale rendering workers
kubectl scale deployment bbb-rendering --replicas=8 -n bbb-pipeline

# Scale analysis workers
kubectl scale deployment bbb-analysis --replicas=4 -n bbb-pipeline

# Add GPU nodes for transcription (requires new nodes)
# Then scale transcription
kubectl scale deployment bbb-transcription --replicas=2 -n bbb-pipeline
```

### Resource Limits

Update resource limits in deployment manifests:

```yaml
resources:
  requests:
    cpu: 4
    memory: 8Gi
  limits:
    cpu: 8
    memory: 16Gi
```

## Troubleshooting

### Pod Stuck in Pending

**Check events:**
```bash
kubectl describe pod <pod-name> -n bbb-pipeline
```

**Common causes:**
- Insufficient resources: Add more nodes or reduce resource requests
- GPU not available: Check GPU node labels and device plugin
- PVC not bound: Check PersistentVolume configuration

### Pod CrashLoopBackOff

**Check logs:**
```bash
kubectl logs <pod-name> -n bbb-pipeline --previous
```

**Common causes:**
- Missing secrets/configmap: Verify secrets are created
- Database connection failed: Check DATABASE_URL and network
- Missing Python dependencies: Rebuild Docker image

### Health Check Failures

**Check readiness probe:**
```bash
kubectl get pods -n bbb-pipeline
kubectl logs <pod-name> -n bbb-pipeline | grep -i health
```

**Common causes:**
- Database/Redis not ready: Wait for infrastructure pods
- Disk space full: Clean up old files or expand storage
- GPU not detected: Check NVIDIA device plugin

### Slow Transcription

**Check GPU utilization:**
```bash
kubectl exec -it <transcription-pod> -n bbb-pipeline -- nvidia-smi
```

**Optimize:**
- Increase batch size in config
- Use larger GPU (A10, A100)
- Add more GPU nodes

### Jobs Stuck in Queue

**Check queue depth:**
```bash
kubectl exec -it deployment/redis -n bbb-pipeline -- redis-cli ZCARD queue:transcription
```

**Check worker logs:**
```bash
kubectl logs deployment/bbb-transcription -n bbb-pipeline --tail=100
```

**Common causes:**
- Workers crashed: Check pod status
- Circuit breaker open: Check external API connectivity
- Priority issues: Verify job priority calculation

## Next Steps

- **[Configuration Guide](configuration.md)** - Tune performance and behavior
- **[Operations Guide](operations.md)** - Day-to-day operations
- **[Monitoring Guide](monitoring.md)** - Set up observability
- **[Troubleshooting Guide](troubleshooting.md)** - Detailed problem resolution
