"""Weather from MET Norway (api.met.no: free for commercial use, CC BY 4.0, attribution "MET Norway") for the
weather category, before any web search: forecast pages are heavy and their numbers sit in JavaScript, so web
search answered weather questions badly (and slowly).

- the town is geocoded with Nominatim (OpenStreetMap; at most one request a second, results cached here, ODbL
  attribution), preferring the watch's country; the same lookup tells web search whether a place is abroad
- the forecast is cached per position until its Expires header and refreshed with If-Modified-Since (MET's
  terms); coordinates are rounded to 4 decimals; sunrise / sunset come from MET's sunrise API
- both identify the service with an honest User-Agent

The result is one SearchResult with the facts as text, answered by the same answering call as web results.
Any failure returns None and the normal search runs instead.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx

from app.websearch.fetch import USER_AGENT
from app.websearch.providers import SharedClient, region_for
from app.websearch.types import SearchResult

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
SUN_URL = "https://api.met.no/weatherapi/sunrise/3.0/sun"
GEOCODE_GAP_S = 1.0  # Nominatim usage policy: at most one request a second
GEOCODE_CACHE = 1000

_WORDS = ("clearsky", "partly", "cloudy", "fair", "fog", "heavy", "light", "rain", "sleet", "snow", "showers",
          "thunder", "and")
_SPLIT = re.compile("|".join(sorted(_WORDS, key=len, reverse=True)))


def describe(symbol: str) -> str:
    """'lightrainshowers_day' -> 'light rain showers'."""
    base = (symbol or "").split("_", 1)[0]
    words = _SPLIT.findall(base)
    return " ".join("clear sky" if w == "clearsky" else w for w in words) if words and "".join(words) == base else base


class WeatherSource:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        self._client = SharedClient(transport=transport, headers=headers, follow_redirects=False)
        self.clock = clock
        self._places: OrderedDict[str, tuple[float, float, str, str] | None] = OrderedDict()  # lat, lon, name, cc
        self._forecasts: dict[tuple[float, float], tuple[float, str, dict]] = {}  # (expires, last-modified, json)
        self._sun: dict[tuple[float, float, str], tuple[str, str]] = {}
        self._geo_lock = asyncio.Lock()
        self._geo_last = 0.0

    async def geocode(self, place: str, tz: str, n: Counter) -> tuple[float, float, str] | None:
        found = await self._lookup(place, tz, n, settlement=True)
        return found[:3] if found else None

    async def abroad(self, place: str, tz: str, timeout: float, n: Counter) -> bool:
        """Whether `place` (a town, region or country) is outside the watch's country. Unknown, no time or any
        error: False (search keeps the watch's country preference)."""
        _, country = region_for(tz)
        if not country or not place.strip() or timeout <= 0.3:
            return False
        try:
            async with asyncio.timeout(timeout):
                found = await self._lookup(place, tz, n, settlement=False)
        except (httpx.HTTPError, TimeoutError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as exc:
            log.info("place lookup: %s", type(exc).__name__)
            return False
        return bool(found and found[3] and found[3] != country)

    async def _lookup(self, place: str, tz: str, n: Counter, settlement: bool) -> tuple[float, float, str, str] | None:
        _, country = region_for(tz)
        key = f"{country}|{int(settlement)}|{' '.join(place.casefold().split())}"
        if key in self._places:
            self._places.move_to_end(key)
            return self._places[key]
        # worldwide (towns only for a forecast); the watch's country wins unless its place is far less known
        # (Richmond -> London for a UK watch, but Paris stays in France and never becomes a hamlet in Yorkshire)
        params = {"q": place, "format": "jsonv2", "limit": 5, "accept-language": "en", "addressdetails": 1}
        if settlement:
            params["featureType"] = "settlement"
        async with self._geo_lock:
            wait = self._geo_last + GEOCODE_GAP_S - self.clock()
            if wait > 0:
                await asyncio.sleep(wait)
            self._geo_last = self.clock()
            n["geocode_requests"] += 1
            r = await self._client.get().get(NOMINATIM_URL, params=params)
        r.raise_for_status()
        rows = [x for x in r.json() if isinstance(x, dict) and "lat" in x and "lon" in x]
        found = None
        if rows:
            top = float(rows[0].get("importance") or 0)
            local = [x for x in rows if country and str((x.get("address") or {}).get("country_code", "")).upper() == country
                     and float(x.get("importance") or 0) >= 0.6 * top]
            row = (local or rows)[0]
            found = (round(float(row["lat"]), 4), round(float(row["lon"]), 4), str(row.get("display_name") or place)[:120],
                     str((row.get("address") or {}).get("country_code", "")).upper())
        self._places[key] = found
        if len(self._places) > GEOCODE_CACHE:
            self._places.popitem(last=False)
        return found

    async def forecast(self, lat: float, lon: float, n: Counter) -> dict:
        key = (lat, lon)
        cached = self._forecasts.get(key)
        if cached and cached[0] > self.clock():
            return cached[2]
        headers = {"If-Modified-Since": cached[1]} if cached and cached[1] else {}
        n["weather_requests"] += 1
        r = await self._client.get().get(FORECAST_URL, params={"lat": lat, "lon": lon}, headers=headers)
        if r.status_code == 304 and cached:
            data = cached[2]
        else:
            r.raise_for_status()
            data = r.json()
        expires = self.clock() + 600
        try:
            ttl = (parsedate_to_datetime(r.headers["expires"]) - datetime.now(timezone.utc)).total_seconds()
            expires = self.clock() + max(60.0, ttl)
        except (KeyError, TypeError, ValueError):
            pass
        self._forecasts[key] = (expires, r.headers.get("last-modified", cached[1] if cached else ""), data)
        if len(self._forecasts) > GEOCODE_CACHE:
            self._forecasts.pop(next(iter(self._forecasts)))
        return data

    async def sun(self, lat: float, lon: float, day: str, offset: str, n: Counter) -> tuple[str, str] | None:
        key = (lat, lon, day)
        if key not in self._sun:
            n["weather_requests"] += 1
            r = await self._client.get().get(SUN_URL, params={"lat": lat, "lon": lon, "date": day, "offset": offset})
            r.raise_for_status()
            p = r.json().get("properties") or {}
            rise, set_ = (p.get("sunrise") or {}).get("time"), (p.get("sunset") or {}).get("time")
            if len(self._sun) > GEOCODE_CACHE:
                self._sun.clear()
            self._sun[key] = (str(rise or ""), str(set_ or ""))
        return self._sun[key]

    async def evidence(self, place: str, tz: str, timeout: float, n: Counter) -> SearchResult | None:
        """The forecast for `place` as one result, or None (unknown place, any error, out of time)."""
        if not place.strip() or timeout <= 0.3:
            return None
        try:
            zone = ZoneInfo(tz)
        except Exception:  # noqa: BLE001
            zone = ZoneInfo("UTC")
        try:
            async with asyncio.timeout(timeout):
                where = await self.geocode(place, tz, n)
                if where is None:
                    return None
                lat, lon, name = where
                now = datetime.now(zone)
                offset = now.strftime("%z")
                fc, sun = await asyncio.gather(
                    self.forecast(lat, lon, n),
                    self.sun(lat, lon, now.date().isoformat(), f"{offset[:3]}:{offset[3:]}", n),
                    return_exceptions=True)
                if isinstance(fc, BaseException):
                    raise fc
        except (httpx.HTTPError, TimeoutError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as exc:
            log.info("weather source: %s", type(exc).__name__)
            n["weather_failures"] += 1
            return None
        text = summarise(fc, None if isinstance(sun, BaseException) else sun, zone, name)
        if not text:
            return None
        return SearchResult("metno", f"Weather forecast for {name} (MET Norway)", "https://api.met.no/", text[:600],
                            text=text, extraction="full")


def summarise(data: dict, sun: tuple[str, str] | None, zone: ZoneInfo, name: str) -> str:
    """Now, the rest of today every 3 hours, the next three days (min / max, mostly what, rain), sunrise / sunset,
    in the user's time zone."""
    series = (data.get("properties") or {}).get("timeseries") or []
    points = []
    for p in series:
        try:
            t = datetime.fromisoformat(str(p["time"]).replace("Z", "+00:00")).astimezone(zone)
        except (KeyError, ValueError):
            continue
        d = p.get("data") or {}
        inst = (d.get("instant") or {}).get("details") or {}
        nxt = d.get("next_1_hours") or d.get("next_6_hours") or {}
        points.append((t, inst, (nxt.get("summary") or {}).get("symbol_code", ""),
                       ((d.get("next_6_hours") or {}).get("details") or {}).get("precipitation_amount"),
                       ((d.get("next_1_hours") or {}).get("details") or {}).get("precipitation_amount")))
    if not points:
        return ""
    now = datetime.now(zone)
    current = min(points, key=lambda x: abs((x[0] - now).total_seconds()))

    def temp(v) -> str:
        return f"{round(v)}°C" if isinstance(v, (int, float)) else "?"

    t, inst, sym, _, rain1 = current
    wind = inst.get("wind_speed")
    lines = [f"Place: {name}. Now ({t:%a %d %b %H:%M}): {temp(inst.get('air_temperature'))}, {describe(sym)}"
             + (f", wind {round(wind * 3.6)} km/h ({round(wind * 2.237)} mph)" if isinstance(wind, (int, float)) else "")
             + (f", humidity {round(inst['relative_humidity'])}%" if isinstance(inst.get("relative_humidity"), (int, float)) else "")
             + (f", rain next hour {rain1} mm" if rain1 is not None else "") + "."]
    later = [x for x in points if x[0].date() == now.date() and x[0] > t and x[0].hour % 3 == 0]
    if later:
        lines.append("Later today: " + "; ".join(
            f"{x[0]:%H:%M} {temp(x[1].get('air_temperature'))} {describe(x[2])}" for x in later))
    for k in range(1, 4):
        day = now.date() + timedelta(days=k)
        pts = [x for x in points if x[0].date() == day]
        temps = [x[1]["air_temperature"] for x in pts if isinstance(x[1].get("air_temperature"), (int, float))]
        if not temps:
            continue
        syms = Counter(describe(x[2]) for x in pts if x[2] and 8 <= x[0].hour <= 20)
        rain = sum(x[3] for x in pts if isinstance(x[3], (int, float)) and x[0].astimezone(timezone.utc).hour % 6 == 0)  # MET's 6-hour totals start at 00/06/12/18 UTC
        lines.append(f"{'Tomorrow' if k == 1 else f'{day:%A}'} ({day:%a %d %b}): min {temp(min(temps))}, max {temp(max(temps))}"
                     + (f", mostly {syms.most_common(1)[0][0]}" if syms else "") + f", rain about {round(rain, 1)} mm.")
    if sun and sun[0] and sun[1]:
        try:
            rise = datetime.fromisoformat(sun[0]).astimezone(zone)
            set_ = datetime.fromisoformat(sun[1]).astimezone(zone)
            lines.append(f"Today: sunrise {rise:%H:%M}, sunset {set_:%H:%M}.")
        except ValueError:
            pass
    return "\n".join(lines)
