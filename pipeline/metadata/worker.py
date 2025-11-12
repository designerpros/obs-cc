"""
Metadata Generation Worker
Creates platform-specific titles, descriptions, hashtags, and thumbnails
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID
from typing import List, Dict, Any

from loguru import logger
from fastapi import FastAPI
import uvicorn

# Add parent to path
sys.path.append(str(Path(__file__).parent.parent))

from common.config import config
from common.queue import init_queue, close_queue, job_queue, JobType
from common.db import get_db, Extraction, PlatformPost
from common.notifications import init_notifications, close_notifications, notifier

from .title_generator import TitleGenerator
from .description_generator import DescriptionGenerator
from .thumbnail_generator import ThumbnailGenerator
from .llm_approver import LLMApprover

# Initialize FastAPI for health checks
app = FastAPI(title="Metadata Worker", version="1.0.0")

# Global instances
title_gen: TitleGenerator = None
description_gen: DescriptionGenerator = None
thumbnail_gen: ThumbnailGenerator = None
llm_approver: LLMApprover = None
worker_id = f"metadata-{os.getenv('HOSTNAME', 'local')}"


@app.on_event("startup")
async def startup():
    """Initialize services on startup"""
    global title_gen, description_gen, thumbnail_gen, llm_approver

    logger.info("Starting Metadata Worker...")

    # Initialize dependencies
    await init_queue()
    await init_notifications()

    # Initialize components
    title_gen = TitleGenerator()
    await title_gen.initialize()

    description_gen = DescriptionGenerator()
    await description_gen.initialize()

    thumbnail_gen = ThumbnailGenerator()
    await thumbnail_gen.initialize()

    llm_approver = LLMApprover()
    await llm_approver.initialize()

    # Start job processing loop
    asyncio.create_task(process_jobs())

    logger.info("Metadata Worker started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down Metadata Worker...")

    if title_gen:
        await title_gen.cleanup()

    if description_gen:
        await description_gen.cleanup()

    if thumbnail_gen:
        await thumbnail_gen.cleanup()

    if llm_approver:
        await llm_approver.cleanup()

    await close_queue()
    await close_notifications()

    logger.info("Metadata Worker shut down")


async def process_jobs():
    """Main job processing loop"""
    logger.info("Job processing loop started")

    while True:
        try:
            # Dequeue next metadata job
            job_data = await job_queue.dequeue(
                job_type=JobType.GENERATE_METADATA,
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
                await process_metadata_generation(
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
                    f"❌ Metadata generation failed for stream {stream_id}: {str(e)}",
                    level="error",
                    stream_id=stream_id,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Error in job processing loop: {e}")
            await asyncio.sleep(10)


async def process_metadata_generation(
    job_id: UUID,
    stream_id: UUID,
    payload: dict,
):
    """Generate metadata for all extractions"""
    extraction_ids = [UUID(eid) for eid in payload['extraction_ids']]

    logger.info(f"Generating metadata for {len(extraction_ids)} extractions")

    # Get all platforms
    platforms = config.get('posting.platforms.enabled', [
        'youtube', 'tiktok', 'instagram', 'facebook', 'x',
        'linkedin', 'pinterest', 'snapchat', 'twitch', 'reddit'
    ])

    total_posts = len(extraction_ids) * len(platforms)
    processed = 0

    # Load extractions from database
    async with get_db() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(Extraction).where(Extraction.id.in_(extraction_ids))
        )
        extractions = result.scalars().all()

    # Process each extraction
    for extraction in extractions:
        logger.info(f"Generating metadata for extraction {extraction.id} ({extraction.type})")

        # Generate thumbnail variants
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=int((processed / total_posts) * 100),
            current_step=f"Generating thumbnails for {extraction.type}",
        )

        thumbnail_variants = await thumbnail_gen.generate_thumbnails(
            extraction=extraction,
            count=2,  # A/B testing
        )

        # Generate metadata for each platform
        for platform in platforms:
            # Generate title
            title = await title_gen.generate_title(
                extraction=extraction,
                platform=platform,
            )

            # Generate description
            description = await description_gen.generate_description(
                extraction=extraction,
                platform=platform,
            )

            # Generate hashtags
            hashtags = await description_gen.generate_hashtags(
                extraction=extraction,
                platform=platform,
            )

            # Get LLM approval
            metadata_approved, llm_scores = await llm_approver.approve_metadata(
                title=title,
                description=description,
                hashtags=hashtags,
                platform=platform,
                extraction_type=extraction.type,
            )

            # Create platform post record
            async with get_db() as db:
                platform_post = PlatformPost(
                    extraction_id=extraction.id,
                    platform=platform,
                    title=title,
                    description=description,
                    hashtags=hashtags,
                    thumbnail_path=str(thumbnail_variants[0]) if thumbnail_variants else None,
                    thumbnail_variant=1,
                    metadata_approved=metadata_approved,
                    llm_metadata_scores=llm_scores,
                    status='pending',
                )
                db.add(platform_post)
                await db.commit()

            processed += 1
            await job_queue.update_progress(
                job_id=job_id,
                progress_percent=int((processed / total_posts) * 100),
                current_step=f"Generated {platform} metadata",
            )

    logger.info(f"Generated metadata for {len(extractions)} extractions across {len(platforms)} platforms")

    # Enqueue posting job
    await job_queue.enqueue(
        job_type=JobType.POST,
        stream_id=stream_id,
        payload={
            'extraction_ids': [str(eid) for eid in extraction_ids],
        },
        priority=60,
    )


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Metadata Worker",
        "worker_id": worker_id,
        "status": "running",
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.getenv('PORT', '9094'))
    logger.info(f"Starting Metadata Worker on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
