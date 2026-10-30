"""Controlled page download for extraction (search result URLs are untrusted input).

- http / https only, default ports only, no user:password@ in the URL
- the host name is resolved here and every address must be public (ipaddress.is_global): loopback, private,
  link-local (cloud metadata 169.254.169.254), CGNAT, multicast, reserved and IPv4-mapped private addresses
  are refused. The request then goes to the vetted address itself (Host header + TLS SNI keep the name), so a
  second DNS answer cannot swap in an internal address
- redirects are followed by hand, at most MAX_REDIRECTS, each one checked again; https -> http is refused
- certificates are always verified (no insecure retry); only text/html or application/xhtml+xml; the body is
  streamed and refused past max_bytes; one deadline for the whole download
- an honest User-Agent, no cookies, and at most one download per site every DOMAIN_GAP_S
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_REDIRECTS = 3
DOMAIN_GAP_S = 1.0
USER_AGENT = "olaCompanionBot/1.0 (+https://olacompanion.com; answers a user's spoken question)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchRefused(Exception):
    """The URL or its response is not allowed (reason in the message, never the URL's query string)."""


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(i[4][0]) for i in infos))


def public_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None and not mapped.is_global:
            return False
    return ip.is_global and not ip.is_multicast


def check_url(url: str) -> tuple[str, str, int]:
    """(scheme, host, port) of an allowed URL, or FetchRefused."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchRefused("malformed url") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise FetchRefused("scheme")
    if parts.username is not None or parts.password is not None:
        raise FetchRefused("credentials in url")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchRefused("no host")
    default = 443 if scheme == "https" else 80
    if port not in (None, default):
        raise FetchRefused("port")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
        raise FetchRefused("internal host name")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not public_ip(host):
            raise FetchRefused("private address")
    return scheme, host, default


class PageFetcher:
    def __init__(self, *, max_bytes: int = 1_500_000, max_concurrency: int = 4, resolver: Resolver | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic) -> None:
        self.max_bytes = max_bytes
        self.resolve = resolver or system_resolve
        self._transport = transport
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._last: dict[str, float] = {}
        self.clock = clock

    async def _vetted_ip(self, host: str, port: int) -> str:
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
        try:
            addrs = await self.resolve(host, port)
        except OSError as exc:
            raise FetchRefused("dns") from exc
        if not addrs or not all(public_ip(a) for a in addrs):
            raise FetchRefused("private address")
        return addrs[0]

    def _polite(self, host: str) -> None:
        now = self.clock()
        last = self._last.get(host)
        if last is not None and now - last < DOMAIN_GAP_S:
            raise FetchRefused("site rate limit")
        self._last[host] = now
        if len(self._last) > 2000:
            self._last.clear()

    async def fetch(self, url: str, timeout: float) -> tuple[str, bytes]:
        """(final url, html bytes) or FetchRefused / httpx errors / asyncio.TimeoutError."""
        async with self._sem:
            return await asyncio.wait_for(self._fetch(url), timeout)

    async def _fetch(self, url: str) -> tuple[str, bytes]:
        async with httpx.AsyncClient(verify=True, follow_redirects=False, transport=self._transport,
                                     timeout=httpx.Timeout(10.0), trust_env=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                scheme, host, port = check_url(url)
                ip = await self._vetted_ip(host, port)
                self._polite(host)
                parts = urlsplit(url)
                netloc = f"[{ip}]" if ":" in ip else ip
                target = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
                headers = {"Host": host, "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml",
                           "Accept-Language": "en"}
                req = client.build_request("GET", target, headers=headers,
                                           extensions={"sni_hostname": host} if scheme == "https" else None)
                resp = await client.send(req, stream=True)
                try:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location", "")
                        if not location:
                            raise FetchRefused("redirect without location")
                        nxt = urljoin(url, location)
                        if scheme == "https" and urlsplit(nxt).scheme.lower() == "http":
                            raise FetchRefused("https to http redirect")
                        self._last.pop(urlsplit(nxt).hostname or "", None)  # a redirect is the same visit
                        url = nxt
                        continue
                    if resp.status_code != 200:
                        raise FetchRefused(f"http {resp.status_code}")
                    ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if ctype not in HTML_TYPES:
                        raise FetchRefused("content type")
                    length = resp.headers.get("content-length")
                    if length and length.isdigit() and int(length) > self.max_bytes:
                        raise FetchRefused("too large")
                    body = bytearray()
                    async for part in resp.aiter_bytes():
                        body += part
                        if len(body) > self.max_bytes:
                            raise FetchRefused("too large")
                    return url, bytes(body)
                finally:
                    await resp.aclose()
            raise FetchRefused("too many redirects")
