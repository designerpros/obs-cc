"""
Predictive Analytics
Forecasts performance and audience behavior using historical data
"""
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from uuid import UUID
import json

from loguru import logger
from sqlalchemy import text
import numpy as np

from ..common.db import get_db
from ..common.config import config


class PredictiveAnalytics:
    """
    Performance forecasting and trend prediction

    Phase 6: Advanced automation
    - Predict expected views for new content
    - Forecast optimal posting windows
    - Detect performance trends
    - Anomaly detection
    - Seasonality analysis
    """

    def __init__(self):
        self.enabled = config.get('predictive_analytics.enabled', True)
        self.lookback_days = config.get('predictive_analytics.lookback_days', 90)

    async def initialize(self):
        """Initialize predictive analytics"""
        if self.enabled:
            logger.info(f"Predictive analytics initialized (lookback: {self.lookback_days} days)")
        else:
            logger.info("Predictive analytics disabled")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Predictive analytics cleaned up")

    async def predict_views(
        self,
        platform: str,
        extraction_type: str,
        topic_category: str,
        virality_score: float,
        posting_hour: int,
    ) -> Dict[str, Any]:
        """
        Predict expected views for new content

        Args:
            platform: Platform name
            extraction_type: Extraction type
            topic_category: Topic category
            virality_score: Virality score (0-100)
            posting_hour: Hour of day to post (0-23)

        Returns:
            Prediction with confidence intervals
        """
        if not self.enabled:
            return {'enabled': False}

        async with get_db() as db:
            # Get similar historical posts
            result = await db.execute(
                text("""
                    SELECT
                        pp.views,
                        e.virality_score,
                        EXTRACT(HOUR FROM pp.published_at) as pub_hour
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    JOIN topics t ON e.topic_id = t.id
                    WHERE
                        pp.platform = :platform
                        AND e.type = :extraction_type
                        AND t.category = :topic_category
                        AND pp.published_at >= NOW() - INTERVAL ':days days'
                        AND pp.status = 'published'
                        AND pp.views > 0
                """),
                {
                    'platform': platform,
                    'extraction_type': extraction_type,
                    'topic_category': topic_category,
                    'days': self.lookback_days,
                }
            )

            historical_data = list(result)

        if len(historical_data) < 10:
            return {
                'status': 'insufficient_data',
                'message': f'Need at least 10 similar posts (found {len(historical_data)})',
            }

        # Simple linear regression: views ~ virality_score + posting_hour
        # In production, use proper ML library (scikit-learn)

        views = np.array([d.views for d in historical_data])
        virality_scores = np.array([d.virality_score or 50 for d in historical_data])
        posting_hours = np.array([d.pub_hour for d in historical_data])

        # Normalize features
        virality_normalized = (virality_score - np.mean(virality_scores)) / (np.std(virality_scores) + 1e-8)
        hour_normalized = (posting_hour - np.mean(posting_hours)) / (np.std(posting_hours) + 1e-8)

        # Calculate correlation-based prediction
        virality_correlation = np.corrcoef(virality_scores, views)[0, 1]
        hour_correlation = np.corrcoef(posting_hours, views)[0, 1]

        # Weighted prediction
        base_views = np.median(views)
        virality_adjustment = virality_normalized * virality_correlation * np.std(views)
        hour_adjustment = hour_normalized * hour_correlation * np.std(views) * 0.5

        predicted_views = base_views + virality_adjustment + hour_adjustment

        # Confidence intervals (simple percentile-based)
        lower_bound = np.percentile(views, 25)
        upper_bound = np.percentile(views, 75)

        return {
            'predicted_views': int(max(0, predicted_views)),
            'confidence_interval': {
                'lower': int(lower_bound),
                'upper': int(upper_bound),
            },
            'baseline_median': int(base_views),
            'sample_size': len(historical_data),
            'virality_impact': f"{'+' if virality_adjustment > 0 else ''}{int(virality_adjustment)} views",
            'timing_impact': f"{'+' if hour_adjustment > 0 else ''}{int(hour_adjustment)} views",
        }

    async def detect_trend(
        self,
        platform: str,
        metric: str = 'views',
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Detect performance trends

        Args:
            platform: Platform name
            metric: Metric to analyze (views, engagement_rate)
            days: Number of days to analyze

        Returns:
            Trend analysis
        """
        async with get_db() as db:
            result = await db.execute(
                text(f"""
                    SELECT
                        DATE(pp.published_at) as date,
                        AVG(pp.{metric}) as avg_metric,
                        COUNT(*) as post_count
                    FROM platform_posts pp
                    WHERE
                        pp.platform = :platform
                        AND pp.published_at >= NOW() - INTERVAL ':days days'
                        AND pp.status = 'published'
                    GROUP BY DATE(pp.published_at)
                    ORDER BY date
                """),
                {
                    'platform': platform,
                    'days': days,
                }
            )

            daily_data = list(result)

        if len(daily_data) < 7:
            return {
                'status': 'insufficient_data',
                'message': f'Need at least 7 days of data (found {len(daily_data)})',
            }

        # Calculate trend (simple linear regression)
        dates = np.arange(len(daily_data))
        metrics = np.array([d.avg_metric for d in daily_data if d.avg_metric])

        if len(metrics) < 7:
            return {'status': 'insufficient_data'}

        # Linear fit
        coefficients = np.polyfit(dates[:len(metrics)], metrics, 1)
        slope = coefficients[0]

        # Trend direction
        avg_metric = np.mean(metrics)
        percent_change_per_day = (slope / avg_metric * 100) if avg_metric > 0 else 0

        if percent_change_per_day > 2:
            trend_direction = 'strongly_increasing'
        elif percent_change_per_day > 0.5:
            trend_direction = 'increasing'
        elif percent_change_per_day < -2:
            trend_direction = 'strongly_decreasing'
        elif percent_change_per_day < -0.5:
            trend_direction = 'decreasing'
        else:
            trend_direction = 'stable'

        return {
            'platform': platform,
            'metric': metric,
            'trend_direction': trend_direction,
            'percent_change_per_day': percent_change_per_day,
            'current_average': float(metrics[-7:].mean()),  # Last week average
            'previous_average': float(metrics[:7].mean()) if len(metrics) >= 14 else None,
            'data_points': len(daily_data),
        }

    async def detect_anomalies(
        self,
        platform: str,
        days: int = 14,
        threshold_std: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Detect performance anomalies

        Args:
            platform: Platform name
            days: Days to analyze
            threshold_std: Standard deviation threshold for anomalies

        Returns:
            Anomaly detection results
        """
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT
                        pp.id,
                        pp.title,
                        pp.views,
                        pp.engagement_rate,
                        pp.published_at,
                        e.type as extraction_type
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    WHERE
                        pp.platform = :platform
                        AND pp.published_at >= NOW() - INTERVAL ':days days'
                        AND pp.status = 'published'
                    ORDER BY pp.published_at DESC
                """),
                {
                    'platform': platform,
                    'days': days,
                }
            )

            posts = list(result)

        if len(posts) < 10:
            return {
                'status': 'insufficient_data',
                'message': f'Need at least 10 posts (found {len(posts)})',
            }

        # Calculate statistics
        views = np.array([p.views or 0 for p in posts])
        mean_views = np.mean(views)
        std_views = np.std(views)

        # Find anomalies
        anomalies = []
        for post in posts:
            z_score = (post.views - mean_views) / (std_views + 1e-8)

            if abs(z_score) > threshold_std:
                anomalies.append({
                    'post_id': str(post.id),
                    'title': post.title,
                    'views': post.views or 0,
                    'expected_views': int(mean_views),
                    'deviation': f"{z_score:.2f} std devs",
                    'type': 'high_performer' if z_score > 0 else 'low_performer',
                    'published_at': post.published_at.isoformat(),
                })

        return {
            'platform': platform,
            'analyzed_posts': len(posts),
            'anomalies_found': len(anomalies),
            'mean_views': int(mean_views),
            'std_deviation': int(std_views),
            'anomalies': sorted(anomalies, key=lambda x: abs(float(x['deviation'].split()[0])), reverse=True)[:10],
        }

    async def analyze_seasonality(
        self,
        platform: str,
        topic_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Analyze day-of-week and hour-of-day seasonality

        Args:
            platform: Platform name
            topic_category: Optional topic category filter

        Returns:
            Seasonality analysis
        """
        async with get_db() as db:
            where_clause = "pp.platform = :platform AND pp.status = 'published'"
            params = {'platform': platform}

            if topic_category:
                where_clause += " AND t.category = :category"
                params['category'] = topic_category

            result = await db.execute(
                text(f"""
                    SELECT
                        EXTRACT(DOW FROM pp.published_at) as day_of_week,
                        EXTRACT(HOUR FROM pp.published_at) as hour,
                        AVG(pp.views) as avg_views,
                        COUNT(*) as post_count
                    FROM platform_posts pp
                    JOIN extractions e ON pp.extraction_id = e.id
                    JOIN topics t ON e.topic_id = t.id
                    WHERE {where_clause}
                        AND pp.published_at >= NOW() - INTERVAL '90 days'
                    GROUP BY
                        EXTRACT(DOW FROM pp.published_at),
                        EXTRACT(HOUR FROM pp.published_at)
                """),
                params
            )

            seasonality_data = list(result)

        if not seasonality_data:
            return {'status': 'insufficient_data'}

        # Group by day of week
        by_day = {}
        for row in seasonality_data:
            day = int(row.day_of_week)
            if day not in by_day:
                by_day[day] = {'total_views': 0, 'post_count': 0, 'hours': []}

            by_day[day]['total_views'] += row.avg_views * row.post_count
            by_day[day]['post_count'] += row.post_count
            by_day[day]['hours'].append({
                'hour': int(row.hour),
                'avg_views': float(row.avg_views),
            })

        # Calculate average for each day
        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        day_performance = []

        for day in range(7):
            if day in by_day:
                avg = by_day[day]['total_views'] / by_day[day]['post_count']
                day_performance.append({
                    'day': day_names[day],
                    'avg_views': int(avg),
                    'post_count': by_day[day]['post_count'],
                })

        # Find best day
        best_day = max(day_performance, key=lambda d: d['avg_views']) if day_performance else None

        return {
            'platform': platform,
            'topic_category': topic_category,
            'day_of_week_performance': day_performance,
            'best_day': best_day,
        }


logger.info("Predictive analytics module loaded")
