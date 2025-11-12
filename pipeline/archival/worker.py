"""
Archival Worker
Orchestrates S3 uploads for sources and extractions with lifecycle management
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
from common.db import get_db, Stream, Extraction
from common.notifications import init_notifications, close_notifications, notifier

from .s3_uploader import S3Uploader

# Initialize FastAPI for health checks
app = FastAPI(title="Archival Worker", version="1.0.0")

# Global instances
s3_uploader: S3Uploader = None
worker_id = f"archival-{os.getenv('HOSTNAME', 'local')}"


@app.on_event("startup")
async def startup():
    """Initialize services on startup"""
    global s3_uploader

    logger.info("Starting Archival Worker...")

    # Initialize dependencies
    await init_queue()
    await init_notifications()

    # Initialize S3 uploader
    s3_uploader = S3Uploader()
    await s3_uploader.initialize()

    # Start job processing loop
    asyncio.create_task(process_jobs())

    logger.info("Archival Worker started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down Archival Worker...")

    if s3_uploader:
        await s3_uploader.cleanup()

    await close_queue()
    await close_notifications()

    logger.info("Archival Worker shut down")


async def process_jobs():
    """Main job processing loop"""
    logger.info("Job processing loop started")

    while True:
        try:
            # Dequeue next archival job
            job_data = await job_queue.dequeue(
                job_type=JobType.ARCHIVE,
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
                await process_archival(
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
                    f"❌ Archival failed for stream {stream_id}: {str(e)}",
                    level="error",
                    stream_id=stream_id,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Error in job processing loop: {e}")
            await asyncio.sleep(10)


async def process_archival(
    job_id: UUID,
    stream_id: UUID,
    payload: dict,
):
    """Archive sources and extractions to S3"""
    extraction_ids = [UUID(eid) for eid in payload['extraction_ids']]

    logger.info(f"Archiving stream {stream_id} with {len(extraction_ids)} extractions")

    # Load stream and extractions
    async with get_db() as db:
        from sqlalchemy import select

        # Get stream
        result = await db.execute(
            select(Stream).where(Stream.id == stream_id)
        )
        stream = result.scalar_one()

        # Get extractions
        result = await db.execute(
            select(Extraction).where(Extraction.id.in_(extraction_ids))
        )
        extractions = result.scalars().all()

    # Step 1: Archive source files to Deep Glacier (perpetual)
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=10,
        current_step="Archiving source files to Deep Glacier",
    )

    try:
        source_archive_result = await s3_uploader.upload_sources(
            stream_id=stream_id,
            file_mapping={
                'cam_main': stream.cam_main_path,
                'cam_wide': stream.cam_wide_path,
                'cam_tight': stream.cam_tight_path,
                'cam_bbb': stream.cam_bbb_path,
                'cam_guest1': stream.cam_guest1_path,
                'cam_guest2': stream.cam_guest2_path,
                'cam_guest3': stream.cam_guest3_path,
                'live_mix': stream.live_mix_path,
            },
        )

        # Update stream with S3 paths
        stream.s3_sources_archived = True
        stream.s3_sources_path = source_archive_result['s3_prefix']
        stream.s3_sources_bucket = source_archive_result['bucket']

        async with get_db() as db:
            db.add(stream)
            await db.commit()

        logger.info(
            f"Source files archived: {source_archive_result['total_size_gb']:.2f} GB "
            f"to {source_archive_result['bucket']}/{source_archive_result['s3_prefix']}"
        )

    except Exception as e:
        logger.error(f"Failed to archive source files: {e}")
        # Don't fail the entire job, continue with extractions
        await notifier.notify(
            f"⚠️ Source archival failed for stream {stream_id}: {str(e)}",
            level="warning",
            stream_id=stream_id,
        )

    # Step 2: Archive each extraction to Glacier (5-year auto-delete)
    total_extractions = len(extractions)
    processed = 0

    for extraction in extractions:
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=int(10 + ((processed / total_extractions) * 85)),
            current_step=f"Archiving extraction {extraction.type} ({processed+1}/{total_extractions})",
        )

        try:
            # Upload both landscape and portrait if they exist
            for format_type in ['landscape', 'portrait']:
                video_path = getattr(extraction, f'video_path_{format_type}', None)
                if video_path and Path(video_path).exists():
                    extraction_result = await s3_uploader.upload_extraction(
                        extraction_id=extraction.id,
                        video_path=Path(video_path),
                        format=format_type,
                    )

                    # Update extraction with S3 path
                    if format_type == 'landscape':
                        extraction.s3_archived_landscape = True
                        extraction.s3_path_landscape = extraction_result['s3_key']
                    else:
                        extraction.s3_archived_portrait = True
                        extraction.s3_path_portrait = extraction_result['s3_key']

                    logger.info(
                        f"Extraction {extraction.id} ({format_type}) archived: "
                        f"{extraction_result['size_mb']:.2f} MB"
                    )

            # Update extraction status
            extraction.archived = True
            async with get_db() as db:
                db.add(extraction)
                await db.commit()

        except Exception as e:
            logger.error(f"Failed to archive extraction {extraction.id}: {e}")
            # Continue with next extraction

        processed += 1

    # Step 3: Cleanup local files (optional, based on config)
    cleanup_enabled = config.get('archival.cleanup_after_archive', True)
    cleanup_delay_hours = config.get('archival.cleanup_delay_hours', 48)

    if cleanup_enabled:
        await job_queue.update_progress(
            job_id=job_id,
            progress_percent=95,
            current_step="Scheduling local file cleanup",
        )

        # Schedule cleanup job (delayed)
        from datetime import datetime, timedelta
        cleanup_time = datetime.utcnow() + timedelta(hours=cleanup_delay_hours)

        await job_queue.enqueue(
            job_type=JobType.CLEANUP,
            stream_id=stream_id,
            payload={
                'stream_id': str(stream_id),
                'extraction_ids': [str(eid) for eid in extraction_ids],
            },
            priority=10,  # Low priority
            scheduled_time=cleanup_time,
        )

        logger.info(f"Cleanup job scheduled for {cleanup_time}")

    # Final progress update
    await job_queue.update_progress(
        job_id=job_id,
        progress_percent=100,
        current_step="Archival complete",
    )

    # Calculate total archived size
    total_size_gb = source_archive_result.get('total_size_gb', 0)

    # Send completion notification
    await notifier.notify(
        f"✅ Archival completed for stream {stream_id}\n"
        f"📦 Sources: {total_size_gb:.2f} GB → Deep Glacier (perpetual)\n"
        f"🎬 Extractions: {processed} videos → Glacier (5-year retention)\n"
        f"🗑️ Local cleanup scheduled for {cleanup_delay_hours}h",
        level="success",
        stream_id=stream_id,
    )

    # Mark stream as completed
    stream.status = 'completed'
    stream.completed_at = datetime.utcnow()
    async with get_db() as db:
        db.add(stream)
        await db.commit()

    logger.info(f"Stream {stream_id} processing pipeline completed")


@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Archival Worker",
        "worker_id": worker_id,
        "status": "running",
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.getenv('PORT', '9096'))
    logger.info(f"Starting Archival Worker on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
