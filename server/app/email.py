"""Outgoing email. `console` (default, development) logs the message; `smtp` sends it.

The provider is chosen later (see plan M7): any SMTP service works (Resend, Postmark, SES, ...).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from app.config import get_settings

log = logging.getLogger("buddyai.email")


@dataclass
class Email:
    to: str
    subject: str
    text: str


class EmailSender(Protocol):
    async def send(self, email: Email) -> None: ...


class ConsoleEmailSender:
    """Development: prints emails (including links) to the server log."""

    sent: list[Email] = []  # inspected by tests

    async def send(self, email: Email) -> None:
        ConsoleEmailSender.sent.append(email)
        log.info("EMAIL to=%s subject=%r\n%s", email.to, email.subject, email.text)


class SMTPEmailSender:
    async def send(self, email: Email) -> None:
        await asyncio.to_thread(self._send_sync, email)

    def _send_sync(self, email: Email) -> None:
        s = get_settings()
        msg = EmailMessage()
        msg["From"] = s.email_from
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.text)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)


def get_sender() -> EmailSender:
    return SMTPEmailSender() if get_settings().email_backend == "smtp" else ConsoleEmailSender()


async def send(email: Email) -> None:
    """Never raises: a failed email must not break the request that triggered it."""
    try:
        await get_sender().send(email)
    except Exception:  # noqa: BLE001
        log.exception("sending email to %s failed", email.to)


# --- templates --------------------------------------------------------------------------------

def _footer() -> str:
    return "\n\n— BuddyAI\nYou received this email because of your BuddyAI account."


def verify_email(to: str, link: str) -> Email:
    return Email(to, "Confirm your email for BuddyAI", f"Hi,\n\nPlease confirm your email address:\n{link}\n\nThe link is valid for 48 hours.{_footer()}")


def reset_password(to: str, link: str) -> Email:
    return Email(
        to,
        "Reset your BuddyAI password",
        f"Hi,\n\nSomeone (hopefully you) asked to reset your BuddyAI password:\n{link}\n\n"
        f"The link is valid for 1 hour. If you didn't ask for this, ignore this email.{_footer()}",
    )


def watch_paired(to: str, watch_name: str) -> Email:
    return Email(to, "Your BuddyAI watch is connected", f"Hi,\n\n\"{watch_name}\" is now linked to your account.{_footer()}")
