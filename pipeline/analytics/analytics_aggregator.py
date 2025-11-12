"""
Analytics Aggregator
Collects and aggregates performance data from all platforms via Late API
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from uuid import UUID

from loguru import logger
from sqlalchemy import text

from ..common.db import get_db
from ..common.config import config
from ..posting.late_client import LateAPIClient


class AnalyticsAggregator:
    """
    Aggregate platform analytics for performance tracking

    Phase 5: Data-driven optimization
    - Pull analytics from Late API for all posts
    - Calculate engagement metrics (CTR, watch time, etc.)
    - Track performance trends by platform, format, topic
    - Identify top performers for learning
    """

    def __init__(self):
        self.late_client = None
        self.poll_interval_hours = config.get('analytics.poll_interval_hours', 6)
        self.platforms = config.get('posting.platforms', {}).keys()

    async def initialize(self):
        """Initialize analytics aggregator"""
        self.late_client = LateAPIClient()
        await self.late_client.initialize()
        logger.info(f"Analytics aggregator initialized (poll interval: {self.poll_interval_hours}h)")

    async def cleanup(self):
        """Cleanup resources"""
        if self.late_client:
            await self.late_client.cleanup()
        logger.info("Analytics aggregator cleaned up")

    async def aggregate_all_posts(self, days_back: int = 7):
        """
        Aggregate analytics for all recent posts

        Args:
            days_back: How many days back to fetch analytics
        """
        logger.info(f"Aggregating analytics for posts from last {days_back} days")

        # Get all posts from last N days
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT id, late_post_id, platform, extraction_id, stream_id, published_at
                    FROM platform_posts
                    WHERE status = 'published'
                        AND published_at >= NOW() - INTERVAL ':days days'
                    ORDER BY published_at DESC
                """),
                {'days': days_back}
            )

            posts = result.fetchall()

        logger.info(f"Found {len(posts)} published posts to analyze")

        # Fetch analytics for each post
        for post in posts:
            try:
                await self._update_post_analytics(
                    post_id=post.id,
                    late_post_id=post.late_post_id,
                    platform=post.platform,
                )
                await asyncio.sleep(1)  # Rate limiting

            except Exception as e:
                logger.error(f"Error fetching analytics for post {post.id}: {e}")

        logger.info("Analytics aggregation complete")

    async def _update_post_analytics(
        self,
        post_id: UUID,
        late_post_id: str,
        platform: str,
    ):
        """
        Update analytics for a single post

        Args:
            post_id: Platform post ID (our database)
            late_post_id: Late API post ID
            platform: Platform name
        """
        # Fetch analytics from Late API
        analytics = await self.late_client.get_analytics(late_post_id)

        if not analytics:
            logger.debug(f"No analytics available for post {post_id}")
            return

        # Extract platform-specific metrics
        views = analytics.get('views', 0)
        likes = analytics.get('likes', 0)
        comments = analytics.get('comments', 0)
        shares = analytics.get('shares', 0)
        saves = analytics.get('saves', 0)
        watch_time_seconds = analytics.get('watch_time_seconds', 0)
        impressions = analytics.get('impressions', 0)

        # Calculate engagement metrics
        engagement_total = likes + comments + shares + saves
        engagement_rate = (engagement_total / views * 100) if views > 0 else 0
        ctr_percent = (views / impressions * 100) if impressions > 0 else 0

        # Update database
        async with get_db() as db:
            await db.execute(
                text("""
                    UPDATE platform_posts
                    SET
                        views = :views,
                        likes = :likes,
                        comments = :comments,
                        shares = :shares,
                        saves = :saves,
                        watch_time_seconds = :watch_time_seconds,
                        impressions = :impressions,
                        engagement_rate = :engagement_rate,
                        ctr_percent = :ctr_percent,
                        last_analytics_update = NOW()
                    WHERE id = :post_id
                """),
                {
                    'post_id': post_id,
                    'views': views,
                    'likes': likes,
                    'comments': comments,
                    'shares': shares,
                    'saves': saves,
                    'watch_time_seconds': watch_time_seconds,
                    'impressions': impressions,
                    'engagement_rate': engagement_rate,
                    'ctr_percent': ctr_percent,
                }
            )
            await db.commit()

        logger.debug(
            f"Updated analytics for {platform} post: "
            f"{views} views, {engagement_rate:.2f}% engagement, {ctr_percent:.2f}% CTR"
        )

    async def get_performance_report(
        self,
        stream_id: Optional[UUID] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Generate performance report

        Args:
            stream_id: Optional stream ID to filter by
            days: Number of days to analyze

        Returns:
            Performance report dict
        """
        async with get_db() as db:
            # Overall metrics
            where_clause = "WHERE published_at >= NOW() - INTERVAL ':days days'"
            params = {'days': days}

            if stream_id:
                where_clause += " AND stream_id = :stream_id"
                params['stream_id'] = stream_id

            result = await db.execute(
                text(f"""
                    SELECT
                        COUNT(*) as total_posts,
                        SUM(views) as total_views,
                        SUM(likes) as total_likes,
                        SUM(comments) as total_comments,
                        SUM(shares) as total_shares,
                        AVG(engagement_rate) as avg_engagement_rate,
                        AVG(ctr_percent) as avg_ctr
                    FROM platform_posts
                    {where_clause}
                """),
                params
            )

            overall = result.fetchone()

            # Per-platform breakdown
            result = await db.execute(
                text(f"""
                    SELECT
                        platform,
                        COUNT(*) as post_count,
                        SUM(views) as total_views,
                        AVG(engagement_rate) as avg_engagement,
                        AVG(ctr_percent) as avg_ctr
                    FROM platform_posts
                    {where_clause}
                    GROUP BY platform
                    ORDER BY total_views DESC
                """),
                params
            )

            platform_breakdown = [
                {
                    'platform': row.platform,
                    'post_count': row.post_count,
                    'total_views': row.total_views or 0,
                    'avg_engagement': float(row.avg_engagement) if row.avg_engagement else 0,
                    'avg_ctr': float(row.avg_ctr) if row.avg_ctr else 0,
                }
                for row in result
            ]

            # Top performing posts
            result = await db.execute(
                text(f"""
                    SELECT
                        pp.id,
                        pp.platform,
                        pp.title,
                        pp.views,
                        pp.engagement_rate,
                        e.type as extraction_type,
                        t.category as topic_category
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    JOIN topics t ON e.topic_id = t.id
                    {where_clause}
                    ORDER BY pp.views DESC
                    LIMIT 10
                """),
                params
            )

            top_posts = [
                {
                    'id': str(row.id),
                    'platform': row.platform,
                    'title': row.title,
                    'views': row.views or 0,
                    'engagement_rate': float(row.engagement_rate) if row.engagement_rate else 0,
                    'extraction_type': row.extraction_type,
                    'topic_category': row.topic_category,
                }
                for row in result
            ]

        return {
            'period_days': days,
            'stream_id': str(stream_id) if stream_id else None,
            'overall': {
                'total_posts': overall.total_posts or 0,
                'total_views': overall.total_views or 0,
                'total_likes': overall.total_likes or 0,
                'total_comments': overall.total_comments or 0,
                'total_shares': overall.total_shares or 0,
                'avg_engagement_rate': float(overall.avg_engagement_rate) if overall.avg_engagement_rate else 0,
                'avg_ctr': float(overall.avg_ctr) if overall.avg_ctr else 0,
            },
            'platform_breakdown': platform_breakdown,
            'top_posts': top_posts,
        }

    async def identify_success_patterns(self) -> Dict[str, Any]:
        """
        Identify patterns in successful content

        Returns:
            Success patterns analysis
        """
        async with get_db() as db:
            # Find top performing extraction types
            result = await db.execute(
                text("""
                    SELECT
                        e.type as extraction_type,
                        COUNT(*) as post_count,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                    GROUP BY e.type
                    ORDER BY avg_views DESC
                """)
            )

            extraction_performance = [
                {
                    'extraction_type': row.extraction_type,
                    'post_count': row.post_count,
                    'avg_views': float(row.avg_views) if row.avg_views else 0,
                    'avg_engagement': float(row.avg_engagement) if row.avg_engagement else 0,
                }
                for row in result
            ]

            # Find top performing topic categories
            result = await db.execute(
                text("""
                    SELECT
                        t.category,
                        COUNT(*) as post_count,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    JOIN topics t ON e.topic_id = t.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                    GROUP BY t.category
                    ORDER BY avg_views DESC
                """)
            )

            category_performance = [
                {
                    'category': row.category,
                    'post_count': row.post_count,
                    'avg_views': float(row.avg_views) if row.avg_views else 0,
                    'avg_engagement': float(row.avg_engagement) if row.avg_engagement else 0,
                }
                for row in result
            ]

            # Best platforms for each extraction type
            result = await db.execute(
                text("""
                    SELECT
                        e.type as extraction_type,
                        pp.platform,
                        AVG(pp.views) as avg_views
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE pp.published_at >= NOW() - INTERVAL '30 days'
                    GROUP BY e.type, pp.platform
                    ORDER BY e.type, avg_views DESC
                """)
            )

            platform_fit = {}
            for row in result:
                if row.extraction_type not in platform_fit:
                    platform_fit[row.extraction_type] = []
                platform_fit[row.extraction_type].append({
                    'platform': row.platform,
                    'avg_views': float(row.avg_views) if row.avg_views else 0,
                })

        return {
            'extraction_performance': extraction_performance,
            'category_performance': category_performance,
            'platform_fit': platform_fit,
        }


logger.info("Analytics aggregator module loaded")
