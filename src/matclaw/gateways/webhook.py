"""
Generic inbound webhook gateway.
POST /api/gateway/webhook — any platform (Slack, Discord, n8n, Zapier) can POST here.
"""
from __future__ import annotations

from pydantic import BaseModel


class WebhookRequest(BaseModel):
    channel: str = "webhook"
    chat_id: str = "default"
    user_id: str = "webhook_user"
    text: str
    metadata: dict = {}


class WebhookResponse(BaseModel):
    text: str
    channel: str = "webhook"
    chat_id: str = "default"
    plots: list[str] = []
    runtime: str = ""
    request_id: str = ""


__all__ = ["WebhookRequest", "WebhookResponse"]
