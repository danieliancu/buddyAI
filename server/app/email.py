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
from datetime import datetime, timezone
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


def _app_link(path: str) -> str:
    s = get_settings()
    return f"{(s.app_url.rstrip('/') or f'http://localhost:{s.port}')}{path}"


def watch_paired(to: str, watch_name: str, paired_at=None) -> Email:
    when = (paired_at or datetime.now(timezone.utc)).strftime("%d %B %Y, %H:%M UTC")
    link = _app_link("/my")
    return Email(
        to,
        "Your ola watch is connected",
        f"Hi,\n\n\"{watch_name}\" is now linked to your ola account ({when}).\n"
        "Tap the watch and start talking: your assistant is ready.\n"
        f"Open your account to choose the voice, language and companions:\n{link}\n\n"
        "Didn't connect a watch? Remove it in your account and change your password." + _footer(),
        html=designed(
            "Your watch is connected",
            f"“{watch_name}” is now linked to your ola account. Tap the watch and start talking: "
            "your assistant is ready.",
            "Open my ola account", link,
            "Didn't connect a watch? Remove it from your account and change your password.",
            details=[("Watch", watch_name), ("Account", to), ("Connected", when)],
        ),
    )


def _money(minor: int, currency: str) -> str:
    symbol = {"gbp": "£", "eur": "€"}.get(currency.lower(), currency.upper() + " ")
    return f"{symbol}{minor / 100:,.2f}"


def welcome_set_password(to: str, link: str, days_valid: int = 7) -> Email:
    return Email(
        to,
        "Welcome to ola — set your password",
        "Hi,\n\nThank you for your order! We created your ola account with this email address.\n"
        f"Set your password here (valid for {days_valid} days, you can request a new link any time):\n{link}\n\n"
        "Then, when your watch arrives: sign in, choose your phone (Android or iPhone), connect the watch to "
        "Wi-Fi and pair it. Your ola Care free trial starts when the watch is paired.{footer}".replace("{footer}", _footer()),
        html=designed(
            "Welcome to ola",
            "Thank you for your order! We created your ola account with this email address. Set a password now; "
            "when your watch arrives, sign in and follow the setup: choose your phone, connect the watch to Wi-Fi "
            "and pair it. Your ola Care free trial starts when the watch is paired.",
            "Set my password", link,
            f"The link is valid for {days_valid} days. You can ask for a new one any time from the sign-in page.",
        ),
    )


def order_confirmed(to: str, order_id: int, amount_minor: int, currency: str, care_terms: str | None = None) -> Email:
    care = (
        "\n\nola Care - what you agreed to at checkout:\n" + care_terms + "\n"
        "Nothing has been charged for ola Care. The free trial starts when you pair your watch; we'll email you "
        "the trial end date then, and again before the first payment."
        if care_terms else ""
    )
    return Email(
        to,
        f"Your ola order #{order_id}",
        f"Hi,\n\nWe received your order #{order_id} ({_money(amount_minor, currency)} incl. VAT and shipping).\n"
        "We'll email you the tracking number as soon as it ships.\n\n"
        "You can cancel within 14 days of delivery for a full refund (your statutory right to cancel)."
        + care
        + _footer(),
        html=designed(
            "Thank you for your order",
            "We received your order and we're getting your ola watch ready. We'll email you the tracking number "
            "as soon as it ships.",
            "Open my ola account", _app_link("/my/setup"),
            ("ola Care, as agreed at checkout: " + care_terms + " " if care_terms else "")
            + "You can cancel the watch within 14 days of delivery for a full refund (your statutory right to cancel).",
            details=[("Order", f"#{order_id}"), ("Paid today", f"{_money(amount_minor, currency)} incl. VAT and shipping")]
            + ([("ola Care", "Free trial starts when you pair your watch")] if care_terms else []),
        ),
    )


def order_payment_failed(to: str) -> Email:
    site = (get_settings().site_url or "").rstrip("/") or _app_link("")
    return Email(
        to,
        "Your ola order could not be paid",
        "Hi,\n\nYour bank did not confirm the payment for your ola watch, so the order was not placed and "
        f"nothing was charged. You can order again with another payment method:\n{site}\n" + _footer(),
        html=designed(
            "Your order could not be paid",
            "Your bank did not confirm the payment for your ola watch, so the order was not placed and nothing "
            "was charged.",
            "Order again", site,
            "You can use another card or payment method. Nothing was charged for ola Care either.",
        ),
    )


def care_trial_started(to: str, trial_end, amount_minor: int, currency: str, interval: str = "month") -> Email:
    when = trial_end.strftime("%d %B %Y") if trial_end else "the end of your free trial"
    price = f"{_money(amount_minor, currency)} per {interval}"
    link = _app_link("/my/account")
    return Email(
        to,
        "Your ola Care free trial has started",
        "Hi,\n\nYour watch is paired, so your ola Care free trial has started.\n"
        f"Free until: {when}\n"
        f"Then: {price}, charged to the card you saved at checkout, until you cancel.\n\n"
        "To cancel, go to Account > Manage subscription before the trial ends and you won't be charged. "
        f"We'll remind you a few days before the first payment.\n{link}\n" + _footer(),
        html=designed(
            "Your free trial has started",
            "Your watch is paired, so ola Care is on: your assistant, reminders and notes, free until the end of "
            "the trial.",
            "See my plan", link,
            "To cancel, go to Account > Manage subscription before the trial ends and you won't be charged. "
            "We'll remind you a few days before the first payment.",
            details=[("Free until", when), ("Then", price), ("Paid with", "The card you saved at checkout")],
        ),
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


def allowance_warning(to: str, line: str) -> Email:
    """`line`: e.g. "You've used 80% of your monthly AI interactions. Your allowance renews on 8 November 2026."
    (app/usage_notices.web_text)."""
    return Email(
        to,
        "You've used most of this month's ola AI interactions",
        f"Hi,\n\n{line}\nYou can see your usage any time in your ola account (Account > ola Care)." + _footer(),
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


def _details_html(details: list[tuple[str, str]] | None, font: str) -> str:
    """A small summary box (label / value rows) between the text and the button."""
    if not details:
        return ""
    rows = "".join(
        f'<tr><td style="font-family:{font};font-size:13px;line-height:20px;color:#7a8099;padding:6px 12px 6px 0;'
        f'white-space:nowrap;vertical-align:top">{escape(k)}</td>'
        f'<td align="right" style="font-family:{font};font-size:14px;line-height:20px;font-weight:600;color:#0f1533;'
        f'padding:6px 0;word-break:break-word">{escape(v)}</td></tr>'
        for k, v in details
    )
    return (
        '<tr><td style="padding:0 0 28px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="background:#f6f7fd;border:1px solid #eceef6;border-radius:14px">'
        f'<tr><td style="padding:12px 18px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0">{rows}</table></td></tr></table></td></tr>'
    )


def designed(title: str, intro: str, button: str, link: str, note: str, details: list[tuple[str, str]] | None = None) -> str:
    """One centred card: logo, title, text, optional summary box, button, small print, footer."""
    t, i, b, n, url = escape(title), escape(intro), escape(button), escape(note), escape(link, quote=True)
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    box = _details_html(details, font)
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
    {box}
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
