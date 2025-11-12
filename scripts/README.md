# Deployment Scripts

Utilities for deploying and managing the BBB post-stream extraction pipeline.

## Hardware Discovery

**`discover_hardware.sh`** - Discover available hardware across Tailscale network

Query all nodes on your Tailnet to discover:
- CPU cores and model
- RAM capacity
- GPU availability (NVIDIA)
- Disk space
- OS version
- Docker/Kubernetes availability

**Usage:**
```bash
./scripts/discover_hardware.sh
```

**Example Output:**
```
═══════════════════════════════════════
  BBB Pipeline Hardware Discovery
═══════════════════════════════════════

🔍 Checking Tailscale network...
✓ Found 4 node(s) on Tailscale network

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Node: node1 (10.0.0.1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  CPU: 32 cores
       AMD Ryzen 9 5950X
  RAM: 128Gi total, 110Gi available
  GPU:
       • NVIDIA GeForce RTX 3090 - 24576 MiB - Driver: 535.129.03
       • NVIDIA GeForce RTX 3090 - 24576 MiB - Driver: 535.129.03
  Disk: 2.0T total, 1.5T available (25% used)
  OS: Linux 5.15.0-91-generic x86_64
  Docker: 24.0.7
  Kubernetes: v1.28.4

[... more nodes ...]

═══════════════════════════════════════
  Resource Summary
═══════════════════════════════════════
  Total Resources:
    • CPU Cores: 72
    • GPUs: 4

  Node Categories:
    • GPU Nodes: node1 (2 GPU) node2 (1 GPU) node3 (1 GPU)
    • High-CPU Nodes (≥16 cores): node1 (32 cores)
    • Standard Nodes: node4 (8 cores)

═══════════════════════════════════════
  Deployment Recommendations
═══════════════════════════════════════
✓ Recommended: Kubernetes cluster deployment
  Reason: Multiple nodes with sufficient resources

  Suggested node assignments:
    • node1: Transcription worker (GPU)
    • node2: Transcription worker (GPU)
    • node3: Transcription worker (GPU)
    • node1: Rendering workers (High CPU)
    • node4: Ingestion, Analysis, Posting, Archival

✓ Hardware discovery complete!

Next steps:
  1. Run: claude-code
  2. Type: /deploy
  3. Follow interactive deployment wizard
```

**Requirements:**
- Tailscale installed and logged in
- SSH access to all nodes (`ssh-copy-id <node>`)
- Nodes must be online and accessible

## Claude Code Deployment

### `/deploy` - Interactive Deployment Wizard

Claude Code can act as an intelligent deployment orchestrator when installed on your deployment server.

**Capabilities:**
1. ✅ Clone repo from GitHub
2. ✅ Read and understand documentation
3. ✅ Discover hardware on Tailscale network
4. ✅ Query GPU, CPU, RAM, disk on each node
5. ✅ Recommend deployment strategy
6. ✅ Ask clarifying questions interactively
7. ✅ Generate optimized configuration files
8. ✅ Assign workloads to appropriate nodes
9. ✅ Execute deployment commands
10. ✅ Verify deployment success

**Setup:**

1. **Install Claude Code on orchestrator/deployment server:**
```bash
# On your deployment/orchestrator server
curl -fsSL https://claude.ai/install.sh | sh
```

2. **Install Tailscale (if not already installed):**
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

3. **Set up SSH access to all nodes:**
```bash
ssh-keygen -t ed25519 -C "deployment-orchestrator"
ssh-copy-id node1
ssh-copy-id node2
ssh-copy-id node3
# ... for all nodes
```

4. **Configure GitHub access:**
```bash
gh auth login
# Or add deploy key to repo
```

5. **Clone repository:**
```bash
git clone https://github.com/designerpros/obs-cc.git
cd obs-cc
```

6. **Start Claude Code:**
```bash
claude-code
```

7. **Run deployment wizard:**
```
/deploy
```

**Example Session:**

