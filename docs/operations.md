# Operations Guide

Day-to-day operations, maintenance, and best practices for the BBB post-stream extraction pipeline.

## Table of Contents
- [Daily Operations](#daily-operations)
- [Monitoring](#monitoring)
- [Maintenance Tasks](#maintenance-tasks)
- [Backup & Recovery](#backup--recovery)
- [Scaling](#scaling)
- [Performance Optimization](#performance-optimization)

## Daily Operations

### Starting the Pipeline

**Kubernetes Cluster:**
```bash
# Check all pods are running
kubectl get pods -n bbb-pipeline

# If pods are stopped, restart deployments
kubectl rollout restart deployment -n bbb-pipeline
```

**Solo Node:**
```bash
# Start all workers (systemd)
sudo systemctl start bbb-*

# Or with supervisor
sudo supervisorctl start bbb-pipeline:*

# Check status
sudo systemctl status bbb-*
```

### Adding New Stream

**Manual Trigger:**
```bash
# Copy files to watch directory
cp -r /path/to/stream-files /data/bbb/streams/2025-01-15/

# Or use webhook
curl -X POST http://localhost:8080/trigger \
  -H "Authorization: Bearer ${WEBHOOK_AUTH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"stream_path": "/data/bbb/streams/2025-01-15"}'
```

### Monitoring Job Progress

**Check Queue Depth:**
```bash
# Kubernetes
kubectl exec -it deployment/redis -n bbb-pipeline -- redis-cli

# Solo node
docker exec -it bbb-redis redis-cli

# In redis-cli:
ZCARD queue:ingestion
ZCARD queue:transcription
ZCARD queue:analysis
ZCARD queue:rendering
ZCARD queue:posting
ZCARD queue:archival
```

**Check Database:**
```bash
# Kubernetes
kubectl exec -it deployment/postgres -n bbb-pipeline -- psql -U bbb_user -d bbb_pipeline

# Solo node
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline

# In psql:
SELECT status, COUNT(*) FROM jobs GROUP BY status;
SELECT * FROM streams ORDER BY created_at DESC LIMIT 10;
```

**Check Logs:**
```bash
# Kubernetes
kubectl logs -f deployment/bbb-transcription -n bbb-pipeline

# Solo node
tail -f /data/bbb/logs/transcription.log
sudo journalctl -u bbb-transcription -f
```

### Stopping the Pipeline

**Graceful Shutdown:**
```bash
# Kubernetes - scale down workers
kubectl scale deployment bbb-ingestion --replicas=0 -n bbb-pipeline
kubectl scale deployment bbb-transcription --replicas=0 -n bbb-pipeline
# ... (repeat for all workers)

# Wait for active jobs to complete
kubectl wait --for=delete pod -l app=bbb-transcription -n bbb-pipeline --timeout=600s

# Solo node
sudo systemctl stop bbb-*
```

**Emergency Shutdown:**
```bash
# Kubernetes
kubectl delete pod -l app=bbb-transcription -n bbb-pipeline --force --grace-period=0

# Solo node
sudo systemctl kill bbb-*
sudo killall -9 python
```

## Monitoring

### Key Metrics to Watch

**1. Queue Depth:**
- Normal: 0-5 jobs per queue
- Warning: 10-20 jobs (backlog building)
- Critical: 50+ jobs (workers overloaded)

**2. GPU Utilization:**
```bash
# Check GPU usage
nvidia-smi
watch -n 1 nvidia-smi
```
- Target: 80-95% during transcription
- Low usage (<50%): Check batch size or worker count

**3. CPU & Memory:**
```bash
htop
```
- Rendering workers: Expect high CPU usage
- Memory: Should stay below 80% total

**4. Disk Space:**
```bash
df -h /data/bbb
du -sh /data/bbb/*
```
- Working directory: Should clear after archival
- Alert if <20% free space

### Health Checks

**Automated Health Checks:**
```bash
# Liveness (is service alive?)
curl http://localhost:8080/health/liveness

# Readiness (are dependencies ready?)
curl http://localhost:8080/health/readiness
```

**Manual Health Checks:**
1. Database connectivity
2. Redis connectivity
3. S3 upload test
4. LLM API connectivity
5. GPU availability (transcription)

## Maintenance Tasks

### Daily Tasks

**1. Check Queue Health:**
```bash
# Script: check_queue_health.sh
#!/bin/bash
QUEUE_DEPTH=$(redis-cli ZCARD queue:transcription)
if [ "$QUEUE_DEPTH" -gt 20 ]; then
    echo "WARNING: Queue depth is $QUEUE_DEPTH"
    # Send alert
fi
```

**2. Monitor Disk Space:**
```bash
# Script: check_disk_space.sh
#!/bin/bash
USAGE=$(df -h /data/bbb | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$USAGE" -gt 80 ]; then
    echo "WARNING: Disk usage at ${USAGE}%"
    # Clean up old files
    find /data/bbb/working -type f -mtime +7 -delete
fi
```

### Weekly Tasks

**1. Database Maintenance:**
```bash
# Vacuum and analyze
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline -c "VACUUM ANALYZE;"

# Check database size
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline -c "SELECT pg_size_pretty(pg_database_size('bbb_pipeline'));"
```

**2. Log Rotation:**
```bash
# Check log sizes
du -sh /data/bbb/logs/*

# Rotate logs (logrotate handles automatically)
sudo logrotate -f /etc/logrotate.d/bbb-pipeline
```

**3. Performance Review:**
- Check average processing time per stream
- Identify bottlenecks
- Review LLM API costs

### Monthly Tasks

**1. Update Dependencies:**
```bash
# Update Python packages
source venv/bin/activate
pip install --upgrade -r requirements.txt

# Restart workers
sudo systemctl restart bbb-*
```

**2. Review S3 Costs:**
```bash
# Check S3 usage
aws s3 ls s3://bbb-sources-prod --recursive --summarize
aws s3 ls s3://bbb-extractions-prod --recursive --summarize

# Verify lifecycle policies are working
aws s3api get-bucket-lifecycle-configuration --bucket bbb-sources-prod
```

**3. Security Updates:**
```bash
# Update system packages
sudo apt-get update
sudo apt-get upgrade -y

# Rotate API keys (if policy requires)
```

### Quarterly Tasks

**1. Performance Optimization:**
- Review LLM cascade effectiveness
- Optimize transcription batch sizes
- Tune rendering workers

**2. Capacity Planning:**
- Review growth trends
- Plan for additional GPU nodes
- Estimate future S3 costs

**3. Disaster Recovery Test:**
- Restore database from backup
- Verify job recovery process
- Test failover procedures

## Backup & Recovery

### Database Backups

**Automated Backup Script:**
```bash
#!/bin/bash
# backup_db.sh
BACKUP_DIR=/data/bbb/backups
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

# Backup database
docker exec bbb-postgres pg_dump -U bbb_user bbb_pipeline | gzip > $BACKUP_DIR/db_backup_$DATE.sql.gz

# Keep last 30 days
find $BACKUP_DIR -name "db_backup_*.sql.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/db_backup_$DATE.sql.gz"
```

**Schedule with cron:**
```bash
crontab -e
# Add:
0 2 * * * /opt/obs-cc/scripts/backup_db.sh
```

**Restore from Backup:**
```bash
# List backups
ls -lh /data/bbb/backups/

# Restore
gunzip < /data/bbb/backups/db_backup_20250115_020000.sql.gz | \
  docker exec -i bbb-postgres psql -U bbb_user -d bbb_pipeline
```

### Configuration Backups

```bash
# Backup configuration
tar -czf config_backup_$(date +%Y%m%d).tar.gz \
  config/ \
  .env \
  infrastructure/k8s/*.yaml

# Store off-site
aws s3 cp config_backup_*.tar.gz s3://bbb-backups-prod/configs/
```

### Job Recovery

**If worker crashes during processing:**
```bash
# Jobs are marked as 'processing'
# Circuit breaker will retry after timeout

# Manually requeue stuck jobs:
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline

-- In psql:
UPDATE jobs SET status='pending', retry_count=retry_count+1
WHERE status='processing' AND updated_at < NOW() - INTERVAL '1 hour';
```

## Scaling

### Horizontal Scaling (Add More Workers)

**Kubernetes:**
```bash
# Scale rendering workers
kubectl scale deployment bbb-rendering --replicas=8 -n bbb-pipeline

# Scale analysis workers
kubectl scale deployment bbb-analysis --replicas=4 -n bbb-pipeline
```

**Solo Node:**
```bash
# Add more worker instances
# Edit systemd service file to run multiple instances:
# bbb-rendering@1.service
# bbb-rendering@2.service
```

### Vertical Scaling (Bigger Resources)

**Increase CPU/RAM:**
```yaml
# Kubernetes: Edit deployment
resources:
  requests:
    cpu: 8            # Increased from 4
    memory: 16Gi      # Increased from 8Gi
  limits:
    cpu: 16
    memory: 32Gi
```

**Add GPU Nodes:**
```bash
# Add node to cluster with GPU
kubectl label nodes <new-gpu-node> workload-type=gpu

# Scale transcription workers
kubectl scale deployment bbb-transcription --replicas=2 -n bbb-pipeline
```

### Auto-Scaling (HPA)

```bash
# Apply Horizontal Pod Autoscaler
kubectl apply -f infrastructure/k8s/hpa.yaml

# Monitor autoscaling
kubectl get hpa -n bbb-pipeline -w
```

## Performance Optimization

### Bottleneck Identification

**1. Transcription Bottleneck:**
- Symptom: transcription queue growing
- Solution: Add GPU nodes, increase batch size

**2. Rendering Bottleneck:**
- Symptom: rendering queue growing
- Solution: Add CPU workers, enable NVENC

**3. Analysis Bottleneck:**
- Symptom: analysis queue growing
- Solution: Add analysis workers, check LLM API limits

**4. Database Bottleneck:**
- Symptom: Slow queries, high connection count
- Solution: Increase pool size, add indexes, upgrade PostgreSQL

### Optimization Checklist

- [ ] GPU utilization >80% during transcription
- [ ] NVENC enabled for rendering (5-10x speedup)
- [ ] LLM response caching enabled
- [ ] Database connection pool sized appropriately
- [ ] Redis max connections sufficient for workers
- [ ] Disk I/O not saturated (check with iotop)
- [ ] Network bandwidth not saturated
- [ ] Worker count matches available resources

## Troubleshooting Common Issues

### Jobs Stuck in Queue

**Diagnosis:**
```bash
# Check worker status
kubectl get pods -n bbb-pipeline
sudo systemctl status bbb-transcription

# Check logs for errors
kubectl logs deployment/bbb-transcription -n bbb-pipeline --tail=100
```

**Solutions:**
- Restart workers
- Check for crashed pods
- Verify dependencies (DB, Redis, GPU)

### High Memory Usage

**Diagnosis:**
```bash
free -h
# Check per-process memory
ps aux --sort=-%mem | head -20
```

**Solutions:**
- Reduce transcription batch size
- Reduce number of rendering workers
- Add swap space (temporary)
- Upgrade RAM

### Slow Transcription

**Diagnosis:**
```bash
nvidia-smi
# Check GPU utilization
```

**Solutions:**
- Increase batch size (if VRAM available)
- Use float16 instead of float32
- Upgrade to faster GPU (A10, A100)

## Emergency Procedures

### Pipeline Completely Stuck

1. Stop all workers gracefully
2. Check database for stuck jobs
3. Reset job statuses to 'pending'
4. Clear Redis queues if corrupted
5. Restart workers one at a time
6. Monitor logs for errors

### Database Corruption

1. Stop all workers immediately
2. Assess damage with pg_dump
3. Restore from most recent backup
4. Replay missing transactions from logs
5. Verify data integrity
6. Resume operations

### S3 Upload Failures

1. Check S3 credentials and permissions
2. Verify network connectivity
3. Check S3 service status
4. Retry failed uploads manually
5. Increase retry attempts in config

## Best Practices

1. **Always monitor queue depths** - Early warning of issues
2. **Keep backups current** - Daily database backups minimum
3. **Test disaster recovery** - Quarterly failover tests
4. **Document changes** - Keep changelog for config updates
5. **Rotate logs regularly** - Prevent disk space issues
6. **Monitor costs** - Track LLM API and S3 spending
7. **Update dependencies** - Monthly security patches
8. **Scale proactively** - Don't wait for queues to back up

## Next Steps

- **[Monitoring Guide](monitoring.md)** - Set up comprehensive observability
- **[Troubleshooting Guide](troubleshooting.md)** - Detailed problem resolution
- **[Configuration Guide](configuration.md)** - Tune settings for your workload
