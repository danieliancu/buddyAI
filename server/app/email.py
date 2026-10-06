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
from html import escape
from typing import Protocol

from app.config import get_settings

log = logging.getLogger("buddyai.email")


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None  # designed version; `text` stays the plain-text alternative


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
        if email.html:
            msg.add_alternative(email.html, subtype="html")
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
    return "\n\n— ola\nYou received this email because of your ola account."


def verify_email(to: str, link: str) -> Email:
    return Email(
        to, "Confirm your email for ola",
        f"Hi,\n\nPlease confirm your email address:\n{link}\n\nThe link is valid for 48 hours.{_footer()}",
        html=designed(
            "Confirm your email",
            "Thanks for creating your ola account. Please confirm this is your email address, so we can keep "
            "your account safe and reach you about your watch.",
            "Confirm my email", link,
            "The link is valid for 48 hours. If you didn't create an ola account, you can ignore this email.",
        ),
    )


def reset_password(to: str, link: str) -> Email:
    return Email(
        to,
        "Reset your ola password",
        f"Hi,\n\nSomeone (hopefully you) asked to reset your ola password:\n{link}\n\n"
        f"The link is valid for 1 hour. If you didn't ask for this, ignore this email.{_footer()}",
        html=designed(
            "Reset your password",
            "Someone (hopefully you) asked to reset the password of your ola account.",
            "Choose a new password", link,
            "The link is valid for 1 hour. If you didn't ask for this, ignore this email: your password stays the same.",
        ),
    )


def watch_paired(to: str, watch_name: str) -> Email:
    return Email(to, "Your ola watch is connected", f"Hi,\n\n\"{watch_name}\" is now linked to your account.{_footer()}")


def _money(minor: int, currency: str) -> str:
    symbol = {"gbp": "£", "eur": "€"}.get(currency.lower(), currency.upper() + " ")
    return f"{symbol}{minor / 100:,.2f}"


def welcome_set_password(to: str, link: str) -> Email:
    return Email(
        to,
        "Welcome to ola — set your password",
        "Hi,\n\nThank you for your order! We created your ola account with this email address.\n"
        f"Set your password here (valid for 1 hour, you can request a new link any time):\n{link}\n\n"
        "When your watch arrives, sign in and choose \"Add watch\".{footer}".replace("{footer}", _footer()),
        html=designed(
            "Welcome to ola",
            "Thank you for your order! We created your ola account with this email address. Set a password now; "
            "when your watch arrives, sign in and choose \u201cAdd watch\u201d.",
            "Set my password", link,
            "The link is valid for 1 hour. You can ask for a new one any time from the sign-in page.",
        ),
    )


def order_confirmed(to: str, order_id: int, amount_minor: int, currency: str) -> Email:
    return Email(
        to,
        f"Your ola order #{order_id}",
        f"Hi,\n\nWe received your order #{order_id} ({_money(amount_minor, currency)} incl. VAT and shipping).\n"
        "We'll email you the tracking number as soon as it ships.\n\n"
        "You can cancel within 14 days of delivery for a full refund (your statutory right to cancel)."
        + _footer(),
    )


def order_shipped(to: str, order_id: int, carrier: str, tracking: str) -> Email:
    track = f"\nCarrier: {carrier}\nTracking number: {tracking}\n" if tracking else "\n"
    return Email(to, f"Your ola order #{order_id} is on its way", f"Hi,\n\nGood news: your order has shipped.{track}{_footer()}")


def payment_failed(to: str) -> Email:
    return Email(
        to,
        "Payment problem with your ola subscription",
        "Hi,\n\nWe couldn't take the payment for your ola Care subscription. We'll retry automatically.\n"
        "Please update your card in your account (Account > Manage subscription) to keep your assistant working."
        + _footer(),
    )


def allowance_warning(to: str) -> Email:
    return Email(
        to,
        "You've used most of this month's ola allowance",
        "Hi,\n\nYou've used about 80% of this month's fair-use allowance for your assistant.\n"
        "It resets on the 1st of next month." + _footer(),
    )


def trial_ending(to: str, trial_end) -> Email:
    when = trial_end.strftime("%d %B %Y") if trial_end else "in a few days"
    body = (
        "Hi,\n\n"
        f"Your free ola Care period ends on {when}. After that your subscription continues "
        "at the monthly price shown when you bought your watch, charged to the card on file.\n\n"
        "Nothing to do if you want to keep your assistant. To cancel, go to Account > Manage subscription "
        "before that date and you won't be charged."
    )
    return Email(to, "Your ola Care free period ends soon", body + _footer())


# --- designed (HTML) emails -----------------------------------------------------------------------
# Email clients support CSS unevenly: tables, inline styles, system fonts, a PNG logo from the public site
# (never SVG), and a button that is a plain link. The plain-text version is always sent too.

def _logo_html() -> str:
    site = get_settings().site_url.rstrip("/")
    if site:
        return (f'<img src="{escape(site)}/email/logo.png" width="200" alt="olacompanion" '
                'style="display:block;margin:0 auto;border:0;outline:none;text-decoration:none;'
                'width:200px;height:auto;max-width:200px">')
    return ('<span style="font-size:26px;font-weight:700;letter-spacing:-0.5px;color:#0f1533">ola'
            '<span style="color:#5b4cf0">companion</span></span>')


def designed(title: str, intro: str, button: str, link: str, note: str) -> str:
    """One centred card: logo, title, text, button, small print, footer."""
    t, i, b, n, url = escape(title), escape(intro), escape(button), escape(note), escape(link, quote=True)
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{t}</title></head>
<body style="margin:0;padding:0;background:#f4f5fb;-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{i}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f5fb">
<tr><td align="center" style="padding:40px 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px">
  <tr><td align="center" style="padding:0 0 28px">{_logo_html()}</td></tr>
  <tr><td style="background:#ffffff;border-radius:20px;border:1px solid #e6e8f4;padding:40px 32px;font-family:{font}">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td align="center" style="font-family:{font};font-size:24px;line-height:32px;font-weight:700;color:#0f1533;padding:0 0 12px">{t}</td></tr>
    <tr><td align="center" style="font-family:{font};font-size:16px;line-height:25px;color:#4a5170;padding:0 0 28px">{i}</td></tr>
    <tr><td align="center" style="padding:0 0 28px">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
      <td align="center" bgcolor="#5b4cf0" style="border-radius:12px;background:#5b4cf0;background-image:linear-gradient(90deg,#4f6bff,#7b4cf0)">
        <a href="{url}" target="_blank" style="display:inline-block;padding:15px 34px;font-family:{font};font-size:16px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:12px">{b}</a>
      </td></tr></table>
    </td></tr>
    <tr><td align="center" style="font-family:{font};font-size:13px;line-height:20px;color:#7a8099;padding:0 0 22px">{n}</td></tr>
    <tr><td align="center" style="border-top:1px solid #eceef6;padding:18px 0 0;font-family:{font};font-size:12px;line-height:18px;color:#9aa0b8">
      Button not working? Copy this link into your browser:<br>
      <a href="{url}" target="_blank" style="color:#5b4cf0;word-break:break-all;text-decoration:underline">{escape(link)}</a>
    </td></tr>
    </table>
  </td></tr>
  <tr><td align="center" style="padding:24px 12px 0;font-family:{font};font-size:12px;line-height:18px;color:#9aa0b8">
    You received this email because of your ola account.<br>Ola Technologies London Ltd &middot; Essex, United Kingdom
  </td></tr>
  </table>
</td></tr></table>
</body></html>"""