```
You: /deploy

Claude Code:
🔍 Discovering infrastructure...

Found Tailscale network with 4 nodes:
1. node1: 32 cores, 128GB RAM, 2x RTX 3090 (24GB each)
2. node2: 16 cores, 64GB RAM, 1x RTX 2080S (8GB)
3. node3: 16 cores, 64GB RAM, 1x RTX 2080S (8GB)
4. node4: 8 cores, 32GB RAM, no GPU

📊 Hardware Analysis:
- Total: 72 CPU cores, 288GB RAM, 4 GPUs (56GB VRAM total)
- GPU nodes: 3 (capable of 4-6 streams/day)
- High-CPU nodes: 1 (for rendering)
- Standard nodes: 1 (for auxiliary services)

✅ Resources exceed minimum requirements!

Recommended deployment: **Kubernetes cluster**

I suggest:
- node1: 2x Transcription workers (dual RTX 3090)
- node2,node3: 1x Transcription worker each
- node1: 4x Rendering workers (high CPU)
- node4: Ingestion, Analysis, Posting, Archival, Infrastructure

Questions:
1. Should I install K3s on these nodes?
   - Yes, install K3s (recommended)
   - No, I have existing K8s cluster

2. PostgreSQL and Redis deployment:
   - Deploy on node4 (in-cluster)
   - Use existing instance (provide connection string)

3. S3 configuration:
   - Sources bucket name: (default: bbb-sources-prod)
   - Extractions bucket name: (default: bbb-extractions-prod)

4. API Keys - do you have these ready?
   - ANTHROPIC_API_KEY
   - OPENAI_API_KEY
   - GROK_API_KEY
   - PERPLEXITY_API_KEY
   - LATE_API_KEY
   - IDEOGRAM_API_KEY

Let me know your preferences, or type "auto" for automated deployment with defaults.

You: auto

Claude Code:
📦 Starting automated deployment...

Step 1/8: Installing K3s on control plane (node4)...
✓ K3s installed on node4

Step 2/8: Joining worker nodes...
✓ node1 joined cluster
✓ node2 joined cluster
✓ node3 joined cluster

Step 3/8: Labeling nodes by workload...
✓ node1 labeled: workload-type=gpu,high-cpu=true
✓ node2 labeled: workload-type=gpu
✓ node3 labeled: workload-type=gpu
✓ node4 labeled: workload-type=standard

Step 4/8: Creating namespace and secrets...
✓ Namespace 'bbb-pipeline' created
✓ Secrets configured

Step 5/8: Deploying infrastructure...
✓ PostgreSQL deployed and healthy
✓ Redis deployed and healthy
✓ Database initialized

Step 6/8: Deploying pipeline workers...
✓ Ingestion: 1 replica (node4)
✓ Transcription: 4 replicas (node1 x2, node2, node3)
✓ Analysis: 2 replicas (node4)
✓ Rendering: 4 replicas (node1)
✓ Posting: 1 replica (node4)
✓ Archival: 1 replica (node4)

Step 7/8: Verifying deployment...
✓ All pods healthy
✓ Health checks passing
✓ GPU detection confirmed (4 GPUs)
✓ Queue system operational

Step 8/8: Configuring monitoring...
✓ Prometheus + Grafana deployed
✓ Alert rules configured
✓ Grafana dashboard imported

═══════════════════════════════════════
  Deployment Complete! 🎉
═══════════════════════════════════════

Cluster Summary:
- Control Plane: node4
- Workers: node1, node2, node3, node4
- Total Resources: 72 CPUs, 288GB RAM, 4 GPUs

Services Deployed:
- PostgreSQL: postgres.bbb-pipeline.svc:5432
- Redis: redis.bbb-pipeline.svc:6379
- 13 worker pods across 6 phases

Monitoring:
- Grafana: http://node4:3000 (admin/prom-operator)
- Prometheus: http://node4:9090

Next Steps:
1. Add test stream:
   ssh node4 "mkdir -p /data/bbb/streams/2025-01-15"
   scp -r /path/to/test-files/* node4:/data/bbb/streams/2025-01-15/

2. Monitor processing:
   kubectl logs -f deployment/bbb-ingestion -n bbb-pipeline

3. View Grafana dashboard:
   Open: http://node4:3000

4. Check queue depth:
   kubectl exec -it deployment/redis -n bbb-pipeline -- redis-cli ZCARD queue:transcription

Pipeline is ready for production! 🚀
```

