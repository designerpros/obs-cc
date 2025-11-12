"""
Late API client for multi-platform posting
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import httpx

from loguru import logger

from ..common.config import config


class LateAPIClient:
    """
    Client for Late API (https://www.late.dev)
    Handles posting to all 10 supported platforms
    """

    SUPPORTED_PLATFORMS = [
        'youtube', 'tiktok', 'instagram', 'facebook', 'x',
        'linkedin', 'pinterest', 'snapchat', 'twitch', 'reddit'
    ]

    def __init__(self):
        self.api_endpoint = config.get('posting.late.api_endpoint')
        self.api_key = config.get('posting.late.api_key')
        self.timeout = config.get('posting.late.timeout_seconds', 300)
        self.http_client: Optional[httpx.AsyncClient] = None

    async def initialize(self):
        """Initialize HTTP client"""
        if not self.api_endpoint or not self.api_key:
            raise Exception("Late API endpoint and key must be configured")

        self.http_client = httpx.AsyncClient(
            base_url=self.api_endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

        logger.info(f"Late API client initialized: {self.api_endpoint}")

    async def cleanup(self):
        """Cleanup HTTP client"""
        if self.http_client:
            await self.http_client.aclose()
        logger.info("Late API client cleaned up")

    async def schedule_post(
        self,
        platforms: List[str],
        video_url: str,
        title: str,
        description: str,
        thumbnail_url: Optional[str] = None,
        scheduled_time: Optional[datetime] = None,
        hashtags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Schedule a post across multiple platforms

        Args:
            platforms: List of platform names
            video_url: URL to video file (must be publicly accessible)
            title: Post title
            description: Post description
            thumbnail_url: Optional thumbnail URL
            scheduled_time: When to post (None = immediate)
            hashtags: List of hashtags
            metadata: Additional platform-specific metadata

        Returns:
            Late job response
        """
        # Validate platforms
        invalid_platforms = [p for p in platforms if p not in self.SUPPORTED_PLATFORMS]
        if invalid_platforms:
            raise ValueError(f"Invalid platforms: {invalid_platforms}")

        # Build request payload
        payload = {
            "platforms": platforms,
            "mediaItems": [{
                "type": "video",
                "url": video_url,
            }],
            "content": description,
            "title": title,
        }

        if thumbnail_url:
            payload["mediaItems"].append({
                "type": "image",
                "url": thumbnail_url,
            })

        if scheduled_time:
            payload["scheduledFor"] = scheduled_time.isoformat()

        if hashtags:
            payload["hashtags"] = hashtags

        if metadata:
            payload["metadata"] = metadata

        # Make API request
        try:
            response = await self.http_client.post("/v1/posts", json=payload)
            response.raise_for_status()

            result = response.json()
            logger.info(f"Scheduled post to {len(platforms)} platforms: {result.get('id')}")

            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"Late API error: {e.response.status_code} - {e.response.text}")
            raise Exception(f"Late API failed: {e.response.text}")

        except Exception as e:
            logger.exception(f"Error scheduling post: {e}")
            raise

    async def get_post_status(self, post_id: str) -> Dict[str, Any]:
        """
        Get status of a scheduled post

        Args:
            post_id: Late post ID

        Returns:
            Post status
        """
        try:
            response = await self.http_client.get(f"/v1/posts/{post_id}")
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"Error getting post status: {e}")
            raise

    async def get_analytics(
        self,
        post_id: str,
        platform: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get analytics for a post

        Args:
            post_id: Late post ID
            platform: Optional specific platform

        Returns:
            Analytics data
        """
        try:
            url = f"/v1/posts/{post_id}/analytics"
            if platform:
                url += f"?platform={platform}"

            response = await self.http_client.get(url)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"Error getting analytics: {e}")
            raise

    async def update_thumbnail(
        self,
        post_id: str,
        platform: str,
        thumbnail_url: str,
    ) -> Dict[str, Any]:
        """
        Update thumbnail for a post (for A/B testing)

        Args:
            post_id: Late post ID
            platform: Platform to update
            thumbnail_url: New thumbnail URL

        Returns:
            Update result
        """
        try:
            response = await self.http_client.patch(
                f"/v1/posts/{post_id}/thumbnail",
                json={
                    "platform": platform,
                    "thumbnailUrl": thumbnail_url,
                },
            )
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"Error updating thumbnail: {e}")
            raise

    async def add_comment(
        self,
        post_id: str,
        platform: str,
        comment_text: str,
        pin: bool = True,
    ) -> Dict[str, Any]:
        """
        Add first comment to a post (for funnel)

        Args:
            post_id: Late post ID
            platform: Platform to comment on
            comment_text: Comment text
            pin: Whether to pin comment

        Returns:
            Comment result
        """
        try:
            response = await self.http_client.post(
                f"/v1/posts/{post_id}/comments",
                json={
                    "platform": platform,
                    "text": comment_text,
                    "pin": pin,
                },
            )
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            raise

    async def get_next_slot(
        self,
        profile_id: str,
        platform: str,
    ) -> Dict[str, Any]:
        """
        Get next available posting slot

        Args:
            profile_id: Late profile ID
            platform: Platform name

        Returns:
            Next slot info
        """
        try:
            response = await self.http_client.get(
                f"/v1/queue/next-slot",
                params={
                    "profileId": profile_id,
                    "platform": platform,
                },
            )
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"Error getting next slot: {e}")
            raise


logger.info("Late API client module loaded")
