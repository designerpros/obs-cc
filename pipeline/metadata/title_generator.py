"""
Title generator for platform-specific titles
"""
import asyncio
from typing import Dict, Any

from loguru import logger
from anthropic import AsyncAnthropic

from ..common.config import config
from ..common.db import get_db


class TitleGenerator:
    """Generate platform-specific titles using Claude"""

    # Platform-specific style guidelines
    PLATFORM_STYLES = {
        'youtube': 'seo_keyword_rich',  # "How The Fed's $2T Policy Will Impact Crypto Markets"
        'tiktok': 'hook_emoji',  # "💰 You Won't Believe What The Fed Just Did..."
        'x': 'concise_bold',  # "Fed drops $2T bombshell"
        'linkedin': 'professional_insight',  # "Federal Reserve Policy Analysis: Market Implications"
        'instagram': 'casual_engaging',  # "This Fed decision changes everything 👀"
        'facebook': 'conversational',  # "Here's why the Fed's latest move matters to you"
        'reddit': 'discussion_starter',  # "The Fed just did something unprecedented - here's the breakdown"
        'pinterest': 'visual_descriptive',  # "Understanding Federal Reserve Policy Changes"
        'snapchat': 'ultra_casual',  # "fed just changed everything 😱"
        'twitch': 'gaming_casual',  # "Fed's crazy new policy - let's break it down"
    }

    # Max title lengths per platform
    MAX_LENGTHS = {
        'youtube': 100,
        'tiktok': 150,
        'x': 280,
        'linkedin': 200,
        'instagram': 150,
        'facebook': 255,
        'reddit': 300,
        'pinterest': 100,
        'snapchat': 100,
        'twitch': 140,
    }

    def __init__(self):
        self.anthropic_client = None
        self.model = config.get('metadata.titles.llm_model', 'claude-3-5-sonnet-20241022')

    async def initialize(self):
        """Initialize LLM client"""
        api_key = config.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            raise Exception("Anthropic API key not configured")

        self.anthropic_client = AsyncAnthropic(api_key=api_key)
        logger.info("Title generator initialized")

    async def cleanup(self):
        """Cleanup resources"""
        if self.anthropic_client:
            await self.anthropic_client.close()
        logger.info("Title generator cleaned up")

    async def generate_title(
        self,
        extraction: Any,
        platform: str,
    ) -> str:
        """
        Generate platform-specific title

        Args:
            extraction: Extraction database object
            platform: Platform name

        Returns:
            Generated title
        """
        style = self.PLATFORM_STYLES.get(platform, 'conversational')
        max_length = self.MAX_LENGTHS.get(platform, 150)

        # Load topic data if available
        topic_title = ""
        topic_description = ""
        topic_category = ""

        if extraction.topic_id:
            # Get topic from database
            async with get_db() as db:
                from sqlalchemy import select
                from ..common.db import Topic

                result = await db.execute(
                    select(Topic).where(Topic.id == extraction.topic_id)
                )
                topic = result.scalar_one_or_none()

                if topic:
                    topic_title = topic.title
                    topic_description = topic.description
                    topic_category = topic.category

        # Build prompt
        prompt = f"""Generate a {platform} title for this video clip.

CONTENT:
- Topic: {topic_title}
- Description: {topic_description}
- Category: {topic_category}
- Type: {extraction.type} ({extraction.format})
- Duration: {extraction.end_time - extraction.start_time:.0f} seconds

PLATFORM: {platform}
STYLE: {style}
MAX LENGTH: {max_length} characters

GUIDELINES:
{self._get_style_guidelines(style)}

Return ONLY the title text, nothing else."""

        # Call Claude
        response = await self.anthropic_client.messages.create(
            model=self.model,
            max_tokens=200,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )

        title = response.content[0].text.strip()

        # Remove quotes if present
        title = title.strip('"\'')

        # Truncate if too long
        if len(title) > max_length:
            title = title[:max_length-3] + '...'

        logger.debug(f"Generated {platform} title: {title}")
        return title

    def _get_style_guidelines(self, style: str) -> str:
        """Get style-specific guidelines"""
        guidelines = {
            'seo_keyword_rich': """
- Include relevant keywords for search
- Use clear, descriptive language
- Focus on value proposition
- Example: "How To Analyze Federal Reserve Policy Changes in 2024"
""",
            'hook_emoji': """
- Start with attention-grabbing statement
- Use 2-3 relevant emojis
- Create curiosity gap
- Example: "💰 The Fed's Secret Move That Nobody Saw Coming 😱"
""",
            'concise_bold': """
- Short, punchy statement
- No fluff or filler
- Bold claims that intrigue
- Example: "Fed's $2T gamble backfires"
""",
            'professional_insight': """
- Professional, authoritative tone
- Focus on insights and analysis
- Avoid clickbait
- Example: "Federal Reserve Monetary Policy: Q4 2024 Analysis"
""",
            'casual_engaging': """
- Conversational, friendly tone
- 1-2 emojis maximum
- Relatable language
- Example: "This Fed decision changes everything 👀"
""",
            'conversational': """
- Natural, speaking tone
- Address viewer directly
- Create personal connection
- Example: "Here's why the Fed's latest move matters to you"
""",
            'discussion_starter': """
- Pose interesting question or statement
- Encourage community engagement
- Avoid clickbait
- Example: "The Fed just did something unprecedented - thoughts?"
""",
            'visual_descriptive': """
- Clear, descriptive
- Emphasize visual/educational value
- Pinterest-friendly
- Example: "Understanding Federal Reserve Policy Changes [Explained]"
""",
            'ultra_casual': """
- Very short, casual
- Lowercase acceptable
- More emojis ok
- Example: "fed went crazy 😱💰"
""",
            'gaming_casual': """
- Casual gaming community style
- Mix of excitement and analysis
- Example: "Fed's new move is WILD - let's analyze"
""",
        }

        return guidelines.get(style, guidelines['conversational'])


logger.info("Title generator module loaded")