## Workflow Integration

The `/deploy` command is designed to work seamlessly with Claude Code's capabilities:

1. **Repository Understanding** - Claude Code reads all documentation
2. **Hardware Discovery** - Uses `discover_hardware.sh` or direct SSH queries
3. **Intelligent Recommendations** - Matches workloads to hardware
4. **Interactive Configuration** - Asks clarifying questions
5. **Automated Deployment** - Executes all deployment steps
6. **Verification** - Ensures deployment succeeded
7. **Monitoring Setup** - Optional Prometheus/Grafana deployment

## Manual Deployment

If you prefer manual deployment without Claude Code, follow:
- **Kubernetes Cluster:** [docs/cluster-setup.md](../docs/cluster-setup.md)
- **Solo Node:** [docs/solo-setup.md](../docs/solo-setup.md)

## Advanced Features

### Custom Node Assignments

Claude Code can handle complex node assignment scenarios:

**Example: Mixed GPU Types**
```
You: I want to use node1 (RTX 3090) for transcription, and node2 (RTX 2080) for rendering

Claude Code:
✓ Custom assignment configured:
  - node1 (RTX 3090): Transcription workers
    - Reason: Higher VRAM for large-v3 model
  - node2 (RTX 2080): Rendering workers (NVENC)
    - Reason: Lower-end GPU still excellent for hardware encoding

Updating deployment manifests...
```

### Resource Constraints

Claude Code detects and adapts to resource limitations:

**Example: Limited RAM**
```
⚠️  WARNING: node3 has only 16GB RAM (32GB recommended for transcription)

Applying optimizations:
  ✓ Reduced batch_size: 16 → 8
  ✓ Using compute_type: float16 (instead of float32)
  ✓ Memory limit: 12Gi (leaves 4GB for system)

Expected impact:
  - Transcription speed: ~30% slower
  - Memory usage: Fits within 12GB limit
```

### Multi-Region Deployment

Claude Code can orchestrate deployments across multiple Tailnets:

**Example: US + EU Regions**
```
You: Deploy transcription to US nodes, rendering to EU nodes

Claude Code:
📍 Multi-region deployment detected

US Tailnet (tailnet-us):
  - node1-us, node2-us: Transcription workers

EU Tailnet (tailnet-eu):
  - node1-eu, node2-eu: Rendering workers

Shared Infrastructure:
  - PostgreSQL: node1-us (primary)
  - Redis: node1-us (primary)

⚠️  Note: Cross-region latency may impact queue performance
Recommendation: Use separate pipelines per region
```

## Troubleshooting

### SSH Connection Failed

```bash
# Ensure SSH keys are set up
ssh-copy-id node1

# Test connection
ssh node1 "echo 'Connection successful!'"
```

### Tailscale Nodes Not Found

```bash
# Check Tailscale status
tailscale status

# If not logged in
sudo tailscale up
```

### Claude Code Permission Denied

```bash
# Ensure Claude Code can execute sudo (for K3s install)
sudo visudo
# Add: your_user ALL=(ALL) NOPASSWD: ALL
```

## Security Notes

- Claude Code only executes commands you approve
- Secrets are stored in Kubernetes secrets (encrypted at rest)
- SSH keys should use passphrase protection
- Tailscale provides encrypted tunnel for all traffic
- Use RBAC for Kubernetes cluster access

## Contributing

To improve deployment automation:
1. Edit `.claude/commands/deploy.md` for workflow changes
2. Update `scripts/discover_hardware.sh` for additional hardware detection
3. Test on your infrastructure before submitting PR

## Support

For deployment issues:
1. Check logs: `claude-code` shows full execution logs
2. Review docs: [docs/cluster-setup.md](../docs/cluster-setup.md)
3. Open issue: [GitHub Issues](https://github.com/designerpros/obs-cc/issues)
