-- Post-Stream Extraction Pipeline Database Schema
-- Requires PostgreSQL 14+ with pgvector extension

-- Enable pgvector extension for similarity search
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- Streams: Represents a single livestream session
CREATE TABLE streams (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stream_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title TEXT,
    duration_seconds INTEGER,

    -- Source files
    cam_main_path TEXT NOT NULL,
    cam_guest_path TEXT,
    cam_overhead_path TEXT,
    cam_screen_path TEXT,
    cam_online_caller_path TEXT,
    live_mix_path TEXT NOT NULL,

    -- Processing status
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'ingesting', 'transcribing', 'analyzing',
        'rendering', 'posting', 'completed', 'failed'
    )),

    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,

    -- S3 archival
    s3_source_uploaded BOOLEAN DEFAULT FALSE,
    s3_source_key TEXT,

    CONSTRAINT valid_source_files CHECK (
        cam_main_path IS NOT NULL AND live_mix_path IS NOT NULL
    )
);

CREATE INDEX idx_streams_status ON streams(status);
CREATE INDEX idx_streams_created_at ON streams(created_at DESC);


-- Transcripts: Full transcription with speaker diarization
CREATE TABLE transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stream_id UUID NOT NULL REFERENCES streams(id) ON DELETE CASCADE,

    -- Transcript data
    full_text TEXT NOT NULL,
    language TEXT DEFAULT 'en',
    confidence_score FLOAT,

    -- Speaker diarization
    speakers JSONB, -- Array of {speaker_id, name, total_duration}

    -- Vector embedding for similarity search (1536 dim for OpenAI, 768 for others)
    embedding vector(768),

    -- Processing metadata
    transcription_engine TEXT DEFAULT 'whisper-large-v3',
    processing_duration_seconds FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_confidence CHECK (confidence_score >= 0 AND confidence_score <= 1)
);

CREATE INDEX idx_transcripts_stream_id ON transcripts(stream_id);
CREATE INDEX idx_transcripts_embedding ON transcripts USING ivfflat (embedding vector_cosine_ops);


-- Transcript Segments: Time-aligned transcript chunks
CREATE TABLE transcript_segments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    transcript_id UUID NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    stream_id UUID NOT NULL REFERENCES streams(id) ON DELETE CASCADE,

    -- Timing
    start_time FLOAT NOT NULL, -- seconds from stream start
    end_time FLOAT NOT NULL,
    duration FLOAT GENERATED ALWAYS AS (end_time - start_time) STORED,

    -- Content
    text TEXT NOT NULL,
    speaker_id TEXT,
    speaker_name TEXT,

    -- Metadata
    confidence FLOAT,
    words JSONB, -- Detailed word-level timing [{word, start, end, confidence}]

    -- Vector embedding for this segment
    embedding vector(768),

    CONSTRAINT valid_timing CHECK (start_time >= 0 AND end_time > start_time)
);

CREATE INDEX idx_segments_transcript_id ON transcript_segments(transcript_id);
CREATE INDEX idx_segments_stream_id ON transcript_segments(stream_id);
CREATE INDEX idx_segments_time ON transcript_segments(start_time, end_time);
CREATE INDEX idx_segments_speaker ON transcript_segments(speaker_id);
CREATE INDEX idx_segments_embedding ON transcript_segments USING ivfflat (embedding vector_cosine_ops);


-- Topics: AI-identified topic segments
CREATE TABLE topics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stream_id UUID NOT NULL REFERENCES streams(id) ON DELETE CASCADE,

    -- Topic identification
    title TEXT NOT NULL,
    description TEXT,
    category TEXT, -- e.g., 'crypto', 'finance', 'policy', 'business'
    keywords TEXT[], -- Array of relevant keywords

    -- Timing (references transcript segments)
    start_time FLOAT NOT NULL,
    end_time FLOAT NOT NULL,
    duration FLOAT GENERATED ALWAYS AS (end_time - start_time) STORED,

    -- AI analysis scores
    coherence_score FLOAT NOT NULL, -- 0-100
    standalone_viability_score FLOAT NOT NULL, -- 0-100
    engagement_score FLOAT, -- 0-100
    energy_level FLOAT, -- 0-100

    -- LLM consensus (from Grok, GPT-4, Claude, Perplexity)
    llm_scores JSONB, -- {grok: 85, gpt4: 90, claude: 88, perplexity: 87}
    consensus_score FLOAT, -- Average of LLM scores
    consensus_approved BOOLEAN DEFAULT FALSE, -- 3/4 LLMs >= 80

    -- Vector embedding for topic
    embedding vector(768),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_scores CHECK (
        coherence_score >= 0 AND coherence_score <= 100 AND
        standalone_viability_score >= 0 AND standalone_viability_score <= 100
    )
);

