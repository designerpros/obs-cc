"""
Notification utilities for Discord and Telegram
"""
import os
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

import httpx
from loguru import logger

# Discord configuration
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')


class NotificationManager:
    """Manage notifications to Discord and Telegram"""

    def __init__(self):
        self.discord_enabled = bool(DISCORD_WEBHOOK_URL)
        self.telegram_enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        self.http_client: Optional[httpx.AsyncClient] = None

        # Track message IDs for editing
        self.progress_messages: Dict[UUID, Dict[str, Any]] = {}

        logger.info(
            f"Notifications initialized - Discord: {self.discord_enabled}, "
            f"Telegram: {self.telegram_enabled}"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client"""
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=30.0)
        return self.http_client

    async def close(self):
        """Close HTTP client"""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

    async def send_discord(
        self,
        content: str,
        embed: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Send message to Discord

        Returns:
            Message ID if successful
        """
        if not self.discord_enabled:
            return None

        try:
            client = await self._get_client()

            payload = {"content": content}
            if embed:
                payload["embeds"] = [embed]

            response = await client.post(
                f"{DISCORD_WEBHOOK_URL}?wait=true",
                json=payload,
            )
            response.raise_for_status()

            message_data = response.json()
            return message_data.get('id')

        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")
            return None

    async def edit_discord(
        self,
        message_id: str,
        content: str,
        embed: Optional[Dict[str, Any]] = None,
    ):
        """Edit Discord message"""
        if not self.discord_enabled or not message_id:
            return

        try:
            client = await self._get_client()

            # Extract webhook ID and token from URL
            # Format: https://discord.com/api/webhooks/{id}/{token}
            parts = DISCORD_WEBHOOK_URL.split('/')
            webhook_id = parts[-2]
            webhook_token = parts[-1]

            payload = {"content": content}
            if embed:
                payload["embeds"] = [embed]

            response = await client.patch(
                f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}",
                json=payload,
            )
            response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to edit Discord message: {e}")

    async def send_telegram(
        self,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        Send message to Telegram

        Returns:
            Message ID if successful
        """
        if not self.telegram_enabled:
            return None

        try:
            client = await self._get_client()

            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
            }

            if reply_markup:
                payload["reply_markup"] = reply_markup

            response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload,
            )
            response.raise_for_status()

            message_data = response.json()
            return message_data.get('result', {}).get('message_id')

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return None

    async def edit_telegram(
        self,
        message_id: int,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: Optional[Dict[str, Any]] = None,
    ):
        """Edit Telegram message"""
        if not self.telegram_enabled or not message_id:
            return

        try:
            client = await self._get_client()

            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            if reply_markup:
                payload["reply_markup"] = reply_markup

            response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                json=payload,
            )
            response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to edit Telegram message: {e}")

    async def notify(
        self,
        message: str,
        level: str = "info",
        stream_id: Optional[UUID] = None,
    ):
        """
        Send notification to both Discord and Telegram

        Args:
            message: Message text
            level: Message level (info, warning, error, success)
            stream_id: Optional stream ID for tracking
        """
        # Add emoji based on level
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅",
        }
        emoji = emoji_map.get(level, "📢")

        formatted_message = f"{emoji} {message}"

        # Send to Discord
        discord_color_map = {
            "info": 0x3498db,  # Blue
            "warning": 0xf39c12,  # Orange
            "error": 0xe74c3c,  # Red
            "success": 0x2ecc71,  # Green
        }

        embed = {
            "description": message,
            "color": discord_color_map.get(level, 0x95a5a6),
            "timestamp": datetime.utcnow().isoformat(),
        }

        if stream_id:
            embed["footer"] = {"text": f"Stream ID: {stream_id}"}

        await self.send_discord(content="", embed=embed)

        # Send to Telegram
        await self.send_telegram(formatted_message)

        logger.info(f"Notification sent ({level}): {message}")

    async def send_progress(
        self,
        stream_id: UUID,
        stream_name: str,
        stages: List[Dict[str, Any]],
        eta_seconds: Optional[int] = None,
    ):
        """
        Send/update progress bar message

        Args:
            stream_id: Stream ID
            stream_name: Stream name
            stages: List of stages with status
            eta_seconds: Estimated time remaining
        """
        # Calculate progress
        total_stages = len(stages)
        completed_stages = sum(1 for s in stages if s['status'] == 'completed')
        progress_percent = int((completed_stages / total_stages) * 100)

        # Build progress bar
        bar_length = 20
        filled_length = int(bar_length * progress_percent / 100)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)

        # Format ETA
        eta_text = ""
        if eta_seconds:
            hours = eta_seconds // 3600
            minutes = (eta_seconds % 3600) // 60
            eta_text = f"\n\n⏱️ ETA: {hours}h {minutes}m"

        # Build stage list
        stage_lines = []
        for stage in stages:
            status_emoji = {
                'pending': '⏸️',
                'running': '⏳',
                'completed': '✅',
                'failed': '❌',
            }.get(stage['status'], '❓')

            stage_text = f"{status_emoji} {stage['name']}"
            if stage['status'] == 'running' and stage.get('duration_remaining'):
                stage_text += f" ({stage['duration_remaining']} remaining)"
            elif stage['status'] == 'completed' and stage.get('duration'):
                stage_text += f" ({stage['duration']})"

            stage_lines.append(stage_text)

        stages_text = "\n".join(stage_lines)

        # Build full message
        message = (
            f"🚀 **Processing {stream_name}**\n\n"
            f"Progress: [{bar}] {progress_percent}%\n\n"
            f"{stages_text}{eta_text}"
        )

        # Check if we have an existing message to edit
        if stream_id in self.progress_messages:
            # Edit existing message
            msg_data = self.progress_messages[stream_id]

            if msg_data.get('discord_id'):
                await self.edit_discord(
                    msg_data['discord_id'],
                    content="",
                    embed={
                        "description": message,
                        "color": 0x3498db,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )

            if msg_data.get('telegram_id'):
                await self.edit_telegram(
                    msg_data['telegram_id'],
                    text=message,
                )
        else:
            # Send new message
            discord_id = await self.send_discord(
                content="",
                embed={
                    "description": message,
                    "color": 0x3498db,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )

            telegram_id = await self.send_telegram(message)

            self.progress_messages[stream_id] = {
                'discord_id': discord_id,
                'telegram_id': telegram_id,
            }

    async def send_completion_summary(
        self,
        stream_id: UUID,
        stream_name: str,
        stats: Dict[str, Any],
    ):
        """
        Send pipeline completion summary

        Args:
            stream_id: Stream ID
            stream_name: Stream name
            stats: Statistics dictionary
        """
        message = (
            f"🎉 **Pipeline Complete!**\n\n"
            f"**Stream:** {stream_name}\n\n"
            f"📹 **Extracted:**\n"
            f"- {stats.get('longs', 0)} longs\n"
            f"- {stats.get('shorts', 0)} shorts\n"
            f"- {stats.get('micros', 0)} micros\n"
            f"- {stats.get('mediums', 0)} mediums\n\n"
            f"📅 **Scheduled:** {stats.get('total_posts', 0)} posts over "
            f"{stats.get('days_spread', 14)} days\n\n"
            f"💾 **Archived:** {stats.get('archived_size_gb', 0):.1f}GB to S3\n\n"
            f"📊 **Platform Distribution:**\n"
        )

        for platform, count in stats.get('platform_distribution', {}).items():
            message += f"- {platform.title()}: {count} videos\n"

        if stats.get('dashboard_url'):
            message += f"\n📊 **Details:** {stats['dashboard_url']}"

        await self.notify(message, level="success", stream_id=stream_id)

        # Clean up progress tracking
        if stream_id in self.progress_messages:
            del self.progress_messages[stream_id]

    async def send_failure_alert(
        self,
        stream_id: UUID,
        stream_name: str,
        stage: str,
        error_message: str,
        node: Optional[str] = None,
        gpu: Optional[str] = None,
    ):
        """
        Send pipeline failure alert

        Args:
            stream_id: Stream ID
            stream_name: Stream name
            stage: Failed stage
            error_message: Error message
            node: Node name
            gpu: GPU ID
        """
        node_info = f"\nNode: {node}" if node else ""
        gpu_info = f", GPU: {gpu}" if gpu else ""

        message = (
            f"❌ **Pipeline Failed**\n\n"
            f"**Stream:** {stream_name}\n"
            f"**Stage:** {stage}{node_info}{gpu_info}\n\n"
            f"**Error:** {error_message}\n\n"
            f"📋 **Logs:** `/var/log/pipeline/stream_{stream_id}.log`\n\n"
            f"**Actions:**\n"
            f"- 🔄 Retry Stage\n"
            f"- ⏭️ Skip to Next Stage\n"
            f"- 🛑 Cancel Pipeline"
        )

        await self.notify(message, level="error", stream_id=stream_id)

    async def send_daily_summary(
        self,
        date: str,
        stats: Dict[str, Any],
    ):
        """
        Send daily summary

        Args:
            date: Date string
            stats: Statistics dictionary
        """
        message = (
            f"📊 **Daily Summary - {date}**\n\n"
            f"🎬 **Streams Processed:** {stats.get('streams_processed', 0)}\n"
            f"📹 **Total Clips:** {stats.get('total_clips', 0)} "
            f"({stats.get('longs', 0)}L, {stats.get('shorts', 0)}S, "
            f"{stats.get('micros', 0)}M, {stats.get('mediums', 0)}Med)\n"
            f"📤 **Posts Published:** {stats.get('posts_published', 0)}\n"
            f"👁️ **Total Views:** {stats.get('total_views', 0):,} "
            f"(+{stats.get('views_change', 0):,} vs yesterday)\n"
            f"📈 **Top Clip:** {stats.get('top_clip_title', 'N/A')} "
            f"({stats.get('top_clip_views', 0):,} views, "
            f"{stats.get('top_clip_ctr', 0):.1f}% CTR)\n\n"
            f"💰 **LLM Costs:** ${stats.get('llm_costs', 0):.2f}\n"
            f"⏱️ **Total Processing Time:** {stats.get('processing_time', '0h 0m')}"
        )

        await self.notify(message, level="info")


# Global notification manager
notifier = NotificationManager()


async def init_notifications():
    """Initialize notification manager"""
    logger.info("Notifications initialized")


async def close_notifications():
    """Close notification manager"""
    await notifier.close()
    logger.info("Notifications closed")


logger.info("Notification utilities loaded")
