# Troubleshooting Guide

Comprehensive troubleshooting guide for common issues in the BBB post-stream extraction pipeline.

## Table of Contents
- [General Diagnostics](#general-diagnostics)
- [Infrastructure Issues](#infrastructure-issues)
- [Worker Issues](#worker-issues)
- [Performance Issues](#performance-issues)
- [API Issues](#api-issues)
- [Data Issues](#data-issues)

## General Diagnostics

### Quick Health Check

```bash
# 1. Check all services are running
kubectl get pods -n bbb-pipeline  # Kubernetes
sudo systemctl status bbb-*       # Solo node

# 2. Check database connectivity
psql -h localhost -U bbb_user -d bbb_pipeline -c "SELECT 1;"

# 3. Check Redis connectivity
redis-cli ping

# 4. Check GPU availability
nvidia-smi

# 5. Check disk space
df -h /data/bbb

# 6. Check queue health
redis-cli ZCARD queue:transcription
```

### Log Analysis

**Kubernetes:**
```bash
# Recent logs
kubectl logs deployment/bbb-transcription -n bbb-pipeline --tail=100

# Follow logs in real-time
kubectl logs -f deployment/bbb-transcription -n bbb-pipeline

# Previous crash logs
kubectl logs deployment/bbb-transcription -n bbb-pipeline --previous

# All pods with label
kubectl logs -l app=bbb-transcription -n bbb-pipeline --tail=50
```

**Solo Node:**
```bash
# Systemd logs
sudo journalctl -u bbb-transcription -n 100
sudo journalctl -u bbb-transcription -f

# File logs
tail -f /data/bbb/logs/transcription.log
tail -f /data/bbb/logs/transcription.error.log

# Search for errors
grep -i error /data/bbb/logs/*.log
```

## Infrastructure Issues

### Issue: Database Connection Failed

**Symptoms:**
- Workers crash on startup
- Error: `connection refused` or `password authentication failed`

**Diagnosis:**
```bash
# Check PostgreSQL is running
kubectl get pods -n bbb-pipeline -l app=postgres  # Kubernetes
docker ps | grep postgres                         # Solo node

# Check connection string
echo $DATABASE_URL

# Test connection
psql -h localhost -U bbb_user -d bbb_pipeline
```

**Solutions:**

1. **PostgreSQL not running:**
```bash
# Kubernetes
kubectl logs deployment/postgres -n bbb-pipeline
kubectl restart pod <postgres-pod>

# Solo node
docker-compose restart postgres
```

2. **Wrong password:**
```bash
# Check secret
kubectl get secret bbb-secrets -n bbb-pipeline -o jsonpath='{.data.DATABASE_URL}' | base64 -d

# Update password
kubectl edit secret bbb-secrets -n bbb-pipeline
```

3. **Network issues:**
```bash
# Check service
kubectl get svc postgres -n bbb-pipeline

# Test connectivity
kubectl run -it --rm debug --image=postgres:16 --restart=Never -- \
  psql -h postgres.bbb-pipeline.svc.cluster.local -U bbb_user -d bbb_pipeline
```

---

### Issue: Redis Connection Failed

**Symptoms:**
- Workers can't enqueue/dequeue jobs
- Error: `redis.exceptions.ConnectionError`

**Diagnosis:**
```bash
# Check Redis is running
kubectl get pods -n bbb-pipeline -l app=redis
docker ps | grep redis

# Test connection
redis-cli -h localhost -p 6379 ping
```

**Solutions:**

1. **Redis not running:**
```bash
# Kubernetes
kubectl logs deployment/redis -n bbb-pipeline
kubectl restart pod <redis-pod>

# Solo node
docker-compose restart redis
```

2. **Redis out of memory:**
```bash
# Check memory usage
redis-cli info memory

# Clear old jobs
redis-cli FLUSHDB  # WARNING: Clears all jobs!
```

3. **Too many connections:**
```bash
# Check current connections
redis-cli CLIENT LIST | wc -l

# Increase max connections in config
# redis.conf: maxclients 10000
```

---

### Issue: S3 Upload Failed

**Symptoms:**
- Archival jobs fail
- Error: `Access Denied` or `Network timeout`

**Diagnosis:**
```bash
# Test S3 credentials
aws s3 ls s3://bbb-sources-prod --profile default

# Check network connectivity
curl -I https://s3.amazonaws.com
```

**Solutions:**

1. **Wrong credentials:**
```bash
# Verify credentials in secret
kubectl get secret bbb-secrets -n bbb-pipeline -o jsonpath='{.data.S3_ACCESS_KEY}' | base64 -d

# Test credentials
aws configure list
aws s3 ls
```

2. **Bucket permissions:**
```bash
# Check bucket policy
aws s3api get-bucket-policy --bucket bbb-sources-prod

# Required permissions:
# - s3:PutObject
# - s3:GetObject
# - s3:ListBucket
# - s3:PutLifecycleConfiguration
```

3. **Network timeout:**
```bash
# Increase timeout in config
posting:
  late:
    timeout_seconds: 600  # Increase from 300
```

## Worker Issues

### Issue: Worker CrashLoopBackOff

**Symptoms:**
- Pod keeps restarting (Kubernetes)
- Worker exits immediately (Solo node)

**Diagnosis:**
```bash
# Check pod events
kubectl describe pod <pod-name> -n bbb-pipeline

# Check previous crash logs
kubectl logs <pod-name> -n bbb-pipeline --previous

# Check exit code
kubectl get pod <pod-name> -n bbb-pipeline -o jsonpath='{.status.containerStatuses[0].lastState.terminated.exitCode}'
```

**Common Exit Codes:**
- `137`: Killed by OOM (Out of Memory)
- `1`: General error (check logs)
- `139`: Segmentation fault (GPU driver issue)

**Solutions:**

1. **Out of Memory (Exit 137):**
```yaml
# Increase memory limit
resources:
  limits:
    memory: 16Gi  # Increase from 8Gi
```

2. **Missing dependencies:**
```bash
# Rebuild image with dependencies
docker build -t bbb-pipeline:latest .
```

3. **GPU driver issue (Exit 139):**
```bash
# Check NVIDIA driver
nvidia-smi

# Reinstall driver
sudo apt-get purge nvidia-*
sudo apt-get install nvidia-driver-535
sudo reboot
```

---

### Issue: Transcription Worker Slow

**Symptoms:**
- Transcription taking >2 hours
- GPU utilization <50%

**Diagnosis:**
```bash
# Check GPU usage
nvidia-smi

# Check batch size in config
grep batch_size config/pipeline.yaml

# Check worker logs for errors
kubectl logs deployment/bbb-transcription -n bbb-pipeline --tail=100
```

**Solutions:**

1. **Low batch size:**
```yaml
# Increase batch size
transcription:
  whisper:
    batch_size: 24  # Increase from 16
```

2. **Using CPU instead of GPU:**
```yaml
# Ensure CUDA is enabled
transcription:
  whisper:
    device: "cuda"
    compute_type: "float16"
```

3. **GPU memory fragmentation:**
```bash
# Restart worker to clear VRAM
kubectl restart deployment bbb-transcription -n bbb-pipeline
```

---

### Issue: Rendering Worker Slow

**Symptoms:**
- Rendering taking >30 minutes per extraction
- CPU usage <50%

**Diagnosis:**
```bash
# Check NVENC availability
ffmpeg -encoders | grep nvenc

# Check thread count
grep threads config/pipeline.yaml

# Check if using hardware acceleration
ps aux | grep ffmpeg
```

**Solutions:**

1. **Not using NVENC:**
```yaml
# Enable hardware acceleration
rendering:
  ffmpeg:
    hardware_acceleration: "cuda"
    video_codec: "h264_nvenc"
```

2. **Too few threads:**
```yaml
# Increase threads
rendering:
  ffmpeg:
    threads: 16  # Increase from 8
```

3. **Using slow preset:**
```yaml
# Use faster preset
rendering:
  ffmpeg:
    preset: "medium"  # Change from "slow"
```

## Performance Issues

### Issue: Jobs Stuck in Queue

**Symptoms:**
- Queue depth growing
- Jobs not being processed

**Diagnosis:**
```bash
# Check queue depth
redis-cli ZCARD queue:transcription

# Check worker count
kubectl get pods -n bbb-pipeline -l app=bbb-transcription

# Check if workers are processing
kubectl logs deployment/bbb-transcription -n bbb-pipeline --tail=50
```

**Solutions:**

1. **Not enough workers:**
```bash
# Scale up workers
kubectl scale deployment bbb-transcription --replicas=2 -n bbb-pipeline
```

2. **Workers crashed:**
```bash
# Restart workers
kubectl restart deployment bbb-transcription -n bbb-pipeline
```

3. **Circuit breaker open:**
```bash
# Check logs for circuit breaker messages
# Wait for circuit breaker to close (60s default)
# Or restart worker to reset
```

---

### Issue: High LLM API Costs

**Symptoms:**
- Unexpected API bills
- Too many API calls

**Diagnosis:**
```bash
# Check LLM call count
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline

-- In psql:
SELECT COUNT(*) FROM llm_calls WHERE created_at > NOW() - INTERVAL '1 day';
SELECT llm_provider, COUNT(*) FROM llm_calls GROUP BY llm_provider;
```

**Solutions:**

1. **Enable caching:**
```yaml
cost_optimization:
  llm:
    cache_responses: true
    cache_ttl_hours: 168  # 1 week
```

2. **Use cheaper models:**
```yaml
analysis:
  topic_segmentation:
    model: "claude-3-5-haiku-20241022"  # Use Haiku instead of Sonnet
```

3. **Increase approval threshold:**
```yaml
analysis:
  coherence:
    min_score_per_llm: 85  # Stricter filtering, fewer consensus calls
```

---

### Issue: Disk Space Full

**Symptoms:**
- Workers crashing
- Can't write files
- Error: `No space left on device`

**Diagnosis:**
```bash
# Check disk usage
df -h /data/bbb
du -sh /data/bbb/*

# Find large files
find /data/bbb -type f -size +1G -exec ls -lh {} \;
```

**Solutions:**

1. **Clean up working directory:**
```bash
# Remove temp files
rm -rf /data/bbb/working/*

# Remove old extractions (already uploaded)
find /data/bbb/extractions -type f -mtime +7 -delete
```

2. **Enable auto-cleanup:**
```yaml
archival:
  local_cleanup:
    enabled: true
    cleanup_after: "s3_upload_verified"
```

3. **Expand storage:**
```bash
# Kubernetes: Expand PVC
kubectl edit pvc bbb-data -n bbb-pipeline
# Change size: 2Ti → 4Ti

# Solo node: Add disk or mount larger volume
```

## API Issues

### Issue: LLM API Rate Limit

**Symptoms:**
- Error: `Rate limit exceeded`
- Analysis jobs failing

**Diagnosis:**
```bash
# Check recent API errors
kubectl logs deployment/bbb-analysis -n bbb-pipeline | grep -i "rate limit"
```

**Solutions:**

1. **Add delays between calls:**
```yaml
# Reduce concurrency
# Scale down analysis workers
kubectl scale deployment bbb-analysis --replicas=2 -n bbb-pipeline
```

2. **Use different providers:**
```yaml
# Rotate through providers
analysis:
  coherence:
    llm_providers:
      - name: "grok"       # Different rate limits
      - name: "anthropic"
      - name: "openai"
```

3. **Request higher limits:**
- Contact API provider for rate limit increase
- Upgrade to enterprise tier

---

### Issue: Late API Posting Failed

**Symptoms:**
- Posts not appearing on platforms
- Error: `Invalid credentials`

**Diagnosis:**
```bash
# Check Late API connectivity
curl -H "Authorization: Bearer ${LATE_API_KEY}" \
  https://api.late.dev/v1/profiles

# Check logs
kubectl logs deployment/bbb-posting -n bbb-pipeline --tail=100
```

**Solutions:**

1. **Invalid API key:**
```bash
# Verify key
echo $LATE_API_KEY

# Update secret
kubectl edit secret bbb-secrets -n bbb-pipeline
```

2. **Platform not authorized:**
- Check Late dashboard
- Reauthorize platform connections

3. **API timeout:**
```yaml
# Increase timeout
posting:
  late:
    timeout_seconds: 600  # Increase from 300
```

## Data Issues

### Issue: Transcription Missing Words

**Symptoms:**
- Transcript has gaps
- Low confidence scores

**Diagnosis:**
```bash
# Check audio quality
ffprobe /path/to/audio.wav

# Check VAD threshold
grep vad_threshold config/pipeline.yaml
```

**Solutions:**

1. **Lower VAD threshold:**
```yaml
transcription:
  whisper:
    vad_threshold: 0.3  # Lower from 0.5 (more sensitive)
```

2. **Use larger model:**
```yaml
transcription:
  whisper:
    model: "large-v3"  # Instead of "medium"
```

3. **Disable VAD:**
```yaml
transcription:
  whisper:
    vad_filter: false  # Process all audio
```

---

### Issue: Poor Quality Extractions

**Symptoms:**
- LLM cascade rejecting all topics
- Low coherence scores

**Diagnosis:**
```bash
# Check rejection reasons in logs
kubectl logs deployment/bbb-analysis -n bbb-pipeline | grep "rejected"

# Check scores in database
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline

-- In psql:
SELECT title, llm_scores FROM topics WHERE consensus_approved = false ORDER BY created_at DESC LIMIT 10;
```

**Solutions:**

1. **Lower approval threshold:**
```yaml
analysis:
  coherence:
    min_score_per_llm: 70  # Lower from 80
```

2. **Adjust topic segmentation:**
```yaml
analysis:
  topic_segmentation:
    min_topic_duration_seconds: 90  # Shorter topics
```

3. **Review stream quality:**
- Check if source audio is clear
- Verify camera angles are properly captured

---

### Issue: Thumbnail Generation Failed

**Symptoms:**
- No thumbnails created
- Error: `Ideogram API error`

**Diagnosis:**
```bash
# Check API key
echo $IDEOGRAM_API_KEY

# Check logs
kubectl logs deployment/bbb-rendering -n bbb-pipeline | grep -i thumbnail
```

**Solutions:**

1. **Invalid API key:**
```bash
# Verify key
curl -H "Api-Key: ${IDEOGRAM_API_KEY}" \
  https://api.ideogram.ai/v1/models

# Update secret
kubectl edit secret bbb-secrets -n bbb-pipeline
```

2. **Use fallback provider:**
```yaml
metadata:
  thumbnails:
    provider: "dalle"  # Switch to DALL-E
    api_key: "${OPENAI_API_KEY}"
```

3. **Extract from video:**
```yaml
metadata:
  thumbnails:
    # Use frame extraction instead of AI generation
    ai_generation:
      enabled: false
    frame_extraction:
      enabled: true
```

## Emergency Procedures

### Complete Pipeline Failure

1. **Stop all workers:**
```bash
kubectl scale deployment --all --replicas=0 -n bbb-pipeline
```

2. **Check database integrity:**
```bash
docker exec -it bbb-postgres pg_dump -U bbb_user bbb_pipeline > /tmp/backup.sql
```

3. **Clear stuck jobs:**
```sql
UPDATE jobs SET status='pending', retry_count=0 WHERE status='processing';
```

4. **Clear Redis queues:**
```bash
redis-cli FLUSHDB  # WARNING: Clears all queues
```

5. **Restart workers one by one:**
```bash
kubectl scale deployment bbb-ingestion --replicas=1 -n bbb-pipeline
# Wait and verify
kubectl scale deployment bbb-transcription --replicas=1 -n bbb-pipeline
# Continue...
```

### Database Corruption

1. **Backup current state:**
```bash
docker exec bbb-postgres pg_dump -U bbb_user bbb_pipeline | gzip > db_corrupted_$(date +%Y%m%d).sql.gz
```

2. **Check corruption:**
```bash
docker exec -it bbb-postgres psql -U bbb_user -d bbb_pipeline

-- In psql:
SELECT * FROM pg_stat_database WHERE datname = 'bbb_pipeline';
REINDEX DATABASE bbb_pipeline;
```

3. **Restore from backup if needed:**
```bash
gunzip < /data/bbb/backups/db_backup_latest.sql.gz | \
  docker exec -i bbb-postgres psql -U bbb_user -d bbb_pipeline
```

## Getting Help

1. **Check logs first** - 90% of issues are in logs
2. **Search this guide** - Most common issues documented
3. **Check GitHub issues** - Someone may have reported similar issue
4. **Collect diagnostics:**
```bash
# Create diagnostic bundle
./scripts/collect_diagnostics.sh > diagnostics_$(date +%Y%m%d).txt
```

5. **Open GitHub issue** with:
   - Error message
   - Relevant logs
   - Configuration (redact secrets!)
   - Steps to reproduce

## Next Steps

- **[Operations Guide](operations.md)** - Day-to-day operations
- **[Monitoring Guide](monitoring.md)** - Prevent issues with monitoring
- **[Configuration Guide](configuration.md)** - Tune settings