CREATE INDEX idx_topics_stream_id ON topics(stream_id);
CREATE INDEX idx_topics_consensus ON topics(consensus_approved, consensus_score DESC);
CREATE INDEX idx_topics_time ON topics(start_time, end_time);
CREATE INDEX idx_topics_category ON topics(category);
CREATE INDEX idx_topics_embedding ON topics USING ivfflat (embedding vector_cosine_ops);


-- Extractions: Generated video clips (longs + shorts)
CREATE TABLE extractions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stream_id UUID NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
    topic_id UUID REFERENCES topics(id) ON DELETE SET NULL,

    -- Extraction type
    type TEXT NOT NULL CHECK (type IN ('long', 'short')),
    format TEXT NOT NULL CHECK (format IN ('landscape', 'portrait')),

    -- Timing
    start_time FLOAT NOT NULL,
    end_time FLOAT NOT NULL,
    duration FLOAT GENERATED ALWAYS AS (end_time - start_time) STORED,

    -- Video production
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'rendering', 'rendered', 'uploading',
        'posted', 'failed'
    )),

    -- Files
    video_path TEXT,
    thumbnail_path TEXT,
    video_size_bytes BIGINT,

    -- B-roll style
    broll_style TEXT, -- e.g., 'ghibli', 'cyberpunk', 'minimalist'
    broll_count INTEGER DEFAULT 0, -- Number of B-roll panels inserted

    -- AI-generated hook (for virality engine)
    hook_text TEXT,
    hook_audio_path TEXT, -- XTTS2 generated audio
    hook_duration FLOAT,

    -- LLM approval
    extraction_approved BOOLEAN DEFAULT FALSE,
    llm_extraction_scores JSONB,

    -- S3 archival
    s3_uploaded BOOLEAN DEFAULT FALSE,
    s3_key TEXT,
    s3_lifecycle_delete_date DATE, -- Auto-delete after 5 years

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rendered_at TIMESTAMPTZ,

    CONSTRAINT valid_duration CHECK (
        (type = 'long' AND duration >= 300 AND duration <= 900) OR -- 5-15 min
        (type = 'short' AND duration >= 30 AND duration <= 90)     -- 30-90 sec
    )
);

CREATE INDEX idx_extractions_stream_id ON extractions(stream_id);
CREATE INDEX idx_extractions_topic_id ON extractions(topic_id);
CREATE INDEX idx_extractions_status ON extractions(status);
CREATE INDEX idx_extractions_type ON extractions(type);


-- Platform Posts: Track multi-platform posting via LATE API
CREATE TABLE platform_posts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    extraction_id UUID NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,

    -- Platform info
    platform TEXT NOT NULL CHECK (platform IN (
        'youtube', 'tiktok', 'instagram', 'facebook', 'x',
        'linkedin', 'pinterest', 'snapchat', 'twitch', 'reddit'
    )),

    -- Platform-specific metadata
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    tags TEXT[],
    hashtags TEXT[],
    thumbnail_path TEXT,

    -- Posting status
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'scheduled', 'uploading', 'posted', 'failed'
    )),
    scheduled_time TIMESTAMPTZ,
    posted_time TIMESTAMPTZ,

    -- Platform response
    platform_id TEXT, -- ID returned by platform (e.g., YouTube video ID)
    platform_url TEXT,
    late_job_id TEXT, -- LATE API job ID

    -- A/B testing (thumbnails)
    thumbnail_variant INTEGER DEFAULT 1, -- 1 or 2
    thumbnail_swapped BOOLEAN DEFAULT FALSE,
    swap_reason TEXT,

    -- Analytics (from LATE API)
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    ctr_percent FLOAT,
    avg_watch_time_seconds FLOAT,
    retention_percent FLOAT,

    last_analytics_update TIMESTAMPTZ,

    -- LLM metadata approval
    metadata_approved BOOLEAN DEFAULT FALSE,
    llm_metadata_scores JSONB, -- {grok: {clickability: 85, seo: 90}, ...}

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT unique_extraction_platform UNIQUE (extraction_id, platform)
);

