"""
Redis job queue utilities for post-stream extraction pipeline
"""
import os
import json
import asyncio
from typing import Optional, Dict, Any, List
from uuid import UUID, uuid4
from datetime import datetime
from enum import Enum

import redis.asyncio as redis
from loguru import logger

# Redis connection
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD')
REDIS_DB = int(os.getenv('REDIS_DB', '0'))

# Create Redis client
redis_client: Optional[redis.Redis] = None


class JobType(str, Enum):
    """Job types for pipeline stages"""
    UPLOAD = "upload"
    VALIDATE = "validate"
    REMIX = "remix"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    EXTRACT = "extract"
    GENERATE_BROLL = "generate_broll"
    GENERATE_HOOKS = "generate_hooks"
    PROCESS_AUDIO = "process_audio"
    GENERATE_METADATA = "generate_metadata"
    GENERATE_THUMBNAILS = "generate_thumbnails"
    RENDER = "render"
    POST = "post"
    ARCHIVE = "archive"


class JobStatus(str, Enum):
    """Job statuses"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


async def get_redis() -> redis.Redis:
    """Get Redis client"""
    global redis_client
    if redis_client is None:
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            db=REDIS_DB,
            decode_responses=True,
        )
        # Test connection
        await redis_client.ping()
        logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    return redis_client


async def close_redis():
    """Close Redis connection"""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None
        logger.info("Closed Redis connection")


class JobQueue:
    """Redis-backed job queue"""

    def __init__(self):
        self.redis: Optional[redis.Redis] = None

    async def connect(self):
        """Connect to Redis"""
        self.redis = await get_redis()

    async def enqueue(
        self,
        job_type: JobType,
        stream_id: UUID,
        payload: Dict[str, Any],
        priority: int = 0,
    ) -> UUID:
        """
        Enqueue a new job

        Args:
            job_type: Type of job
            stream_id: Stream ID
            payload: Job payload data
            priority: Job priority (higher = more urgent)

        Returns:
            Job ID
        """
        job_id = uuid4()

        job_data = {
            "job_id": str(job_id),
            "job_type": job_type.value,
            "stream_id": str(stream_id),
            "status": JobStatus.PENDING.value,
            "priority": priority,
            "payload": json.dumps(payload),
            "created_at": datetime.utcnow().isoformat(),
            "started_at": None,
            "completed_at": None,
            "worker_id": None,
            "retry_count": 0,
            "error_message": None,
        }

        # Store job data
        await self.redis.hset(f"job:{job_id}", mapping=job_data)

        # Add to priority queue
        queue_key = f"queue:{job_type.value}"
        await self.redis.zadd(queue_key, {str(job_id): -priority})

        # Add to stream's job list
        await self.redis.rpush(f"stream:{stream_id}:jobs", str(job_id))

        logger.info(f"Enqueued job {job_id} (type: {job_type.value}, priority: {priority})")
        return job_id

    async def dequeue(
        self,
        job_type: JobType,
        worker_id: str,
        timeout: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """
        Dequeue next job of given type

        Args:
            job_type: Type of job to dequeue
            worker_id: Worker ID claiming this job
            timeout: Timeout in seconds

        Returns:
            Job data or None if no jobs available
        """
        queue_key = f"queue:{job_type.value}"

        # Pop highest priority job (lowest score due to negative priority)
        result = await self.redis.bzpopmin(queue_key, timeout=timeout)

        if not result:
            return None

        # Validate result structure before unpacking
        if not isinstance(result, (tuple, list)) or len(result) != 3:
            logger.error(f"Unexpected bzpopmin result format: {result}")
            return None

        _, job_id_bytes, _ = result
        job_id = job_id_bytes if isinstance(job_id_bytes, str) else job_id_bytes.decode()

        # Get job data
        job_data = await self.redis.hgetall(f"job:{job_id}")

        if not job_data:
            logger.warning(f"Job {job_id} not found in Redis")
            return None

        # Update job status
        await self.redis.hset(
            f"job:{job_id}",
            mapping={
                "status": JobStatus.RUNNING.value,
                "worker_id": worker_id,
                "started_at": datetime.utcnow().isoformat(),
            }
        )

        # Parse payload with error handling
        try:
            job_data["payload"] = json.loads(job_data["payload"]) if job_data.get("payload") else {}
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse job payload for {job_id}: {e}")
            job_data["payload"] = {}

        logger.info(f"Dequeued job {job_id} (type: {job_type.value}, worker: {worker_id})")
        return job_data

    async def complete_job(
        self,
        job_id: UUID,
        result: Optional[Dict[str, Any]] = None,
    ):
        """Mark job as completed"""
        await self.redis.hset(
            f"job:{job_id}",
            mapping={
                "status": JobStatus.COMPLETED.value,
                "completed_at": datetime.utcnow().isoformat(),
                "result": json.dumps(result) if result else "{}",
            }
        )
        logger.info(f"Completed job {job_id}")

    async def fail_job(
        self,
        job_id: UUID,
        error_message: str,
        retry: bool = True,
        max_retries: int = 3,
    ):
        """
        Mark job as failed

        Args:
            job_id: Job ID
            error_message: Error message
            retry: Whether to retry
            max_retries: Maximum retry count
        """
        job_data = await self.redis.hgetall(f"job:{job_id}")
        retry_count = int(job_data.get("retry_count") or 0)  # Handle None gracefully

        if retry and retry_count < max_retries:
            # Retry
            new_retry_count = retry_count + 1
            await self.redis.hset(
                f"job:{job_id}",
                mapping={
                    "status": JobStatus.PENDING.value,
                    "retry_count": new_retry_count,
                    "error_message": error_message,
                    "worker_id": None,
                }
            )

            # Re-enqueue with lower priority (with minimum bound)
            job_type = job_data["job_type"]
            current_priority = int(job_data.get("priority") or 0)
            priority = max(current_priority - 10, -100)  # Minimum priority bound
            queue_key = f"queue:{job_type}"
            await self.redis.zadd(queue_key, {str(job_id): -priority})

            logger.warning(f"Job {job_id} failed, retrying ({new_retry_count}/{max_retries}): {error_message}")
        else:
            # Permanent failure
            await self.redis.hset(
                f"job:{job_id}",
                mapping={
                    "status": JobStatus.FAILED.value,
                    "completed_at": datetime.utcnow().isoformat(),
                    "error_message": error_message,
                }
            )
            logger.error(f"Job {job_id} permanently failed: {error_message}")

    async def get_job(self, job_id: UUID) -> Optional[Dict[str, Any]]:
        """Get job data"""
        job_data = await self.redis.hgetall(f"job:{job_id}")
        if job_data:
            try:
                job_data["payload"] = json.loads(job_data.get("payload", "{}"))
                job_data["result"] = json.loads(job_data.get("result", "{}"))
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to parse job data for {job_id}: {e}")
                job_data["payload"] = {}
                job_data["result"] = {}
        return job_data if job_data else None

    async def get_stream_jobs(self, stream_id: UUID) -> List[Dict[str, Any]]:
        """Get all jobs for a stream"""
        job_ids = await self.redis.lrange(f"stream:{stream_id}:jobs", 0, -1)
        jobs = []
        for job_id in job_ids:
            job_data = await self.get_job(UUID(job_id))
            if job_data:
                jobs.append(job_data)
        return jobs

    async def cancel_job(self, job_id: UUID):
        """Cancel a pending job"""
        job_data = await self.redis.hgetall(f"job:{job_id}")

        if job_data.get("status") == JobStatus.PENDING.value:
            # Remove from queue
            job_type = job_data["job_type"]
            queue_key = f"queue:{job_type}"
            await self.redis.zrem(queue_key, str(job_id))

            # Update status
            await self.redis.hset(
                f"job:{job_id}",
                mapping={
                    "status": JobStatus.CANCELLED.value,
                    "completed_at": datetime.utcnow().isoformat(),
                }
            )
            logger.info(f"Cancelled job {job_id}")
        else:
            logger.warning(f"Cannot cancel job {job_id} with status {job_data.get('status')}")

    async def get_queue_depth(self, job_type: JobType) -> int:
        """Get number of pending jobs in queue"""
        queue_key = f"queue:{job_type.value}"
        return await self.redis.zcard(queue_key)

    async def update_progress(
        self,
        job_id: UUID,
        progress_percent: float,
        current_step: str,
    ):
        """Update job progress"""
        await self.redis.hset(
            f"job:{job_id}",
            mapping={
                "progress_percent": progress_percent,
                "current_step": current_step,
            }
        )

    async def set_cache(self, key: str, value: Any, ttl: int = 3600):
        """Set cached value"""
        await self.redis.setex(key, ttl, json.dumps(value))

    async def get_cache(self, key: str) -> Optional[Any]:
        """Get cached value"""
        value = await self.redis.get(key)
        if not value:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse cached value for key {key}: {e}")
            return None

    async def delete_cache(self, key: str):
        """Delete cached value"""
        await self.redis.delete(key)


# Global job queue instance
job_queue = JobQueue()


async def init_queue():
    """Initialize job queue"""
    await job_queue.connect()
    logger.info("Job queue initialized")


async def close_queue():
    """Close job queue"""
    await close_redis()
    logger.info("Job queue closed")


logger.info("Redis queue utilities loaded")
