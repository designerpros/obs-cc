"""
Posting Worker
Schedules and posts content to all platforms via Late API
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID
from typing import List, Dict, Any
from datetime import datetime, timedelta

from loguru import logger
from fastapi import FastAPI
import uvicorn

# Add parent to path
sys.path.append(str(Path(__file__).parent.parent))

from common.config import config
from common.queue import init_queue, close_queue, job_queue, JobType
from common.db import get_db, Extraction, PlatformPost
from common.notifications import init_notifications, close_notifications, notifier

from .late_client import LateAPIClient

# Initialize FastAPI for health checks
app = FastAPI(title="Posting Worker", version="1.0.0")

# Global instances
late_client: LateAPIClient = None
worker_id = f"posting-{os.getenv('HOSTNAME', 'local')}"


@app.on_event("startup")
async def startup():
    """Initialize services on startup"""
    global late_client

    logger.info("Starting Posting Worker...")

    # Initialize dependencies
    await init_queue()
    await init_notifications()

    # Initialize Late API client
    late_client = LateAPIClient()
    await late_client.initialize()

    # Start job processing loop
    asyncio.create_task(process_jobs())

    logger.info("Posting Worker started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down Posting Worker...")

    if late_client:
        await late_client.cleanup()

    await close_queue()
    await close_notifications()

    logger.info("Posting Worker shut down")


async def process_jobs():
    """Main job processing loop"""
    logger.info("Job processing loop started")

    while True:
        try:
            # Dequeue next posting job
            job_data = await job_queue.dequeue(
                job_type=JobType.POST,
                worker_id=worker_id,
                timeout=10,
            )

            if not job_data:
                await asyncio.sleep(5)
                continue

            job_id = UUID(job_data['job_id'])
            stream_id = UUID(job_data['stream_id'])
            payload = job_data['payload']

            logger.info(f"Processing job {job_id} for stream {stream_id}")

            try:
                await process_posting(
                    job_id=job_id,
                    stream_id=stream_id,
                    payload=payload,
                )

                await job_queue.complete_job(job_id)
                logger.info(f"Job {job_id} completed successfully")

            except Exception as e:
                logger.exception(f"Error processing job {job_id}: {e}")
                await job_queue.fail_job(job_id=job_id, error_message=str(e), retry=True)
                await notifier.notify(
                    f"❌ Posting failed for stream {stream_id}: {str(e)}",
                    level="error",
                    stream_id=stream_id,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Error in job processing loop: {e}")
            await asyncio.sleep(10)


async def process_posting(
    job_id: UUID,
    stream_id: UUID,
    payload: dict,
):
    """Schedule and post content to all platforms"""
    extraction_ids = [UUID(eid) for eid in payload['extraction_ids']]

    logger.info(f"Processing posting for {len(extraction_ids)} extractions")

    # Load all platform posts for these extractions
    async with get_db() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(PlatformPost, Extraction)
            .join(Extraction, PlatformPost.extraction_id == Extraction.id)
            .where(PlatformPost.extraction_id.in_(extraction_ids))
            .where(PlatformPost.status == 'pending')
            .order_by(Extraction.type, PlatformPost.platform)
        )
        posts_with_extractions = result.all()

    total_posts = len(posts_with_extractions)
    processed = 0

    logger.info(f"Found {total_posts} posts to schedule")

    # Group by extraction type for staggered scheduling
    posts_by_type = {
        'short': [],
        'micro': [],
        'medium': [],
        'long': [],
    }

    for platform_post, extraction in posts_with_extractions:
        posts_by_type[extraction.type].append((platform_post, extraction))

    # Schedule posts with staggering:
    # - Shorts/micros: immediate
    # - Mediums: +6 hours
    # - Longs: +24 hours
    base_time = datetime.utcnow()

    for extraction_type, type_posts in posts_by_type.items():
        if not type_posts:
            continue

        # Calculate delay offset
        if extraction_type in ['short', 'micro']:
            delay_hours = 0
        elif extraction_type == 'medium':
            delay_hours = 6
        else:  # long
            delay_hours = 24

        schedule_time = base_time + timedelta(hours=delay_hours)

        logger.info(f"Scheduling {len(type_posts)} {extraction_type} posts for {schedule_time}")

        # Process each post
        for platform_post, extraction in type_posts:
            await job_queue.update_progress(
                job_id=job_id,
                progress_percent=int((processed / total_posts) * 100),
                current_step=f"Scheduling {platform_post.platform} - {extraction.type}",
            )

            try:
                # Check if metadata is approved
                if not platform_post.metadata_approved:
                    logger.warning(
                        f"Post {platform_post.id} metadata not approved, "
                        f"skipping (scores: {platform_post.llm_metadata_scores})"
                    )
                    platform_post.status = 'skipped'
                    platform_post.error_message = 'Metadata not approved by LLM consensus'
                    async with get_db() as db:
                        db.add(platform_post)
                        await db.commit()
                    processed += 1
                    continue

                # Apply platform-specific scheduling rules
                final_schedule_time = apply_platform_scheduling_rules(
                    platform=platform_post.platform,
                    base_schedule_time=schedule_time,
                )

                # Schedule post via Late API
                late_response = await late_client.schedule_post(
                    platforms=[platform_post.platform],
                    video_url=str(extraction.video_path),
                    title=platform_post.title,
                    description=platform_post.description,
                    hashtags=platform_post.hashtags,
                    thumbnail_url=platform_post.thumbnail_path,
                    schedule_time=final_schedule_time,
                )

                # Update database with Late API response
                platform_post.status = 'scheduled'
                platform_post.late_post_id = late_response.get('post_id')
                platform_post.scheduled_time = final_schedule_time
                platform_post.late_response = late_response

                async with get_db() as db:
                    db.add(platform_post)
                    await db.commit()

                logger.info(
                    f"Scheduled {platform_post.platform} post "
                    f"(Late ID: {platform_post.late_post_id}) for {final_schedule_time}"
                )

            except Exception as e:
                logger.error(f"Error scheduling post {platform_post.id}: {e}")
                platform_post.status = 'failed'
                platform_post.error_message = str(e)
                async with get_db() as db:
                    db.add(platform_post)
                    await db.commit()

            processed += 1

            # Rate limiting between posts
            await asyncio.sleep(2)

    logger.info(f"Scheduled {processed} posts across all platforms")

    # Send completion notification
    await notifier.notify(
        f"✅ Posting completed for stream {stream_id}\n"
        f"📊 {processed} posts scheduled across {len(posts_by_type)} extraction types",
        level="success",
        stream_id=stream_id,
    )

    # Enqueue archival job
    await job_queue.enqueue(
        job_type=JobType.ARCHIVE,
        stream_id=stream_id,
        payload={
            'extraction_ids': [str(eid) for eid in extraction_ids],
        },
        priority=40,
    )


def apply_platform_scheduling_rules(
    platform: str,
    base_schedule_time: datetime,
) -> datetime:
    """
    Apply platform-specific optimal posting times

    Args:
        platform: Platform name
        base_schedule_time: Initial schedule time

    Returns:
        Adjusted schedule time
    """
    # Get platform rules from config
    platform_rules = config.get(f'posting.platforms.{platform}', {})

    # Get optimal hours (e.g., [9, 12, 15, 18, 21])
    optimal_hours = platform_rules.get('optimal_hours', [])

    if not optimal_hours:
        # No optimization, use base time
        return base_schedule_time

    # Find nearest optimal hour
    base_hour = base_schedule_time.hour
    nearest_hour = min(optimal_hours, key=lambda h: abs(h - base_hour))

    # Adjust to nearest optimal hour
    adjusted_time = base_schedule_time.replace(
        hour=nearest_hour,
        minute=0,
        second=0,
        microsecond=0,
    )

    # If we went backwards, add a day
    if adjusted_time < base_schedule_time:
        adjusted_time += timedelta(days=1)

    logger.debug(
        f"{platform}: Adjusted schedule from {base_schedule_time} "
        f"to {adjusted_time} (optimal hour: {nearest_hour})"
    )

    return adjusted_time


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Posting Worker",
        "worker_id": worker_id,
        "status": "running",
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.getenv('PORT', '9095'))
    logger.info(f"Starting Posting Worker on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