CREATE INDEX idx_posts_extraction_id ON platform_posts(extraction_id);
CREATE INDEX idx_posts_platform ON platform_posts(platform);
CREATE INDEX idx_posts_status ON platform_posts(status);
CREATE INDEX idx_posts_scheduled_time ON platform_posts(scheduled_time);
CREATE INDEX idx_posts_ctr ON platform_posts(ctr_percent DESC NULLS LAST);


-- B-roll Library: Reusable AI-generated graphics
CREATE TABLE broll_library (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Visual content
    image_path TEXT NOT NULL, -- Path to generated image
    style TEXT NOT NULL, -- 'ghibli', 'cyberpunk', 'minimalist', 'cinematic', 'general'
    prompt TEXT NOT NULL, -- Original generation prompt

    -- Dimensions (for matching)
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,

    -- Deduplication
    content_hash TEXT NOT NULL UNIQUE, -- SHA256 hash for deduplication
    file_size_bytes BIGINT,

    -- Categorization
    topic_category TEXT, -- 'crypto', 'finance', 'policy', etc.
    keywords TEXT[],
    mood TEXT, -- 'energetic', 'calm', 'dramatic', etc.

    -- Vector embedding for semantic search (prompt-based)
    prompt_embedding vector(768),

    -- Usage tracking
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,

    -- Quality scores
    quality_score FLOAT, -- 0-100 auto-calculated (resolution, blur, contrast)
    manual_rating INTEGER, -- 1-5 stars (optional manual curation)

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_quality CHECK (quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)),
    CONSTRAINT valid_dimensions CHECK (width > 0 AND height > 0)
);

CREATE INDEX idx_broll_style ON broll_library(style);
CREATE INDEX idx_broll_dimensions ON broll_library(width, height);
CREATE INDEX idx_broll_category ON broll_library(topic_category);
CREATE INDEX idx_broll_content_hash ON broll_library(content_hash);
CREATE INDEX idx_broll_embedding ON broll_library USING ivfflat (prompt_embedding vector_cosine_ops);
CREATE INDEX idx_broll_usage ON broll_library(usage_count DESC);
CREATE INDEX idx_broll_quality ON broll_library(quality_score DESC NULLS LAST);


-- Jobs Queue: Redis-backed job tracking
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stream_id UUID REFERENCES streams(id) ON DELETE CASCADE,

    -- Job details
    job_type TEXT NOT NULL, -- 'ingest', 'transcribe', 'analyze', 'render', 'post', etc.
    priority INTEGER DEFAULT 0, -- Higher = more urgent

    -- Status
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'running', 'completed', 'failed', 'cancelled'
    )),

    -- Worker info
    worker_id TEXT,
    worker_node TEXT, -- Tailscale node name
    gpu_id TEXT, -- Which GPU is processing

    -- Timing
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds FLOAT,

    -- Progress tracking
    progress_percent FLOAT DEFAULT 0,
    current_step TEXT,

    -- Payload and result
    payload JSONB, -- Input parameters
    result JSONB, -- Output data
    error_message TEXT,

    -- Retry logic
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,

    CONSTRAINT valid_progress CHECK (progress_percent >= 0 AND progress_percent <= 100)
);

