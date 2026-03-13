"""
Telegram gateway: send_alert (summary + optional plot) and optional listener for remote commands.

Supports:
- Slash commands: /status, /audit, /report, /run, /sync
- Natural Language Messaging: non-slash messages are routed via LLM to run skills, execute code, or query memory
- 5-message conversation buffer for follow-up questions (e.g. "Now make the line red")

Uses python-telegram-bot; token from TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN in .env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from telegram import Bot
    from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    Bot = None
    Application = None
    MessageHandler = None
    CallbackQueryHandler = None
    filters = None
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None

CONV_BUFFER_SIZE = 5


def _get_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")


class TelegramHandler:
    """
    Telegram listener with slash commands and Natural Language Messaging.
    Non-slash messages are passed to the NL handler (RPIExecutor flow).
    Maintains a 5-message conversation buffer per chat for follow-up context.
    """

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self._token = token or _get_token()
        self._chat_id = chat_id  # If None, alerts go to the last chat that sent a command (or nowhere)
        self._last_chat_id: int | None = None
        self._app: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._command_callback: Callable[[str, list[str], int], str] | None = None
        self._nl_handler: Callable[
            [str, int, list[dict[str, str]]],
            tuple[str, list[dict[str, str]], str | Path | None],
        ] | None = None
        self._hitl_response_callback: Callable[[int, bool], None] | None = None
        self._conv_buffers: dict[int, list[dict[str, str]]] = {}

    @property
    def chat_id(self) -> int | str | None:
        """Chat ID to send alerts to (set from TELEGRAM_CHAT_ID or first /command)."""
        if self._chat_id is not None:
            return int(self._chat_id) if isinstance(self._chat_id, str) and self._chat_id.isdigit() else self._chat_id
        return self._last_chat_id

    def _get_buffer(self, chat_id: int) -> list[dict[str, str]]:
        """Get conversation buffer for chat, trimmed to CONV_BUFFER_SIZE."""
        buf = self._conv_buffers.get(chat_id, [])
        return buf[-CONV_BUFFER_SIZE:] if len(buf) > CONV_BUFFER_SIZE else buf

    def _update_buffer(self, chat_id: int, buf: list[dict[str, str]]) -> None:
        """Store updated buffer, keeping last CONV_BUFFER_SIZE messages."""
        self._conv_buffers[chat_id] = buf[-CONV_BUFFER_SIZE:]

    def send_alert(self, text: str, image_path: str | Path | None = None) -> bool:
        """
        Send a summary (and optional image) to the configured chat.
        Safe to call from any thread. Returns True if sent.
        """
        if not self._token:
            logger.debug("Telegram token not set; skipping send_alert.")
            return False
        cid = self.chat_id
        if cid is None:
            logger.debug("No Telegram chat_id set; skipping send_alert.")
            return False
        path = Path(image_path) if image_path else None
        if path is not None and not path.is_file():
            path = None

        async def _send() -> bool:
            try:
                bot = Bot(token=self._token)
                await bot.send_message(chat_id=cid, text=text[:4000])
                if path is not None:
                    with path.open("rb") as f:
                        await bot.send_photo(chat_id=cid, photo=f)
                return True
            except Exception as exc:
                logger.exception("Telegram send_alert failed: %s", exc)
                return False

        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
            try:
                return future.result(timeout=30)
            except Exception:
                logger.exception("Telegram send_alert (threaded) failed.")
                return False
        try:
            return asyncio.run(_send())
        except Exception:
            logger.exception("Telegram send_alert failed.")
            return False

    def send_alert_with_buttons(self, text: str, chat_id: int | None = None, image_path: str | Path | None = None) -> bool:
        if not self._token or InlineKeyboardMarkup is None:
            return False
        cid = chat_id or self.chat_id
        if cid is None:
            return False
        path = Path(image_path) if image_path else None
        if path is not None and not path.is_file():
            path = None

        async def _send() -> bool:
            try:
                bot = Bot(token=self._token)
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Yes", callback_data="hitl_yes"), InlineKeyboardButton("No", callback_data="hitl_no")]
                ])
                await bot.send_message(chat_id=cid, text=text[:4000], reply_markup=keyboard)
                if path is not None:
                    with path.open("rb") as f:
                        await bot.send_photo(chat_id=cid, photo=f)
                return True
            except Exception as exc:
                logger.exception("Telegram send_alert_with_buttons failed: %s", exc)
                return False

        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
            try:
                return future.result(timeout=30)
            except Exception:
                return False
        try:
            return asyncio.run(_send())
        except Exception:
            return False

    def send_document(self, file_path: str | Path, caption: str | None = None) -> bool:
        """Send a file (e.g. report .md or .pdf) to the configured chat. Safe to call from any thread."""
        if not self._token:
            return False
        cid = self.chat_id
        if cid is None:
            return False
        path = Path(file_path)
        if not path.is_file():
            logger.warning("Telegram send_document: file not found %s", path)
            return False

        async def _send_doc() -> bool:
            try:
                bot = Bot(token=self._token)
                with path.open("rb") as f:
                    await bot.send_document(chat_id=cid, document=f, filename=path.name, caption=(caption or "")[:1000])
                return True
            except Exception as exc:
                logger.exception("Telegram send_document failed: %s", exc)
                return False

        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(_send_doc(), self._loop)
            try:
                return future.result(timeout=30)
            except Exception:
                return False
        try:
            return asyncio.run(_send_doc())
        except Exception:
            return False

    def start_listener(
        self,
        command_callback: Callable[[str, list[str], int], str],
        nl_handler: Callable[[str, int, list[dict[str, str]]], tuple[str, list[dict[str, str]]]] | None = None,
        hitl_response_callback: Callable[[int, bool], None] | None = None,
    ) -> None:
        if Application is None or not self._token:
            logger.info("Telegram listener not started (missing library or token).")
            return
        self._command_callback = command_callback
        self._nl_handler = nl_handler
        self._hitl_response_callback = hitl_response_callback

        async def handle_message(update: Any, context: Any) -> None:
            if update.effective_chat is None or update.message is None:
                return
            chat_id = update.effective_chat.id
            self._last_chat_id = chat_id
            text = (update.message.text or "").strip()
            if not text:
                return

            try:
                if text.startswith("/"):
                    # Slash command: /status, /audit, /run, etc.
                    parts = text.split()
                    command = parts[0].lower()
                    args = parts[1:] if len(parts) > 1 else []
                    reply = self._command_callback(command, args, chat_id)
                    if reply:
                        await update.message.reply_text(reply[:4000])
                else:
                    # Natural language: route via NL handler
                    if self._nl_handler is not None:
                        buffer = self._get_buffer(chat_id)
                        result = self._nl_handler(text, chat_id, buffer)
                        reply = result[0]
                        new_buffer = result[1]
                        image_path = result[2] if len(result) > 2 else None
                        self._update_buffer(chat_id, new_buffer)
                        if reply or image_path:
                            if image_path and Path(image_path).is_file():
                                with Path(image_path).open("rb") as f:
                                    await update.message.reply_photo(photo=f, caption=(reply or "")[:1024])
                            elif reply:
                                await update.message.reply_text(reply[:4000])
                    else:
                        await update.message.reply_text(
                            "Natural language is disabled. Use /status, /audit, /report, /run <skill>, or /sync."
                        )
            except Exception as exc:
                logger.exception("Message handler failed: %s", exc)
                await update.message.reply_text(f"Error: {exc}")

        async def handle_callback(update: Any, context: Any) -> None:
            if update.callback_query is None:
                return
            data = getattr(update.callback_query, "data", "") or ""
            cid = update.callback_query.message.chat.id if update.callback_query.message else None
            if cid is not None and self._hitl_response_callback is not None:
                self._hitl_response_callback(cid, data == "hitl_yes")
            await update.callback_query.answer()

        async def post_init(app: Any) -> None:
            self._loop = asyncio.get_event_loop()
            app.add_handler(MessageHandler(filters.TEXT, handle_message))
            if CallbackQueryHandler is not None:
                app.add_handler(CallbackQueryHandler(handle_callback))

        def run_bot() -> None:
            app = Application.builder().token(self._token).post_init(post_init).build()
            self._app = app
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            app.run_polling(allowed_updates=["message", "callback_query"])

        self._thread = threading.Thread(target=run_bot, name="telegram-listener", daemon=True)
        self._thread.start()
        nl_status = "NL enabled" if self._nl_handler else "slash commands only"
        logger.info("Telegram listener started (%s; HITL Yes/No).", nl_status)

    def stop_listener(self) -> None:
        if self._app is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._app.stop)
            except Exception:
                pass
        self._thread = None
        self._app = None
        self._loop = None
        self._conv_buffers.clear()
