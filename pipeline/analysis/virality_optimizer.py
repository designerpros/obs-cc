"""
Virality Optimizer
Analyzes content for virality potential and provides enhancement recommendations
"""
import asyncio
from typing import Dict, Any, List, Optional
import json

from loguru import logger
from anthropic import AsyncAnthropic

from ..common.config import config


class ViralityOptimizer:
    """
    Optimize content for maximum virality

    Phase 3: AI-powered virality analysis
    - Analyzes hooks, pacing, emotional arcs
    - Provides cut recommendations
    - Optimizes segment length and timing
    """

    def __init__(self):
        self.client = None
        self.enabled = config.get('analysis.virality_optimization.enabled', True)

        # Virality factors
        self.hook_weight = config.get('analysis.virality_optimization.hook_weight', 0.35)
        self.pacing_weight = config.get('analysis.virality_optimization.pacing_weight', 0.25)
        self.emotion_weight = config.get('analysis.virality_optimization.emotion_weight', 0.20)
        self.retention_weight = config.get('analysis.virality_optimization.retention_weight', 0.20)

    async def initialize(self):
        """Initialize virality optimizer"""
        if self.enabled:
            import os
            anthropic_key = os.getenv('ANTHROPIC_API_KEY')

            if anthropic_key:
                self.client = AsyncAnthropic(api_key=anthropic_key)
                logger.info("Virality optimizer initialized")
            else:
                logger.warning("Virality optimizer disabled (no Anthropic API key)")
                self.enabled = False
        else:
            logger.info("Virality optimizer disabled in config")

    async def cleanup(self):
        """Cleanup resources"""
        if self.client:
            await self.client.close()
        logger.info("Virality optimizer cleaned up")

    async def optimize_topic(
        self,
        topic: Dict[str, Any],
        transcript_segments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Analyze topic for virality and provide optimization

        Args:
            topic: Topic data with summary, category, etc.
            transcript_segments: Full transcript segments for context

        Returns:
            {
                'virality_score': float,  # 0-100
                'hook_score': float,
                'pacing_score': float,
                'emotion_score': float,
                'retention_score': float,
                'recommendations': List[str],
                'optimal_cuts': List[{start, end, reason}],
            }
        """
        if not self.enabled or not self.client:
            return {
                'virality_score': 50.0,
                'hook_score': 50.0,
                'pacing_score': 50.0,
                'emotion_score': 50.0,
                'retention_score': 50.0,
                'recommendations': [],
                'optimal_cuts': [],
            }

        logger.info(f"Analyzing virality for topic: {topic.get('summary', '')[:50]}...")

        # Get topic segments
        topic_start = topic['start_time']
        topic_end = topic['end_time']

        # Extract relevant transcript
        topic_transcript = [
            seg for seg in transcript_segments
            if seg['start'] >= topic_start and seg['end'] <= topic_end
        ]

        # Build analysis prompt
        transcript_text = "\n".join([
            f"[{seg['start']:.1f}s] {seg['text']}"
            for seg in topic_transcript[:50]  # Limit to first 50 segments
        ])

        prompt = f"""Analyze this content segment for virality potential.

TOPIC SUMMARY: {topic.get('summary', '')}
CATEGORY: {topic.get('category', 'general')}
DURATION: {topic_end - topic_start:.1f} seconds

TRANSCRIPT:
{transcript_text[:3000]}...

ANALYZE on these dimensions (score 0-100):

1. HOOK STRENGTH: How compelling is the opening? Will viewers stay?
2. PACING: Is the content well-paced? Any slow moments?
3. EMOTIONAL RESONANCE: Does it evoke emotion? Surprise, excitement, curiosity?
4. RETENTION POTENTIAL: Will viewers watch to the end?

PROVIDE:
- Scores for each dimension
- Overall virality score (weighted average)
- 3-5 specific recommendations for improvement
- Optimal cut points (if content should be trimmed)

Respond ONLY with JSON:
{{
  "hook_score": 75,
  "pacing_score": 80,
  "emotion_score": 70,
  "retention_score": 85,
  "virality_score": 78,
  "recommendations": [
    "Start 5 seconds later at the key question",
    "Cut 15s lull at 2:30-2:45",
    "End on the punchline at 4:20 instead of explanation"
  ],
  "optimal_cuts": [
    {{"start": 0, "end": 5, "reason": "Slow opening, cut to hook"}},
    {{"start": 150, "end": 165, "reason": "Repetitive content, kills pacing"}}
  ],
  "hook_analysis": "Strong opening question but delayed 5s",
  "pacing_analysis": "Good overall but 15s lull in middle",
  "emotion_analysis": "High curiosity factor, good controversy",
  "retention_analysis": "Strong middle, weak ending"
}}"""

        try:
            response = await self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1500,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )

            result = self._parse_analysis(response.content[0].text)

            logger.info(
                f"Virality analysis: {result['virality_score']:.1f}/100 "
                f"(hook: {result['hook_score']}, pacing: {result['pacing_score']}, "
                f"emotion: {result['emotion_score']}, retention: {result['retention_score']})"
            )

            return result

        except Exception as e:
            logger.exception(f"Virality analysis error: {e}")
            return {
                'virality_score': 50.0,
                'hook_score': 50.0,
                'pacing_score': 50.0,
                'emotion_score': 50.0,
                'retention_score': 50.0,
                'recommendations': [],
                'optimal_cuts': [],
            }

    def _parse_analysis(self, content: str) -> Dict[str, Any]:
        """Parse virality analysis from Claude response"""
        import re

        # Extract JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)

        if json_match:
            try:
                data = json.loads(json_match.group(0))

                # Ensure all required fields
                return {
                    'virality_score': float(data.get('virality_score', 50)),
                    'hook_score': float(data.get('hook_score', 50)),
                    'pacing_score': float(data.get('pacing_score', 50)),
                    'emotion_score': float(data.get('emotion_score', 50)),
                    'retention_score': float(data.get('retention_score', 50)),
                    'recommendations': data.get('recommendations', []),
                    'optimal_cuts': data.get('optimal_cuts', []),
                    'hook_analysis': data.get('hook_analysis', ''),
                    'pacing_analysis': data.get('pacing_analysis', ''),
                    'emotion_analysis': data.get('emotion_analysis', ''),
                    'retention_analysis': data.get('retention_analysis', ''),
                }
            except json.JSONDecodeError:
                pass

        # Fallback
        return {
            'virality_score': 50.0,
            'hook_score': 50.0,
            'pacing_score': 50.0,
            'emotion_score': 50.0,
            'retention_score': 50.0,
            'recommendations': [],
            'optimal_cuts': [],
        }

    async def apply_cuts(
        self,
        topic: Dict[str, Any],
        optimal_cuts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Apply optimal cuts to topic timing

        Args:
            topic: Original topic with start_time, end_time
            optimal_cuts: List of cuts to apply

        Returns:
            Modified topic with adjusted timing
        """
        if not optimal_cuts:
            return topic

        # Sort cuts by start time
        cuts = sorted(optimal_cuts, key=lambda c: c['start'])

        # Calculate new duration after cuts
        total_cut_duration = sum(c['end'] - c['start'] for c in cuts)
        new_duration = (topic['end_time'] - topic['start_time']) - total_cut_duration

        # Update topic
        modified_topic = topic.copy()

        # Apply first cut (trim start)
        if cuts[0]['start'] == 0:
            modified_topic['start_time'] += cuts[0]['end']

        # Apply last cut (trim end)
        if cuts[-1]['end'] >= (topic['end_time'] - topic['start_time']):
            modified_topic['end_time'] -= (cuts[-1]['end'] - cuts[-1]['start'])

        logger.info(
            f"Applied {len(cuts)} cuts, "
            f"reduced duration by {total_cut_duration:.1f}s "
            f"({topic['end_time'] - topic['start_time']:.1f}s → {new_duration:.1f}s)"
        )

        return modified_topic

    def calculate_composite_score(
        self,
        virality_analysis: Dict[str, Any],
        llm_consensus_score: float,
    ) -> float:
        """
        Calculate composite score combining virality and LLM consensus

        Args:
            virality_analysis: Virality optimization results
            llm_consensus_score: LLM cascade consensus score

        Returns:
            Composite score (0-100)
        """
        # Weight: 60% LLM consensus, 40% virality
        composite = (
            llm_consensus_score * 0.6 +
            virality_analysis['virality_score'] * 0.4
        )

        return composite


logger.info("Virality optimizer module loaded")