CREATE INDEX idx_jobs_stream_id ON jobs(stream_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_type ON jobs(job_type);
CREATE INDEX idx_jobs_priority ON jobs(priority DESC);
CREATE INDEX idx_jobs_created_at ON jobs(created_at);


-- Cost Tracking: Monitor LLM and resource costs
CREATE TABLE cost_tracking (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stream_id UUID REFERENCES streams(id) ON DELETE CASCADE,

    -- Operation details
    operation_type TEXT NOT NULL, -- 'llm_call', 'broll_generation', 'compute', etc.
    model TEXT, -- Model name (claude-3-5-sonnet, gpt-4o, comfyui-sdxl, etc.)

    -- Token usage (for LLM calls)
    input_tokens INTEGER,
    output_tokens INTEGER,

    -- Cost
    cost_usd NUMERIC(10, 6) NOT NULL, -- Cost in USD (6 decimal places for precision)

    -- Metadata
    metadata JSONB, -- Additional operation-specific data
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_tokens CHECK (
        (input_tokens IS NULL AND output_tokens IS NULL) OR
        (input_tokens >= 0 AND output_tokens >= 0)
    ),
    CONSTRAINT valid_cost CHECK (cost_usd >= 0)
);

CREATE INDEX idx_cost_stream_id ON cost_tracking(stream_id);
CREATE INDEX idx_cost_operation_type ON cost_tracking(operation_type);
CREATE INDEX idx_cost_model ON cost_tracking(model);
CREATE INDEX idx_cost_created_at ON cost_tracking(created_at DESC);
CREATE INDEX idx_cost_usd ON cost_tracking(cost_usd DESC);


-- A/B Tests: Title and thumbnail variant testing
CREATE TABLE ab_tests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    extraction_id UUID NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,

    -- Test configuration
    test_type TEXT NOT NULL CHECK (test_type IN ('title', 'thumbnail')),
    variant_count INTEGER NOT NULL CHECK (variant_count >= 2 AND variant_count <= 4),
    variants JSONB NOT NULL, -- {titles: [...]} or {thumbnails: [...]}

    -- Test parameters
    min_sample_size INTEGER NOT NULL DEFAULT 100,
    confidence_level FLOAT NOT NULL DEFAULT 0.95,

    -- Test lifecycle
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'inconclusive', 'cancelled')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,

    -- Results
    winner_variant_index INTEGER, -- 0-indexed winner
    results JSONB, -- Statistical analysis results

    CONSTRAINT valid_confidence CHECK (confidence_level > 0 AND confidence_level < 1),
    CONSTRAINT valid_winner CHECK (winner_variant_index IS NULL OR (winner_variant_index >= 0 AND winner_variant_index < variant_count))
);

CREATE INDEX idx_ab_tests_extraction ON ab_tests(extraction_id);
CREATE INDEX idx_ab_tests_platform ON ab_tests(platform);
CREATE INDEX idx_ab_tests_status ON ab_tests(status);
CREATE INDEX idx_ab_tests_started ON ab_tests(started_at DESC);


-- A/B Test Impressions: Individual impressions for each variant
CREATE TABLE ab_test_impressions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_id UUID NOT NULL REFERENCES ab_tests(id) ON DELETE CASCADE,

    -- Impression data
    variant_index INTEGER NOT NULL, -- Which variant was shown (0-indexed)
    clicked BOOLEAN NOT NULL DEFAULT FALSE, -- Whether user clicked/viewed
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ab_impressions_test ON ab_test_impressions(test_id);
CREATE INDEX idx_ab_impressions_variant ON ab_test_impressions(test_id, variant_index);


-- Feedback Adaptations: Learning history from performance data
CREATE TABLE feedback_adaptations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    adaptations JSONB NOT NULL -- Full adaptation report with changes made
);

CREATE INDEX idx_feedback_created ON feedback_adaptations(created_at DESC);


-- Auto Tuning History: Parameter optimization over time
CREATE TABLE auto_tuning_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tuning_results JSONB NOT NULL -- Complete tuning cycle results with adjustments
);

CREATE INDEX idx_tuning_created ON auto_tuning_history(created_at DESC);


-- ============================================================================
-- VIEWS
-- ============================================================================

-- View: Stream processing summary
CREATE VIEW stream_summary AS
SELECT
    s.id,
    s.stream_date,
    s.title,
    s.status,
    s.duration_seconds,
    COUNT(DISTINCT e.id) FILTER (WHERE e.type = 'long') as long_extractions,
    COUNT(DISTINCT e.id) FILTER (WHERE e.type = 'short') as short_extractions,
    COUNT(DISTINCT pp.id) as total_posts,
    COUNT(DISTINCT pp.id) FILTER (WHERE pp.status = 'posted') as posted_count,
    SUM(pp.views) as total_views,
    AVG(pp.ctr_percent) as avg_ctr,
    s.created_at,
    s.completed_at
FROM streams s
LEFT JOIN extractions e ON s.id = e.stream_id
LEFT JOIN platform_posts pp ON e.id = pp.extraction_id
GROUP BY s.id, s.stream_date, s.title, s.status, s.duration_seconds, s.created_at, s.completed_at;


