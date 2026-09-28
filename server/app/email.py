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


def _money(minor: int, currency: str) -> str:
    symbol = {"gbp": "£", "eur": "€"}.get(currency.lower(), currency.upper() + " ")
    return f"{symbol}{minor / 100:,.2f}"


def welcome_set_password(to: str, link: str) -> Email:
    return Email(
        to,
        "Welcome to BuddyAI — set your password",
        "Hi,\n\nThank you for your order! We created your BuddyAI account with this email address.\n"
        f"Set your password here (valid for 1 hour, you can request a new link any time):\n{link}\n\n"
        "When your watch arrives, sign in and choose \"Add watch\".{footer}".replace("{footer}", _footer()),
    )


def order_confirmed(to: str, order_id: int, amount_minor: int, currency: str) -> Email:
    return Email(
        to,
        f"Your BuddyAI order #{order_id}",
        f"Hi,\n\nWe received your order #{order_id} ({_money(amount_minor, currency)} incl. VAT and shipping).\n"
        "We'll email you the tracking number as soon as it ships.\n\n"
        "You can cancel within 14 days of delivery for a full refund (your statutory right to cancel)."
        + _footer(),
    )


def order_shipped(to: str, order_id: int, carrier: str, tracking: str) -> Email:
    track = f"\nCarrier: {carrier}\nTracking number: {tracking}\n" if tracking else "\n"
    return Email(to, f"Your BuddyAI order #{order_id} is on its way", f"Hi,\n\nGood news: your order has shipped.{track}{_footer()}")


def payment_failed(to: str) -> Email:
    return Email(
        to,
        "Payment problem with your BuddyAI subscription",
        "Hi,\n\nWe couldn't take the payment for your BuddyAI Care subscription. We'll retry automatically.\n"
        "Please update your card in your account (Account > Manage subscription) to keep your assistant working."
        + _footer(),
    )


def allowance_warning(to: str) -> Email:
    return Email(
        to,
        "You've used most of this month's BuddyAI allowance",
        "Hi,\n\nYou've used about 80% of this month's fair-use allowance for your assistant.\n"
        "It resets on the 1st of next month." + _footer(),
    )


def trial_ending(to: str, trial_end) -> Email:
    when = trial_end.strftime("%d %B %Y") if trial_end else "in a few days"
    body = (
        "Hi,\n\n"
        f"Your free BuddyAI Care period ends on {when}. After that your subscription continues "
        "at the monthly price shown when you bought your watch, charged to the card on file.\n\n"
        "Nothing to do if you want to keep your assistant. To cancel, go to Account > Manage subscription "
        "before that date and you won't be charged."
    )
    return Email(to, "Your BuddyAI Care free period ends soon", body + _footer())
