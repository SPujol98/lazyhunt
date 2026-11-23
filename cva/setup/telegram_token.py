"""The Telegram bot token: its shape, a startup check and log redaction (beta4 S1).

A token pasted in the wrong place (an AI API key in ``CVA_TELEGRAM_TOKEN``)
used to surface as a python-telegram-bot traceback that printed the rejected
value. Three guards keep a token out of the terminal and the logs:

* :data:`TOKEN_SHAPE`: what BotFather hands out (``<bot id>:<secret>``);
  ``cli.py init`` refuses anything else;
* :func:`check_token`: one ``getMe`` call before the bot prints anything
  (claim code, setup mode, full app). Telegram rejecting the token is
  ``"rejected"``; a network problem is ``"unreachable"`` (polling retries it);
* :class:`RedactFilter` / :func:`install_redaction`: replace the token with
  ``<redacted>`` in every log record, its traceback included (PTB puts the
  token in :class:`telegram.error.InvalidToken` messages and in request URLs).
"""

import asyncio
import contextlib
import logging
import re
import traceback
from typing import Final, Literal

TOKEN_SHAPE: Final[re.Pattern[str]] = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
REDACTED: Final[str] = "<redacted>"

TokenStatus = Literal["ok", "rejected", "unreachable"]


def looks_like_token(value: str) -> bool:
    """True when ``value`` has the shape of a BotFather token."""
    return TOKEN_SHAPE.match(value) is not None


def tail(value: str, keep: int = 4) -> str:
    """The last ``keep`` characters, the only part of a secret ever shown."""
    return f"…{value[-keep:]}" if len(value) > keep else "…"


def redact(text: str, token: str) -> str:
    """``text`` with every occurrence of ``token`` replaced."""
    return text.replace(token, REDACTED) if token else text


class RedactFilter(logging.Filter):
    """Handler filter: the token never reaches a log line or traceback."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        token = self._token
        if not token:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a bad format string is logged as is
            message = str(record.msg)
        if token in message:
            record.msg = redact(message, token)
            record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = record.exc_text.rstrip("\n")
        if record.exc_text and token in record.exc_text:
            record.exc_text = redact(record.exc_text, token)
        if record.stack_info and token in record.stack_info:
            record.stack_info = redact(record.stack_info, token)
        return True


def install_redaction(token: str, logger: logging.Logger | None = None) -> None:
    """Add a :class:`RedactFilter` to every handler of ``logger`` (root by
    default): handler filters see the records of every child logger too."""
    target = logger if logger is not None else logging.getLogger()
    for handler in target.handlers:
        handler.addFilter(RedactFilter(token))


async def _get_me(token: str) -> TokenStatus:
    from telegram import Bot
    from telegram.error import InvalidToken, NetworkError

    bot = Bot(token)
    try:
        await bot.initialize()  # calls getMe
    except InvalidToken:
        return "rejected"
    except NetworkError:
        return "unreachable"
    finally:
        with contextlib.suppress(Exception):  # closing a never-used client
            await bot.shutdown()
    return "ok"


def check_token(token: str) -> TokenStatus:
    """Ask Telegram (``getMe``) whether it accepts ``token``.

    Runs on a private event loop that is closed afterwards, so the bot's own
    ``run_polling`` still finds no loop of ours. Nothing is logged here.
    """
    if not token:
        return "rejected"
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_get_me(token))
    finally:
        loop.close()
