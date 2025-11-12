#!/bin/bash
#
# Hardware Discovery Script for BBB Pipeline Deployment
# Queries nodes on Tailscale network to discover available resources
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  BBB Pipeline Hardware Discovery      ${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

# Check if Tailscale is installed
if ! command -v tailscale &> /dev/null; then
    echo -e "${RED}❌ Tailscale not found. Please install Tailscale first.${NC}"
    exit 1
fi

# Get Tailscale status
echo -e "${YELLOW}🔍 Checking Tailscale network...${NC}"
TAILSCALE_STATUS=$(tailscale status --json 2>/dev/null || echo "{}")

# Parse nodes from Tailscale status
NODES=$(echo "$TAILSCALE_STATUS" | jq -r '.Peer[] | .HostName' 2>/dev/null)

if [ -z "$NODES" ]; then
    echo -e "${RED}❌ No Tailscale peers found.${NC}"
    echo "Make sure you're connected to your Tailnet and nodes are online."
    exit 1
fi

echo -e "${GREEN}✓ Found $(echo "$NODES" | wc -l) node(s) on Tailscale network${NC}"
echo ""

# Function to query node hardware
query_node() {
    local node=$1
    local node_ip=$(echo "$TAILSCALE_STATUS" | jq -r ".Peer[] | select(.HostName==\"$node\") | .TailscaleIPs[0]" 2>/dev/null)

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}📊 Node: $node ($node_ip)${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    # Test SSH connectivity
    if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$node" "exit" 2>/dev/null; then
        echo -e "${RED}  ❌ SSH connection failed${NC}"
        echo -e "${YELLOW}  Setup: ssh-copy-id $node${NC}"
        echo ""
        return
    fi

    # Query CPU
    local cpu_cores=$(ssh "$node" "nproc" 2>/dev/null || echo "unknown")
    local cpu_model=$(ssh "$node" "lscpu | grep 'Model name' | cut -d':' -f2 | xargs" 2>/dev/null || echo "unknown")
    echo -e "  ${YELLOW}CPU:${NC} $cpu_cores cores"
    echo -e "       $cpu_model"

    # Query RAM
    local ram_total=$(ssh "$node" "free -h | grep Mem | awk '{print \$2}'" 2>/dev/null || echo "unknown")
    local ram_avail=$(ssh "$node" "free -h | grep Mem | awk '{print \$7}'" 2>/dev/null || echo "unknown")
    echo -e "  ${YELLOW}RAM:${NC} $ram_total total, $ram_avail available"

    # Query GPU
    local gpu_info=$(ssh "$node" "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader" 2>/dev/null)
    if [ -n "$gpu_info" ]; then
        echo -e "  ${YELLOW}GPU:${NC}"
        while IFS=, read -r name memory driver; do
            echo -e "       • $name - $memory - Driver: $driver"
        done <<< "$gpu_info"
    else
        echo -e "  ${YELLOW}GPU:${NC} None detected"
    fi

    # Query Disk
    local disk_info=$(ssh "$node" "df -h /data 2>/dev/null || df -h / | tail -n1" 2>/dev/null)
    if [ -n "$disk_info" ]; then
        local disk_size=$(echo "$disk_info" | awk '{print $2}')
        local disk_used=$(echo "$disk_info" | awk '{print $3}')
        local disk_avail=$(echo "$disk_info" | awk '{print $4}')
        local disk_percent=$(echo "$disk_info" | awk '{print $5}')
        echo -e "  ${YELLOW}Disk:${NC} $disk_size total, $disk_avail available ($disk_percent used)"
    fi

    # Query OS
    local os_info=$(ssh "$node" "uname -srm" 2>/dev/null || echo "unknown")
    echo -e "  ${YELLOW}OS:${NC} $os_info"

    # Check Docker
    local docker_version=$(ssh "$node" "docker --version 2>/dev/null | cut -d' ' -f3 | tr -d ','" || echo "")
    if [ -n "$docker_version" ]; then
        echo -e "  ${YELLOW}Docker:${NC} $docker_version"
    fi

    # Check Kubernetes
    local k8s_version=$(ssh "$node" "kubectl version --client --short 2>/dev/null | cut -d' ' -f3" || echo "")
    if [ -n "$k8s_version" ]; then
        echo -e "  ${YELLOW}Kubernetes:${NC} $k8s_version"
    fi

    echo ""
}

# Query all nodes
for node in $NODES; do
    query_node "$node"
done

# Summary
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Resource Summary                      ${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"

total_cpu=0
total_ram=0
total_gpu=0
gpu_nodes=""
high_cpu_nodes=""
standard_nodes=""

for node in $NODES; do
    # Count CPUs
    cpu=$(ssh "$node" "nproc" 2>/dev/null || echo "0")
    total_cpu=$((total_cpu + cpu))

    # Count GPUs
    gpu_count=$(ssh "$node" "nvidia-smi --list-gpus 2>/dev/null | wc -l" || echo "0")
    total_gpu=$((total_gpu + gpu_count))

    # Categorize nodes
    if [ "$gpu_count" -gt 0 ]; then
        gpu_nodes="$gpu_nodes$node ($gpu_count GPU) "
    fi

    if [ "$cpu" -ge 16 ]; then
        high_cpu_nodes="$high_cpu_nodes$node ($cpu cores) "
    else
        standard_nodes="$standard_nodes$node ($cpu cores) "
    fi
done

echo -e "  ${YELLOW}Total Resources:${NC}"
echo -e "    • CPU Cores: $total_cpu"
echo -e "    • GPUs: $total_gpu"
echo ""
echo -e "  ${YELLOW}Node Categories:${NC}"
echo -e "    • GPU Nodes: ${gpu_nodes:-None}"
echo -e "    • High-CPU Nodes (≥16 cores): ${high_cpu_nodes:-None}"
echo -e "    • Standard Nodes: ${standard_nodes:-None}"
echo ""

# Deployment recommendations
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Deployment Recommendations            ${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"

if [ $total_gpu -eq 0 ]; then
    echo -e "${RED}⚠️  WARNING: No GPUs detected!${NC}"
    echo -e "   Transcription will be very slow without GPUs."
    echo -e "   Consider adding GPU nodes for production use."
    echo ""
fi

if [ $total_cpu -lt 32 ]; then
    echo -e "${YELLOW}⚠️  LOW CPU: $total_cpu cores total${NC}"
    echo -e "   Minimum recommended: 32 cores for full deployment"
    echo -e "   Consider: Reduce worker replica counts"
    echo ""
fi

# Suggest deployment mode
node_count=$(echo "$NODES" | wc -l)
if [ $node_count -ge 3 ] && [ $total_cpu -ge 32 ]; then
    echo -e "${GREEN}✓ Recommended: Kubernetes cluster deployment${NC}"
    echo -e "  Reason: Multiple nodes with sufficient resources"
    echo ""
    echo -e "  Suggested node assignments:"
    for node in $NODES; do
        gpu_count=$(ssh "$node" "nvidia-smi --list-gpus 2>/dev/null | wc -l" || echo "0")
        cpu=$(ssh "$node" "nproc" 2>/dev/null || echo "0")

        if [ "$gpu_count" -gt 0 ]; then
            echo -e "    • $node: Transcription worker (GPU)"
        elif [ "$cpu" -ge 16 ]; then
            echo -e "    • $node: Rendering workers (High CPU)"
        else
            echo -e "    • $node: Ingestion, Analysis, Posting, Archival"
        fi
    done
else
    echo -e "${YELLOW}ℹ️  Recommended: Solo node deployment${NC}"
    echo -e "  Reason: Limited resources or single node"
    echo -e "  Consider: Deploy all services on most capable node"
fi

echo ""
echo -e "${GREEN}✓ Hardware discovery complete!${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Run: ${YELLOW}claude-code${NC}"
echo -e "  2. Type: ${YELLOW}/deploy${NC}"
echo -e "  3. Follow interactive deployment wizard"
echo ""
