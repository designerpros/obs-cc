"""
Alert Manager
Sends critical alerts to Discord, Telegram, and PagerDuty
"""
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import json

from loguru import logger
import httpx

from ..common.config import config


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'


class AlertManager:
    """
    Centralized alerting system

    Phase 7: Production hardening
    - Multi-channel alerts (Discord, Telegram, PagerDuty)
    - Severity-based routing
    - Alert deduplication
    - Rate limiting
    - Escalation policies
    """

    def __init__(self):
        self.enabled = config.get('alerts.enabled', True)

        # Alert channels
        self.discord_webhook = config.get('alerts.discord_webhook')
        self.telegram_bot_token = config.get('alerts.telegram_bot_token')
        self.telegram_chat_id = config.get('alerts.telegram_chat_id')
        self.pagerduty_key = config.get('alerts.pagerduty_integration_key')

        # Alert configuration
        self.min_severity_discord = config.get('alerts.min_severity_discord', 'warning')
        self.min_severity_telegram = config.get('alerts.min_severity_telegram', 'error')
        self.min_severity_pagerduty = config.get('alerts.min_severity_pagerduty', 'critical')

        # Rate limiting
        self.alert_cooldown_seconds = config.get('alerts.cooldown_seconds', 300)
        self.recent_alerts: Dict[str, datetime] = {}

        # HTTP client
        self.client: Optional[httpx.AsyncClient] = None

    async def initialize(self):
        """Initialize alert manager"""
        self.client = httpx.AsyncClient(timeout=10.0)

        if self.enabled:
            logger.info(
                f"Alert manager initialized "
                f"(Discord: {bool(self.discord_webhook)}, "
                f"Telegram: {bool(self.telegram_bot_token)}, "
                f"PagerDuty: {bool(self.pagerduty_key)})"
            )
        else:
            logger.info("Alert manager disabled")

    async def cleanup(self):
        """Cleanup resources"""
        if self.client:
            await self.client.aclose()
        logger.info("Alert manager cleaned up")

    async def send_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Send alert to configured channels

        Args:
            title: Alert title
            message: Alert message
            severity: Alert severity
            metadata: Optional metadata
        """
        if not self.enabled:
            return

        # Check rate limiting
        alert_key = f"{title}:{severity.value}"
        if not self._should_send_alert(alert_key):
            logger.debug(f"Alert rate limited: {alert_key}")
            return

        # Mark alert as sent
        self.recent_alerts[alert_key] = datetime.utcnow()

        # Log the alert
        logger.info(f"Sending {severity.value} alert: {title}")

        # Send to appropriate channels based on severity
        tasks = []

        if self._should_send_to_discord(severity):
            tasks.append(self._send_discord(title, message, severity, metadata))

        if self._should_send_to_telegram(severity):
            tasks.append(self._send_telegram(title, message, severity, metadata))

        if self._should_send_to_pagerduty(severity):
            tasks.append(self._send_pagerduty(title, message, severity, metadata))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _should_send_alert(self, alert_key: str) -> bool:
        """Check if alert should be sent (rate limiting)"""
        if alert_key not in self.recent_alerts:
            return True

        last_sent = self.recent_alerts[alert_key]
        elapsed = (datetime.utcnow() - last_sent).total_seconds()

        return elapsed >= self.alert_cooldown_seconds

    def _should_send_to_discord(self, severity: AlertSeverity) -> bool:
        """Check if should send to Discord"""
        if not self.discord_webhook:
            return False

        severity_levels = {
            'info': 0,
            'warning': 1,
            'error': 2,
            'critical': 3,
        }

        return severity_levels[severity.value] >= severity_levels[self.min_severity_discord]

    def _should_send_to_telegram(self, severity: AlertSeverity) -> bool:
        """Check if should send to Telegram"""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return False

        severity_levels = {
            'info': 0,
            'warning': 1,
            'error': 2,
            'critical': 3,
        }

        return severity_levels[severity.value] >= severity_levels[self.min_severity_telegram]

    def _should_send_to_pagerduty(self, severity: AlertSeverity) -> bool:
        """Check if should send to PagerDuty"""
        if not self.pagerduty_key:
            return False

        severity_levels = {
            'info': 0,
            'warning': 1,
            'error': 2,
            'critical': 3,
        }

        return severity_levels[severity.value] >= severity_levels[self.min_severity_pagerduty]

    async def _send_discord(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        metadata: Optional[Dict[str, Any]],
    ):
        """Send alert to Discord"""
        try:
            # Color coding by severity
            colors = {
                'info': 0x3498db,  # Blue
                'warning': 0xf39c12,  # Orange
                'error': 0xe74c3c,  # Red
                'critical': 0x992d22,  # Dark red
            }

            # Build embed
            embed = {
                'title': f"🚨 {title}",
                'description': message,
                'color': colors[severity.value],
                'timestamp': datetime.utcnow().isoformat(),
                'footer': {
                    'text': f'Severity: {severity.value.upper()}',
                },
            }

            # Add metadata fields
            if metadata:
                embed['fields'] = [
                    {'name': key, 'value': str(value), 'inline': True}
                    for key, value in metadata.items()
                ]

            payload = {
                'embeds': [embed],
            }

            response = await self.client.post(
                self.discord_webhook,
                json=payload,
            )

            if response.status_code != 204:
                logger.error(f"Discord alert failed: HTTP {response.status_code}")
            else:
                logger.debug("Discord alert sent successfully")

        except Exception as e:
            logger.error(f"Failed to send Discord alert: {e}")

    async def _send_telegram(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        metadata: Optional[Dict[str, Any]],
    ):
        """Send alert to Telegram"""
        try:
            # Emoji by severity
            emojis = {
                'info': 'ℹ️',
                'warning': '⚠️',
                'error': '🔴',
                'critical': '🚨',
            }

            # Build message
            text = f"{emojis[severity.value]} **{title}**\n\n{message}"

            if metadata:
                text += "\n\n**Details:**"
                for key, value in metadata.items():
                    text += f"\n• {key}: {value}"

            payload = {
                'chat_id': self.telegram_chat_id,
                'text': text,
                'parse_mode': 'Markdown',
            }

            response = await self.client.post(
                f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
                json=payload,
            )

            if response.status_code != 200:
                logger.error(f"Telegram alert failed: HTTP {response.status_code}")
            else:
                logger.debug("Telegram alert sent successfully")

        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    async def _send_pagerduty(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        metadata: Optional[Dict[str, Any]],
    ):
        """Send alert to PagerDuty"""
        try:
            # PagerDuty severity mapping
            pd_severity = 'critical' if severity == AlertSeverity.CRITICAL else 'error'

            payload = {
                'routing_key': self.pagerduty_key,
                'event_action': 'trigger',
                'payload': {
                    'summary': title,
                    'severity': pd_severity,
                    'source': 'stream-pipeline',
                    'custom_details': {
                        'message': message,
                        **(metadata or {}),
                    },
                },
            }

            response = await self.client.post(
                'https://events.pagerduty.com/v2/enqueue',
                json=payload,
            )

            if response.status_code != 202:
                logger.error(f"PagerDuty alert failed: HTTP {response.status_code}")
            else:
                logger.debug("PagerDuty alert sent successfully")

        except Exception as e:
            logger.error(f"Failed to send PagerDuty alert: {e}")

    async def alert_system_error(
        self,
        service: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Send system error alert"""
        await self.send_alert(
            title=f"System Error: {service}",
            message=f"Service {service} encountered an error:\n{error}",
            severity=AlertSeverity.ERROR,
            metadata=metadata,
        )

    async def alert_critical_failure(
        self,
        service: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Send critical failure alert"""
        await self.send_alert(
            title=f"CRITICAL: {service} Failure",
            message=f"Service {service} has critically failed:\n{error}",
            severity=AlertSeverity.CRITICAL,
            metadata=metadata,
        )

    async def alert_resource_warning(
        self,
        resource: str,
        usage_percent: float,
        threshold: float,
    ):
        """Send resource usage warning"""
        await self.send_alert(
            title=f"Resource Warning: {resource}",
            message=f"{resource} usage at {usage_percent:.1f}% (threshold: {threshold:.1f}%)",
            severity=AlertSeverity.WARNING,
            metadata={
                'resource': resource,
                'usage_percent': f"{usage_percent:.1f}%",
                'threshold': f"{threshold:.1f}%",
            },
        )


# Global instance
alert_manager = AlertManager()


async def init_alert_manager():
    """Initialize global alert manager"""
    await alert_manager.initialize()


async def close_alert_manager():
    """Cleanup global alert manager"""
    await alert_manager.cleanup()


logger.info("Alert manager module loaded")
