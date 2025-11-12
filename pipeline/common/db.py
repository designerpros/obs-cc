"""
Database utilities and models for post-stream extraction pipeline
"""
import os
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID, uuid4
from contextlib import asynccontextmanager

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, TIMESTAMP,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, Date, BigInteger
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from loguru import logger

# Database connection
DATABASE_URL = (
    f"postgresql+asyncpg://{os.getenv('POSTGRES_USER', 'pipeline_worker')}:"
    f"{os.getenv('POSTGRES_PASSWORD')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/"
    f"{os.getenv('POSTGRES_DB', 'stream_extraction')}"
)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv('DEBUG', 'false').lower() == 'true',
    pool_size=int(os.getenv('DB_POOL_SIZE', '20')),
    max_overflow=int(os.getenv('DB_MAX_OVERFLOW', '10')),
    pool_recycle=3600,  # Recycle connections after 1 hour to prevent stale connections
    pool_pre_ping=True,  # Verify connections before using them
    pool_timeout=30,  # Timeout for getting connection from pool
)

# Create session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base model
Base = declarative_base()


@asynccontextmanager
async def get_db() -> AsyncSession:
    """Get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ============================================================================
# MODELS (matching db/schema.sql)
# ============================================================================

class Stream(Base):
    """Represents a single livestream session"""
    __tablename__ = 'streams'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    stream_date = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    title = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    # Source files
    cam_main_path = Column(Text, nullable=False)
    cam_guest_path = Column(Text, nullable=True)
    cam_overhead_path = Column(Text, nullable=True)
    cam_screen_path = Column(Text, nullable=True)
    cam_online_caller_path = Column(Text, nullable=True)
    live_mix_path = Column(Text, nullable=False)

    # Processing status
    status = Column(String(50), nullable=False, default='pending')

    # Metadata
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    # S3 archival
    s3_source_uploaded = Column(Boolean, default=False)
    s3_source_key = Column(Text, nullable=True)

    # Relationships
    transcripts = relationship("Transcript", back_populates="stream", cascade="all, delete-orphan")
    topics = relationship("Topic", back_populates="stream", cascade="all, delete-orphan")
    extractions = relationship("Extraction", back_populates="stream", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="stream", cascade="all, delete-orphan")


class Transcript(Base):
    """Full transcription with speaker diarization"""
    __tablename__ = 'transcripts'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    stream_id = Column(PGUUID(as_uuid=True), ForeignKey('streams.id', ondelete='CASCADE'), nullable=False)

    # Transcript data
    full_text = Column(Text, nullable=False)
    language = Column(String(10), default='en')
    confidence_score = Column(Float, nullable=True)

    # Speaker diarization
    speakers = Column(JSONB, nullable=True)  # [{speaker_id, name, total_duration}]

    # Vector embedding (768 dim)
    embedding = Column(Vector(768), nullable=True)

    # Processing metadata
    transcription_engine = Column(String(50), default='whisper-large-v3')
    processing_duration_seconds = Column(Float, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())

    # Relationships
    stream = relationship("Stream", back_populates="transcripts")
    segments = relationship("TranscriptSegment", back_populates="transcript", cascade="all, delete-orphan")


class TranscriptSegment(Base):
    """Time-aligned transcript chunks"""
    __tablename__ = 'transcript_segments'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    transcript_id = Column(PGUUID(as_uuid=True), ForeignKey('transcripts.id', ondelete='CASCADE'), nullable=False)
    stream_id = Column(PGUUID(as_uuid=True), ForeignKey('streams.id', ondelete='CASCADE'), nullable=False)

    # Timing
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)

    # Content
    text = Column(Text, nullable=False)
    speaker_id = Column(String(50), nullable=True)
    speaker_name = Column(String(100), nullable=True)

    # Metadata
    confidence = Column(Float, nullable=True)
    words = Column(JSONB, nullable=True)  # [{word, start, end, confidence}]

    # Vector embedding
    embedding = Column(Vector(768), nullable=True)

    # Relationships
    transcript = relationship("Transcript", back_populates="segments")
    stream = relationship("Stream")


class Topic(Base):
    """AI-identified topic segments"""
    __tablename__ = 'topics'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    stream_id = Column(PGUUID(as_uuid=True), ForeignKey('streams.id', ondelete='CASCADE'), nullable=False)

    # Topic identification
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    keywords = Column(ARRAY(Text), nullable=True)

    # Timing
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)

    # AI analysis scores
    coherence_score = Column(Float, nullable=False)
    standalone_viability_score = Column(Float, nullable=False)
    engagement_score = Column(Float, nullable=True)
    energy_level = Column(Float, nullable=True)

    # LLM consensus
    llm_scores = Column(JSONB, nullable=True)  # {grok: 85, gpt4: 90, ...}
    consensus_score = Column(Float, nullable=True)
    consensus_approved = Column(Boolean, default=False)

    # Vector embedding
    embedding = Column(Vector(768), nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())

    # Relationships
    stream = relationship("Stream", back_populates="topics")
    extractions = relationship("Extraction", back_populates="topic")


class Extraction(Base):
    """Generated video clips (longs + shorts)"""
    __tablename__ = 'extractions'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    stream_id = Column(PGUUID(as_uuid=True), ForeignKey('streams.id', ondelete='CASCADE'), nullable=False)
    topic_id = Column(PGUUID(as_uuid=True), ForeignKey('topics.id', ondelete='SET NULL'), nullable=True)

    # Extraction type
    type = Column(String(20), nullable=False)  # 'long' or 'short'
    format = Column(String(20), nullable=False)  # 'landscape' or 'portrait'

    # Timing
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)

    # Video production
    status = Column(String(50), nullable=False, default='pending')

    # Files
    video_path = Column(Text, nullable=True)
    thumbnail_path = Column(Text, nullable=True)
    video_size_bytes = Column(BigInteger, nullable=True)

    # B-roll
    broll_style = Column(String(50), nullable=True)
    broll_count = Column(Integer, default=0)

    # Virality engine
    hook_text = Column(Text, nullable=True)
    hook_audio_path = Column(Text, nullable=True)
    hook_duration = Column(Float, nullable=True)

    # LLM approval
    extraction_approved = Column(Boolean, default=False)
    llm_extraction_scores = Column(JSONB, nullable=True)

    # S3 archival
    s3_uploaded = Column(Boolean, default=False)
    s3_key = Column(Text, nullable=True)
    s3_lifecycle_delete_date = Column(Date, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    rendered_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    stream = relationship("Stream", back_populates="extractions")
    topic = relationship("Topic", back_populates="extractions")
    posts = relationship("PlatformPost", back_populates="extraction", cascade="all, delete-orphan")


class PlatformPost(Base):
    """Track multi-platform posting via LATE API"""
    __tablename__ = 'platform_posts'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    extraction_id = Column(PGUUID(as_uuid=True), ForeignKey('extractions.id', ondelete='CASCADE'), nullable=False)

    # Platform info
    platform = Column(String(50), nullable=False)

    # Platform-specific metadata
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    tags = Column(ARRAY(Text), nullable=True)
    hashtags = Column(ARRAY(Text), nullable=True)
    thumbnail_path = Column(Text, nullable=True)

    # Posting status
    status = Column(String(50), nullable=False, default='pending')
    scheduled_time = Column(TIMESTAMP(timezone=True), nullable=True)
    posted_time = Column(TIMESTAMP(timezone=True), nullable=True)

    # Platform response
    platform_id = Column(Text, nullable=True)
    platform_url = Column(Text, nullable=True)
    late_job_id = Column(Text, nullable=True)

    # A/B testing
    thumbnail_variant = Column(Integer, default=1)
    thumbnail_swapped = Column(Boolean, default=False)
    swap_reason = Column(Text, nullable=True)

    # Analytics
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    ctr_percent = Column(Float, nullable=True)
    avg_watch_time_seconds = Column(Float, nullable=True)
    retention_percent = Column(Float, nullable=True)

    last_analytics_update = Column(TIMESTAMP(timezone=True), nullable=True)

    # LLM metadata approval
    metadata_approved = Column(Boolean, default=False)
    llm_metadata_scores = Column(JSONB, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())

    # Relationships
    extraction = relationship("Extraction", back_populates="posts")

    __table_args__ = (
        UniqueConstraint('extraction_id', 'platform', name='unique_extraction_platform'),
    )


class BRollLibrary(Base):
    """Reusable AI-generated graphics"""
    __tablename__ = 'broll_library'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    # Visual content
    file_path = Column(Text, nullable=False, unique=True)
    style = Column(String(50), nullable=False)
    prompt = Column(Text, nullable=False)
    file_size_bytes = Column(BigInteger, nullable=True)
    duration_seconds = Column(Float, default=4.0)

    # Categorization
    topic_category = Column(String(100), nullable=True)
    keywords = Column(ARRAY(Text), nullable=True)
    mood = Column(String(50), nullable=True)

    # Vector embedding
    embedding = Column(Vector(768), nullable=True)

    # Usage tracking
    usage_count = Column(Integer, default=0)
    last_used_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Quality scores
    quality_score = Column(Float, nullable=True)
    manual_rating = Column(Integer, nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())


class Job(Base):
    """Job queue tracking"""
    __tablename__ = 'jobs'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    stream_id = Column(PGUUID(as_uuid=True), ForeignKey('streams.id', ondelete='CASCADE'), nullable=True)

    # Job details
    job_type = Column(String(50), nullable=False)
    priority = Column(Integer, default=0)

    # Status
    status = Column(String(50), nullable=False, default='pending')

    # Worker info
    worker_id = Column(String(100), nullable=True)
    worker_node = Column(String(100), nullable=True)
    gpu_id = Column(String(50), nullable=True)

    # Timing
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Progress tracking
    progress_percent = Column(Float, default=0)
    current_step = Column(Text, nullable=True)

    # Payload and result
    payload = Column(JSONB, nullable=True)
    result = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)

    # Retry logic
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

    # Relationships
    stream = relationship("Stream", back_populates="jobs")


class PlatformRule(Base):
    """Platform posting rules (best practices)"""
    __tablename__ = 'platform_rules'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    platform = Column(String(50), nullable=False, unique=True)

    # Posting limits
    max_posts_per_day = Column(Integer, nullable=True)
    min_interval_minutes = Column(Integer, nullable=True)

    # Optimal posting times
    optimal_hours = Column(ARRAY(Integer), nullable=True)
    avoid_hours = Column(ARRAY(Integer), nullable=True)

    # Content preferences
    preferred_types = Column(ARRAY(Text), nullable=True)
    max_duration_seconds = Column(Integer, nullable=True)
    min_duration_seconds = Column(Integer, nullable=True)

    # Metadata requirements
    max_title_length = Column(Integer, nullable=True)
    max_description_length = Column(Integer, nullable=True)
    supports_hashtags = Column(Boolean, default=True)
    supports_chapters = Column(Boolean, default=False)

    # Strategy
    priority_score = Column(Integer, default=50)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.now())


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def create_stream(
    cam_main_path: str,
    live_mix_path: str,
    cam_guest_path: Optional[str] = None,
    cam_overhead_path: Optional[str] = None,
    cam_screen_path: Optional[str] = None,
    cam_online_caller_path: Optional[str] = None,
    title: Optional[str] = None,
) -> UUID:
    """Create a new stream entry"""
    async with get_db() as db:
        stream = Stream(
            cam_main_path=cam_main_path,
            live_mix_path=live_mix_path,
            cam_guest_path=cam_guest_path,
            cam_overhead_path=cam_overhead_path,
            cam_screen_path=cam_screen_path,
            cam_online_caller_path=cam_online_caller_path,
            title=title,
            status='pending',
        )
        db.add(stream)
        await db.commit()
        await db.refresh(stream)
        logger.info(f"Created stream {stream.id}")
        return stream.id


async def update_stream_status(stream_id: UUID, status: str, error_message: Optional[str] = None):
    """Update stream processing status"""
    async with get_db() as db:
        result = await db.execute(
            "UPDATE streams SET status = :status, error_message = :error_message, updated_at = NOW() "
            "WHERE id = :stream_id",
            {"status": status, "error_message": error_message, "stream_id": stream_id}
        )
        await db.commit()
        logger.info(f"Updated stream {stream_id} status to {status}")


async def create_job(
    job_type: str,
    stream_id: Optional[UUID] = None,
    payload: Optional[Dict[str, Any]] = None,
    priority: int = 0,
) -> UUID:
    """Create a new job"""
    async with get_db() as db:
        job = Job(
            job_type=job_type,
            stream_id=stream_id,
            payload=payload,
            priority=priority,
            status='pending',
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        logger.info(f"Created job {job.id} (type: {job_type})")
        return job.id


async def get_pending_jobs(job_type: Optional[str] = None, limit: int = 10) -> List[Job]:
    """Get pending jobs"""
    async with get_db() as db:
        query = "SELECT * FROM jobs WHERE status = 'pending'"
        params = {"limit": limit}

        if job_type:
            query += " AND job_type = :job_type"
            params["job_type"] = job_type

        query += " ORDER BY priority DESC, created_at ASC LIMIT :limit"

        result = await db.execute(text(query), params)
        return result.fetchall()


logger.info("Database models initialized")