-- View: Top performing extractions
CREATE VIEW top_extractions AS
SELECT
    e.id,
    e.stream_id,
    e.type,
    t.title as topic_title,
    COUNT(DISTINCT pp.id) as platforms_posted,
    SUM(pp.views) as total_views,
    AVG(pp.ctr_percent) as avg_ctr,
    AVG(pp.retention_percent) as avg_retention,
    MAX(pp.posted_time) as last_posted
FROM extractions e
LEFT JOIN topics t ON e.topic_id = t.id
LEFT JOIN platform_posts pp ON e.id = pp.extraction_id
WHERE e.status = 'posted'
GROUP BY e.id, e.stream_id, e.type, t.title
ORDER BY total_views DESC NULLS LAST;


-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_streams_updated_at BEFORE UPDATE ON streams
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- Search transcripts by semantic similarity
CREATE OR REPLACE FUNCTION search_transcripts_by_similarity(
    query_embedding vector(768),
    similarity_threshold FLOAT DEFAULT 0.7,
    max_results INTEGER DEFAULT 10
)
RETURNS TABLE (
    transcript_id UUID,
    stream_id UUID,
    similarity FLOAT,
    text_preview TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        t.id,
        t.stream_id,
        1 - (t.embedding <=> query_embedding) as similarity,
        LEFT(t.full_text, 200) as text_preview
    FROM transcripts t
    WHERE t.embedding IS NOT NULL
        AND 1 - (t.embedding <=> query_embedding) >= similarity_threshold
    ORDER BY t.embedding <=> query_embedding
    LIMIT max_results;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- SEED DATA
-- ============================================================================

-- Insert platform posting rules (best practices)
CREATE TABLE platform_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform TEXT NOT NULL UNIQUE,

    -- Posting limits
    max_posts_per_day INTEGER,
    min_interval_minutes INTEGER,

    -- Optimal posting times (hour of day, EST)
    optimal_hours INTEGER[],
    avoid_hours INTEGER[],

    -- Content preferences
    preferred_types TEXT[], -- ['long', 'short']
    max_duration_seconds INTEGER,
    min_duration_seconds INTEGER,

    -- Metadata requirements
    max_title_length INTEGER,
    max_description_length INTEGER,
    supports_hashtags BOOLEAN DEFAULT TRUE,
    supports_chapters BOOLEAN DEFAULT FALSE,

    -- Strategy
    priority_score INTEGER DEFAULT 50, -- 0-100, determines posting order

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed platform rules
INSERT INTO platform_rules (platform, max_posts_per_day, min_interval_minutes, optimal_hours, preferred_types, max_duration_seconds, max_title_length, max_description_length, supports_chapters, priority_score) VALUES
('youtube', 6, 120, ARRAY[9,10,11,14,15,16,17], ARRAY['long', 'short'], 900, 100, 5000, TRUE, 100),
('x', 12, 5, ARRAY[8,9,10,11,12,13,14,15,16,17], ARRAY['short', 'long'], 140, 280, 280, FALSE, 95),
('tiktok', 6, 60, ARRAY[9,10,11,12,13,14,15,16,17], ARRAY['short'], 180, 150, 2200, FALSE, 90),
('instagram', 6, 60, ARRAY[9,10,11,13,14,15,17], ARRAY['short'], 90, 150, 2200, FALSE, 85),
('linkedin', 6, 180, ARRAY[8,9,10,11,12,13,14], ARRAY['long', 'short'], 900, 200, 3000, FALSE, 80),
('facebook', 6, 120, ARRAY[9,10,11,13,14,15], ARRAY['long', 'short'], 900, 255, 2000, FALSE, 70),
('reddit', 3, 360, ARRAY[8,9,10,11,12], ARRAY['long'], 900, 300, 40000, FALSE, 60),
('pinterest', 10, 30, ARRAY[8,9,10,14,15,20,21], ARRAY['short'], 90, 100, 500, FALSE, 50),
('twitch', 3, 360, ARRAY[14,15,16,17,18,19,20], ARRAY['long'], 900, 140, 300, FALSE, 40),
('snapchat', 5, 60, ARRAY[9,10,11,12,13,14,15,16,17], ARRAY['short'], 60, 100, 250, FALSE, 30);

-- ============================================================================
-- PERMISSIONS (adjust for your setup)
-- ============================================================================

-- Example: Create pipeline user with appropriate permissions
-- CREATE USER pipeline_worker WITH PASSWORD 'your_secure_password';
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pipeline_worker;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO pipeline_worker;
-- GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO pipeline_worker;
