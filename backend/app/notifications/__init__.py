"""Notification channels.

The in-app channel (DB + realtime bus → browser) is always on. Email / Telegram / WhatsApp are
pluggable providers configured through environment variables; they are best-effort and never
block task execution.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class OutboundNotification:
    kind: str
    title: str
    body: str
    severity: str = "info"
    data: dict[str, Any] = field(default_factory=dict)
    recipients: list[str] = field(default_factory=list)


class NotificationProvider(Protocol):
    name: str

    def configured(self) -> bool: ...

    async def send(self, notification: OutboundNotification) -> bool: ...


class EmailProvider:
    """SMTP e-mail provider (uses the standard library; runs in a thread)."""

    name = "email"

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.smtp_host and s.smtp_from)

    async def send(self, notification: OutboundNotification) -> bool:
        if not self.configured() or not notification.recipients:
            return False
        return await asyncio.to_thread(self._send_sync, notification)

    def _send_sync(self, notification: OutboundNotification) -> bool:
        import smtplib
        from email.message import EmailMessage

        s = get_settings()
        msg = EmailMessage()
        msg["Subject"] = f"[{s.app_name}] {notification.title}"
        msg["From"] = s.smtp_from
        msg["To"] = ", ".join(notification.recipients)
        msg.set_content(notification.body or notification.title)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                if s.smtp_tls:
                    smtp.starttls()
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password or "")
                smtp.send_message(msg)
            return True
        except Exception as exc:
            logger.warning("Email notification failed: %s", exc)
            return False


class TelegramProvider:
    name = "telegram"

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.telegram_bot_token and s.telegram_chat_id)

    async def send(self, notification: OutboundNotification) -> bool:
        if not self.configured():
            return False
        s = get_settings()
        text = f"*{notification.title}*\n{notification.body}"[:4000]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                    json={"chat_id": s.telegram_chat_id, "text": text, "parse_mode": "Markdown"},
                )
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)
            return False


class WebhookProvider:
    """Generic webhook (covers WhatsApp Business API gateways, Slack, etc.)."""

    name = "webhook"

    def configured(self) -> bool:
        return bool(get_settings().notification_webhook_url)

    async def send(self, notification: OutboundNotification) -> bool:
        if not self.configured():
            return False
        s = get_settings()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    str(s.notification_webhook_url),
                    json={
                        "kind": notification.kind,
                        "title": notification.title,
                        "body": notification.body,
                        "severity": notification.severity,
                        "data": notification.data,
                    },
                )
                return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.warning("Webhook notification failed: %s", exc)
            return False


PROVIDERS: list[NotificationProvider] = [EmailProvider(), TelegramProvider(), WebhookProvider()]


def configured_channels() -> list[str]:
    return [p.name for p in PROVIDERS if p.configured()]


async def dispatch_external(notification: OutboundNotification) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for provider in PROVIDERS:
        if provider.configured():
            try:
                results[provider.name] = await provider.send(notification)
            except Exception as exc:  # never let a notification failure propagate
                logger.warning("Notification provider %s failed: %s", provider.name, exc)
                results[provider.name] = False
    return results
