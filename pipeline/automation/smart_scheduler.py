"""
Smart Scheduler
Learns optimal posting times based on historical performance data
"""
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, time
from uuid import UUID
import json

from loguru import logger
from sqlalchemy import text
import numpy as np

from ..common.db import get_db
from ..common.config import config


class SmartScheduler:
    """
    Intelligent posting time optimization

    Phase 6: Advanced automation
    - Learn optimal posting times per platform
    - Analyze audience activity patterns
    - Account for day-of-week variations
    - Timezone-aware scheduling
    - Avoid content cannibalization
    """

    def __init__(self):
        self.enabled = config.get('smart_scheduler.enabled', True)
        self.min_posts_for_learning = config.get('smart_scheduler.min_posts', 30)
        self.cannibalization_window_hours = config.get('smart_scheduler.cannibalization_window_hours', 4)

    async def initialize(self):
        """Initialize smart scheduler"""
        if self.enabled:
            logger.info(
                f"Smart scheduler initialized "
                f"(min_posts: {self.min_posts_for_learning}, "
                f"cannibalization_window: {self.cannibalization_window_hours}h)"
            )
        else:
            logger.info("Smart scheduler disabled")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Smart scheduler cleaned up")

    async def get_optimal_posting_time(
        self,
        platform: str,
        extraction_type: str,
        target_date: Optional[datetime] = None,
    ) -> datetime:
        """
        Calculate optimal posting time for platform and extraction type

        Args:
            platform: Platform name
            extraction_type: Extraction type (long, short, micro, medium)
            target_date: Target date (defaults to tomorrow)

        Returns:
            Optimal posting datetime
        """
        if not self.enabled:
            # Fallback to configured default time
            return self._get_default_posting_time(platform, target_date)

        # Default to tomorrow if not specified
        if target_date is None:
            target_date = datetime.utcnow() + timedelta(days=1)

        # Learn from historical performance
        optimal_hour = await self._learn_optimal_hour(
            platform=platform,
            extraction_type=extraction_type,
            day_of_week=target_date.weekday(),
        )

        if optimal_hour is None:
            # Not enough data, use default
            return self._get_default_posting_time(platform, target_date)

        # Create datetime with optimal hour
        posting_time = target_date.replace(
            hour=optimal_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

        # Check for cannibalization
        posting_time = await self._avoid_cannibalization(
            posting_time=posting_time,
            platform=platform,
        )

        logger.info(
            f"Optimal posting time for {platform} ({extraction_type}): "
            f"{posting_time.strftime('%Y-%m-%d %H:%M UTC')}"
        )

        return posting_time

    async def _learn_optimal_hour(
        self,
        platform: str,
        extraction_type: str,
        day_of_week: int,
    ) -> Optional[int]:
        """
        Learn optimal hour from historical performance

        Args:
            platform: Platform name
            extraction_type: Extraction type
            day_of_week: Day of week (0=Monday, 6=Sunday)

        Returns:
            Optimal hour (0-23) or None if insufficient data
        """
        async with get_db() as db:
            # Get performance by hour of day for this platform/extraction/day
            result = await db.execute(
                text("""
                    SELECT
                        EXTRACT(HOUR FROM pp.published_at) as hour,
                        COUNT(*) as post_count,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement,
                        STDDEV(pp.views) as stddev_views
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE
                        pp.platform = :platform
                        AND e.type = :extraction_type
                        AND EXTRACT(DOW FROM pp.published_at) = :day_of_week
                        AND pp.published_at >= NOW() - INTERVAL '60 days'
                        AND pp.status = 'published'
                    GROUP BY EXTRACT(HOUR FROM pp.published_at)
                    HAVING COUNT(*) >= 3
                    ORDER BY avg_views DESC
                """),
                {
                    'platform': platform,
                    'extraction_type': extraction_type,
                    'day_of_week': day_of_week,
                }
            )

            hour_performance = list(result)

        if not hour_performance:
            # Try without day-of-week filter
            async with get_db() as db:
                result = await db.execute(
                    text("""
                        SELECT
                            EXTRACT(HOUR FROM pp.published_at) as hour,
                            COUNT(*) as post_count,
                            AVG(pp.views) as avg_views
                        FROM platform_posts pp
                        JOIN extractions e ON pp.extraction_id = e.id
                        WHERE
                            pp.platform = :platform
                            AND e.type = :extraction_type
                            AND pp.published_at >= NOW() - INTERVAL '90 days'
                            AND pp.status = 'published'
                        GROUP BY EXTRACT(HOUR FROM pp.published_at)
                        HAVING COUNT(*) >= 5
                        ORDER BY avg_views DESC
                    """),
                    {
                        'platform': platform,
                        'extraction_type': extraction_type,
                    }
                )

                hour_performance = list(result)

        if not hour_performance:
            return None

        # Get top performing hour
        best_hour = int(hour_performance[0].hour)

        logger.debug(
            f"Learned optimal hour for {platform} ({extraction_type}): "
            f"{best_hour}:00 (avg views: {hour_performance[0].avg_views:.0f})"
        )

        return best_hour

    async def _avoid_cannibalization(
        self,
        posting_time: datetime,
        platform: str,
    ) -> datetime:
        """
        Avoid cannibalization by spacing posts on same platform

        Args:
            posting_time: Proposed posting time
            platform: Platform name

        Returns:
            Adjusted posting time
        """
        async with get_db() as db:
            # Check for posts within cannibalization window
            result = await db.execute(
                text("""
                    SELECT scheduled_time
                    FROM platform_posts
                    WHERE
                        platform = :platform
                        AND status IN ('scheduled', 'published')
                        AND scheduled_time BETWEEN :window_start AND :window_end
                    ORDER BY scheduled_time
                """),
                {
                    'platform': platform,
                    'window_start': posting_time - timedelta(hours=self.cannibalization_window_hours),
                    'window_end': posting_time + timedelta(hours=self.cannibalization_window_hours),
                }
            )

            nearby_posts = list(result)

        if not nearby_posts:
            return posting_time

        # Find gap in schedule
        gaps = []
        prev_time = posting_time - timedelta(hours=self.cannibalization_window_hours)

        for post in nearby_posts:
            gap_duration = (post.scheduled_time - prev_time).total_seconds() / 3600
            if gap_duration >= self.cannibalization_window_hours:
                gaps.append({
                    'start': prev_time,
                    'end': post.scheduled_time,
                    'duration': gap_duration,
                })
            prev_time = post.scheduled_time

        # Check gap after last post
        last_gap_duration = (
            posting_time + timedelta(hours=self.cannibalization_window_hours) - prev_time
        ).total_seconds() / 3600

        if last_gap_duration >= self.cannibalization_window_hours:
            gaps.append({
                'start': prev_time,
                'end': posting_time + timedelta(hours=self.cannibalization_window_hours),
                'duration': last_gap_duration,
            })

        if not gaps:
            # No gaps, schedule after last post
            adjusted_time = prev_time + timedelta(hours=self.cannibalization_window_hours)
            logger.info(
                f"Avoiding cannibalization: moved from {posting_time.strftime('%H:%M')} "
                f"to {adjusted_time.strftime('%H:%M')}"
            )
            return adjusted_time

        # Pick largest gap, schedule in middle
        largest_gap = max(gaps, key=lambda g: g['duration'])
        adjusted_time = largest_gap['start'] + (
            largest_gap['end'] - largest_gap['start']
        ) / 2

        if adjusted_time != posting_time:
            logger.info(
                f"Avoiding cannibalization: moved from {posting_time.strftime('%H:%M')} "
                f"to {adjusted_time.strftime('%H:%M')}"
            )

        return adjusted_time

    def _get_default_posting_time(
        self,
        platform: str,
        target_date: datetime,
    ) -> datetime:
        """Get default posting time from config"""
        # Get platform-specific optimal hours from config
        platform_config = config.get(f'posting.platforms.{platform}', {})
        optimal_hours = platform_config.get('optimal_hours', [12, 15, 18])

        # Pick middle of optimal hours
        default_hour = optimal_hours[len(optimal_hours) // 2]

        return target_date.replace(
            hour=default_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

    async def get_posting_strategy(
        self,
        stream_id: UUID,
        extractions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Create complete posting strategy for all extractions

        Args:
            stream_id: Stream ID
            extractions: List of extraction dicts with type, id, etc.

        Returns:
            Posting strategy with schedule for each extraction
        """
        strategy = {
            'stream_id': str(stream_id),
            'created_at': datetime.utcnow().isoformat(),
            'schedules': [],
        }

        # Sort extractions by priority (shorts first, then micros, mediums, longs)
        priority_order = {'short': 0, 'micro': 1, 'medium': 2, 'long': 3}
        sorted_extractions = sorted(
            extractions,
            key=lambda e: priority_order.get(e.get('type', 'medium'), 2)
        )

        # Get all target platforms
        platforms = config.get('posting.enabled_platforms', [
            'youtube', 'tiktok', 'instagram', 'facebook', 'x',
            'linkedin', 'pinterest', 'snapchat', 'twitch', 'reddit'
        ])

        # Schedule each extraction
        current_time = datetime.utcnow()

        for extraction in sorted_extractions:
            extraction_type = extraction.get('type', 'medium')
            extraction_id = extraction.get('id')

            # Determine posting delay based on type
            if extraction_type in ['short', 'micro']:
                delay_hours = 0  # Post ASAP
            elif extraction_type == 'medium':
                delay_hours = 6
            else:  # long
                delay_hours = 24

            base_time = current_time + timedelta(hours=delay_hours)

            # Schedule on each platform
            platform_schedules = {}

            for platform in platforms:
                optimal_time = await self.get_optimal_posting_time(
                    platform=platform,
                    extraction_type=extraction_type,
                    target_date=base_time,
                )

                platform_schedules[platform] = {
                    'scheduled_time': optimal_time.isoformat(),
                    'delay_hours': delay_hours,
                }

            strategy['schedules'].append({
                'extraction_id': str(extraction_id),
                'extraction_type': extraction_type,
                'platforms': platform_schedules,
            })

        logger.info(
            f"Created posting strategy for {len(extractions)} extractions "
            f"across {len(platforms)} platforms"
        )

        return strategy

    async def get_audience_activity_pattern(
        self,
        platform: str,
        days_back: int = 60,
    ) -> Dict[str, Any]:
        """
        Analyze audience activity patterns

        Args:
            platform: Platform name
            days_back: Days of historical data to analyze

        Returns:
            Activity pattern analysis
        """
        async with get_db() as db:
            # Get hourly activity distribution
            result = await db.execute(
                text("""
                    SELECT
                        EXTRACT(HOUR FROM pp.published_at) as hour,
                        EXTRACT(DOW FROM pp.published_at) as day_of_week,
                        AVG(pp.views) as avg_views,
                        AVG(pp.engagement_rate) as avg_engagement
                    FROM platform_posts pp
                    WHERE
                        pp.platform = :platform
                        AND pp.published_at >= NOW() - make_interval(days => :days)
                        AND pp.status = 'published'
                    GROUP BY
                        EXTRACT(HOUR FROM pp.published_at),
                        EXTRACT(DOW FROM pp.published_at)
                    ORDER BY
                        day_of_week,
                        hour
                """),
                {
                    'platform': platform,
                    'days': days_back,
                }
            )

            activity_data = list(result)

        if not activity_data:
            return {
                'platform': platform,
                'status': 'insufficient_data',
            }

        # Build heatmap data
        heatmap = {}
        for row in activity_data:
            day = int(row.day_of_week)
            hour = int(row.hour)

            if day not in heatmap:
                heatmap[day] = {}

            heatmap[day][hour] = {
                'avg_views': float(row.avg_views) if row.avg_views else 0,
                'avg_engagement': float(row.avg_engagement) if row.avg_engagement else 0,
            }

        # Find peak hours for each day
        peak_hours = {}
        for day in range(7):
            if day in heatmap:
                day_data = heatmap[day]
                if day_data:
                    peak_hour = max(day_data.items(), key=lambda x: x[1]['avg_views'])
                    peak_hours[day] = {
                        'hour': peak_hour[0],
                        'avg_views': peak_hour[1]['avg_views'],
                    }

        return {
            'platform': platform,
            'days_analyzed': days_back,
            'heatmap': heatmap,
            'peak_hours_by_day': peak_hours,
        }


logger.info("Smart scheduler module loaded")
