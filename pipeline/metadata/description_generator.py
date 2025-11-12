"""
Description and hashtag generator for platform-specific content
"""
import asyncio
from typing import Dict, Any, List

from loguru import logger
from anthropic import AsyncAnthropic

from ..common.config import config
from ..common.db import get_db, Topic


class DescriptionGenerator:
    """Generate platform-specific descriptions and hashtags"""

    # Max description lengths per platform
    MAX_LENGTHS = {
        'youtube': 5000,
        'tiktok': 2200,
        'x': 280,
        'linkedin': 3000,
        'instagram': 2200,
        'facebook': 2000,
        'reddit': 40000,
        'pinterest': 500,
        'snapchat': 250,
        'twitch': 300,
    }

    # Max hashtags per platform
    MAX_HASHTAGS = {
        'youtube': 15,
        'tiktok': 5,
        'x': 5,
        'linkedin': 10,
        'instagram': 30,
        'facebook': 5,
        'reddit': 0,  # No hashtags
        'pinterest': 20,
        'snapchat': 0,
        'twitch': 5,
    }

    def __init__(self):
        self.anthropic_client = None
        self.model = config.get('metadata.descriptions.llm_model', 'claude-3-5-sonnet-20241022')

    async def initialize(self):
        """Initialize LLM client"""
        api_key = config.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            raise Exception("Anthropic API key not configured")

        self.anthropic_client = AsyncAnthropic(api_key=api_key)
        logger.info("Description generator initialized")

    async def cleanup(self):
        """Cleanup resources"""
        if self.anthropic_client:
            await self.anthropic_client.close()
        logger.info("Description generator cleaned up")

    async def generate_description(
        self,
        extraction: Any,
        platform: str,
    ) -> str:
        """Generate platform-specific description"""
        max_length = self.MAX_LENGTHS.get(platform, 2000)

        # Load topic data
        topic_title = ""
        topic_description = ""
        topic_keywords = []

        if extraction.topic_id:
            async with get_db() as db:
                from sqlalchemy import select

                result = await db.execute(
                    select(Topic).where(Topic.id == extraction.topic_id)
                )
                topic = result.scalar_one_or_none()

                if topic:
                    topic_title = topic.title
                    topic_description = topic.description
                    topic_keywords = topic.keywords or []

        # Build prompt based on platform
        components = self._get_description_components(platform, extraction.type)

        prompt = f"""Generate a {platform} description for this video clip.

CONTENT:
- Topic: {topic_title}
- Description: {topic_description}
- Keywords: {', '.join(topic_keywords)}
- Type: {extraction.type} ({extraction.format})
- Duration: {extraction.end_time - extraction.start_time:.0f} seconds

PLATFORM: {platform}
MAX LENGTH: {max_length} characters

INCLUDE THESE COMPONENTS:
{chr(10).join('- ' + c for c in components)}

Return ONLY the description text."""

        # Call Claude
        response = await self.anthropic_client.messages.create(
            model=self.model,
            max_tokens=1500,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )

        description = response.content[0].text.strip()

        # Truncate if too long
        if len(description) > max_length:
            description = description[:max_length-3] + '...'

        logger.debug(f"Generated {platform} description ({len(description)} chars)")
        return description

    async def generate_hashtags(
        self,
        extraction: Any,
        platform: str,
    ) -> List[str]:
        """Generate platform-specific hashtags"""
        max_hashtags = self.MAX_HASHTAGS.get(platform, 10)

        if max_hashtags == 0:
            return []

        # Load topic data
        topic_keywords = []
        topic_category = ""

        if extraction.topic_id:
            async with get_db() as db:
                from sqlalchemy import select

                result = await db.execute(
                    select(Topic).where(Topic.id == extraction.topic_id)
                )
                topic = result.scalar_one_or_none()

                if topic:
                    topic_keywords = topic.keywords or []
                    topic_category = topic.category

        # Build prompt
        prompt = f"""Generate hashtags for a {platform} post.

CONTENT:
- Category: {topic_category}
- Keywords: {', '.join(topic_keywords)}
- Type: {extraction.type}

PLATFORM: {platform}
MAX HASHTAGS: {max_hashtags}

GUIDELINES:
- Mix broad and niche hashtags
- Include trending topics if relevant
- Platform-appropriate style
- No spaces in hashtags
- Use proper capitalization (e.g., #FederalReserve not #federalreserve)

Return ONLY a JSON array of hashtags without the # symbol:
["HashtagOne", "HashtagTwo", "HashtagThree"]"""

        # Call Claude
        response = await self.anthropic_client.messages.create(
            model=self.model,
            max_tokens=500,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )

        # Parse JSON response
        import json
        import re

        content = response.content[0].text
        json_match = re.search(r'\[(.*?)\]', content, re.DOTALL)

        if json_match:
            try:
                hashtags = json.loads('[' + json_match.group(1) + ']')
                # Ensure no # symbols
                hashtags = [h.lstrip('#') for h in hashtags]
                return hashtags[:max_hashtags]
            except:
                pass

        # Fallback: use keywords as hashtags
        fallback = [kw.replace(' ', '') for kw in topic_keywords]
        return fallback[:max_hashtags]

    def _get_description_components(self, platform: str, extraction_type: str) -> List[str]:
        """Get required description components for platform"""

        base_components = [
            "Hook/opening (1-2 sentences)",
            "Brief summary of content",
            "Key points or takeaways",
        ]

        if platform == 'youtube':
            if extraction_type in ['long', 'medium']:
                return base_components + [
                    "Timestamps for key sections",
                    "Links to related videos or resources",
                    "Call to action (subscribe, comment)",
                    "Social media links",
                ]
            else:
                return base_components + [
                    "Call to action",
                    "Link to full video",
                ]

        elif platform == 'tiktok':
            return [
                "Hook in first sentence",
                "1-2 sentence summary",
                "Question or CTA to drive engagement",
            ]

        elif platform == 'x':
            return [
                "Concise summary (1-2 sentences max)",
                "Link to full content if applicable",
            ]

        elif platform == 'linkedin':
            return base_components + [
                "Professional insights",
                "Industry relevance",
                "Call to discussion",
            ]

        elif platform == 'instagram':
            return [
                "Casual, engaging opening",
                "Brief content summary",
                "Question or poll",
                "CTA",
            ]

        elif platform == 'facebook':
            return base_components + [
                "Conversational tone",
                "Question to encourage comments",
            ]

        elif platform == 'reddit':
            return [
                "Detailed, informative summary",
                "Context and background",
                "Discussion points",
                "Transparency about source",
            ]

        else:
            return base_components


logger.info("Description generator module loaded")
