"""
Cost Tracker
Tracks and reports costs for LLM usage, B-roll generation, and compute resources
"""
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from uuid import UUID
import json

from loguru import logger
from sqlalchemy import text

from .db import get_db
from .config import config


class CostTracker:
    """
    Track costs for pipeline operations

    Phase 4: Real-time cost monitoring
    - LLM API costs (Claude, GPT-4, Grok, Perplexity)
    - B-roll generation costs (ComfyUI/SDXL)
    - Compute resource usage
    - Cost optimization recommendations
    """

    # LLM pricing per 1M tokens (input/output)
    LLM_PRICING = {
        'claude-3-5-sonnet-20241022': {
            'input': 3.00,   # $3 per 1M input tokens
            'output': 15.00,  # $15 per 1M output tokens
        },
        'gpt-4o': {
            'input': 2.50,
            'output': 10.00,
        },
        'grok-beta': {
            'input': 5.00,
            'output': 10.00,
        },
        'perplexity-sonar': {
            'input': 1.00,
            'output': 1.00,
        },
        'llama-3-70b': {
            'input': 0.00,  # Free (local)
            'output': 0.00,
        },
    }

    # ComfyUI generation costs (estimated per image)
    COMFYUI_COST_PER_IMAGE = 0.02  # $0.02 per SDXL generation (cloud GPU)
    # If running locally, this is $0.00

    def __init__(self):
        self.session_costs = {}
        self.use_local_comfyui = config.get('broll.comfyui.local', False)

    async def initialize(self):
        """Initialize cost tracker"""
        logger.info("Cost tracker initialized")

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Cost tracker cleaned up")

    async def track_llm_call(
        self,
        stream_id: UUID,
        model: str,
        input_tokens: int,
        output_tokens: int,
        purpose: str,  # 'transcription', 'topic_segmentation', 'scoring', etc.
    ):
        """
        Track LLM API call cost

        Args:
            stream_id: Stream ID
            model: Model name
            input_tokens: Input token count
            output_tokens: Output token count
            purpose: Purpose of the call
        """
        # Calculate cost
        pricing = self.LLM_PRICING.get(model, {'input': 0, 'output': 0})
        input_cost = (input_tokens / 1_000_000) * pricing['input']
        output_cost = (output_tokens / 1_000_000) * pricing['output']
        total_cost = input_cost + output_cost

        # Store in database
        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO cost_tracking (
                        stream_id, operation_type, model, input_tokens, output_tokens,
                        cost_usd, metadata, created_at
                    )
                    VALUES (
                        :stream_id, :operation_type, :model, :input_tokens, :output_tokens,
                        :cost_usd, :metadata, NOW()
                    )
                """),
                {
                    'stream_id': stream_id,
                    'operation_type': 'llm_call',
                    'model': model,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'cost_usd': total_cost,
                    'metadata': json.dumps({'purpose': purpose}),
                }
            )
            await db.commit()

        logger.debug(
            f"LLM cost tracked: {model} - ${total_cost:.4f} "
            f"({input_tokens} in, {output_tokens} out) - {purpose}"
        )

    async def track_broll_generation(
        self,
        stream_id: UUID,
        generated_count: int,
        reused_count: int,
    ):
        """
        Track B-roll generation costs

        Args:
            stream_id: Stream ID
            generated_count: Number of newly generated panels
            reused_count: Number of reused panels from library
        """
        # Calculate cost (only for generated, not reused)
        cost_per_image = 0.00 if self.use_local_comfyui else self.COMFYUI_COST_PER_IMAGE
        total_cost = generated_count * cost_per_image
        savings = reused_count * cost_per_image

        # Store in database
        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO cost_tracking (
                        stream_id, operation_type, model, cost_usd, metadata, created_at
                    )
                    VALUES (
                        :stream_id, :operation_type, :model, :cost_usd, :metadata, NOW()
                    )
                """),
                {
                    'stream_id': stream_id,
                    'operation_type': 'broll_generation',
                    'model': 'comfyui-sdxl',
                    'cost_usd': total_cost,
                    'metadata': json.dumps({
                        'generated_count': generated_count,
                        'reused_count': reused_count,
                        'savings_usd': savings,
                        'reuse_rate': (reused_count / (generated_count + reused_count) * 100)
                        if (generated_count + reused_count) > 0 else 0,
                    }),
                }
            )
            await db.commit()

        logger.info(
            f"B-roll cost tracked: ${total_cost:.2f} "
            f"({generated_count} generated, {reused_count} reused, ${savings:.2f} saved)"
        )

    async def get_stream_costs(self, stream_id: UUID) -> Dict[str, Any]:
        """
        Get cost breakdown for a stream

        Args:
            stream_id: Stream ID

        Returns:
            Cost breakdown dict
        """
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT
                        operation_type,
                        COUNT(*) as operation_count,
                        SUM(cost_usd) as total_cost,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        jsonb_agg(metadata) as metadata_list
                    FROM cost_tracking
                    WHERE stream_id = :stream_id
                    GROUP BY operation_type
                """),
                {'stream_id': stream_id}
            )

            breakdown = {}
            total_cost = 0.0

            for row in result:
                breakdown[row.operation_type] = {
                    'operation_count': row.operation_count,
                    'total_cost': float(row.total_cost),
                    'total_input_tokens': row.total_input_tokens or 0,
                    'total_output_tokens': row.total_output_tokens or 0,
                }
                total_cost += float(row.total_cost)

        return {
            'stream_id': str(stream_id),
            'total_cost_usd': total_cost,
            'breakdown': breakdown,
        }

    async def get_daily_costs(self, days: int = 7) -> Dict[str, Any]:
        """
        Get daily cost statistics

        Args:
            days: Number of days to look back

        Returns:
            Daily cost statistics
        """
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT
                        DATE(created_at) as date,
                        operation_type,
                        COUNT(DISTINCT stream_id) as stream_count,
                        SUM(cost_usd) as daily_cost
                    FROM cost_tracking
                    WHERE created_at >= NOW() - INTERVAL ':days days'
                    GROUP BY DATE(created_at), operation_type
                    ORDER BY date DESC
                """),
                {'days': days}
            )

            daily_stats = {}
            for row in result:
                date_str = str(row.date)
                if date_str not in daily_stats:
                    daily_stats[date_str] = {
                        'date': date_str,
                        'total_cost': 0.0,
                        'stream_count': row.stream_count,
                        'operations': {},
                    }

                daily_stats[date_str]['operations'][row.operation_type] = float(row.daily_cost)
                daily_stats[date_str]['total_cost'] += float(row.daily_cost)

        return {
            'period_days': days,
            'daily_stats': list(daily_stats.values()),
        }

    async def get_cost_optimization_report(self) -> Dict[str, Any]:
        """
        Generate cost optimization recommendations

        Returns:
            Optimization report with recommendations
        """
        # Get recent costs
        async with get_db() as db:
            # B-roll reuse rate
            broll_result = await db.execute(
                text("""
                    SELECT
                        SUM((metadata->>'generated_count')::int) as total_generated,
                        SUM((metadata->>'reused_count')::int) as total_reused,
                        AVG((metadata->>'reuse_rate')::float) as avg_reuse_rate
                    FROM cost_tracking
                    WHERE operation_type = 'broll_generation'
                        AND created_at >= NOW() - INTERVAL '30 days'
                """)
            )
            broll_row = broll_result.fetchone()

            # LLM usage by model
            llm_result = await db.execute(
                text("""
                    SELECT
                        model,
                        COUNT(*) as call_count,
                        SUM(cost_usd) as total_cost,
                        AVG(cost_usd) as avg_cost_per_call
                    FROM cost_tracking
                    WHERE operation_type = 'llm_call'
                        AND created_at >= NOW() - INTERVAL '30 days'
                    GROUP BY model
                    ORDER BY total_cost DESC
                """)
            )

            llm_stats = {}
            for row in llm_result:
                llm_stats[row.model] = {
                    'call_count': row.call_count,
                    'total_cost': float(row.total_cost),
                    'avg_cost_per_call': float(row.avg_cost_per_call),
                }

        recommendations = []

        # B-roll recommendations
        if broll_row and broll_row.avg_reuse_rate:
            if broll_row.avg_reuse_rate < 30:
                recommendations.append({
                    'category': 'broll',
                    'severity': 'medium',
                    'message': f"B-roll reuse rate is low ({broll_row.avg_reuse_rate:.1f}%). "
                               f"Consider lowering similarity threshold or building larger library.",
                    'potential_savings_usd': broll_row.total_generated * self.COMFYUI_COST_PER_IMAGE * 0.3,
                })

        # LLM recommendations
        if 'gpt-4o' in llm_stats:
            gpt4_cost = llm_stats['gpt-4o']['total_cost']
            if gpt4_cost > 50:  # If spending > $50/month on GPT-4
                recommendations.append({
                    'category': 'llm',
                    'severity': 'high',
                    'message': f"High GPT-4o usage (${gpt4_cost:.2f}/month). "
                               f"Consider using Claude or Grok for non-critical tasks.",
                    'potential_savings_usd': gpt4_cost * 0.4,  # 40% potential savings
                })

        return {
            'period': '30 days',
            'broll_stats': {
                'total_generated': broll_row.total_generated if broll_row else 0,
                'total_reused': broll_row.total_reused if broll_row else 0,
                'avg_reuse_rate': broll_row.avg_reuse_rate if broll_row else 0,
            },
            'llm_stats': llm_stats,
            'recommendations': recommendations,
            'estimated_monthly_savings': sum(r['potential_savings_usd'] for r in recommendations),
        }


# Global instance
cost_tracker = CostTracker()


async def init_cost_tracker():
    """Initialize global cost tracker"""
    await cost_tracker.initialize()


async def close_cost_tracker():
    """Cleanup global cost tracker"""
    await cost_tracker.cleanup()


logger.info("Cost tracker module loaded")
