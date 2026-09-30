"""Offline before/after cost estimate on the recorded workload (no provider calls, read-only database).

Replays the recorded real-provider turns (mock usage excluded) through the cost model of the old and the
new request pipeline:

- LLM input: per turn, rounds x prompt size. The old prompt carried the provider's hosted web-search
  tool on every request; its hidden size H is measured from the data (median observed input of
  single-round, search-free turns minus the visible prompt). The new prompt has no hidden part, a small
  web_search function instead, and a byte-stable prefix that the provider caches (cached tokens priced
  per the pricing rule, applied from the 2nd model call of a turn and to turns <5 min after the previous
  one on the same watch).
- Web search: at most one search per turn (the old path sometimes ran two), and the cache simulated on
  the recorded search turns (same kind of question, same place, within the category's lifetime).
  Searches the model would now skip are NOT counted as savings (that needs a live replay).
- STT: the part of the end-of-speech silence no longer streamed (END_SILENCE_MS - SILENCE_SEND_MS).
- TTS: unchanged (quality first).

Everything printed is an estimate from recorded usage and pricing rules, not provider invoices.

    python tools/cost_report.py [--db data/buddyai.db] [--chars-per-token 3.8]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.items import TOOL_DEFS, TOOLS_RULE  # noqa: E402
from app.pipeline.display_tag import DISPLAY_RULE  # noqa: E402
from app.pipeline.vad import END_SILENCE_MS  # noqa: E402
from app.pipeline.conversation import SILENCE_SEND_MS  # noqa: E402
from app.search import DEFAULT_TTL_S, SEARCH_RULE, SEARCH_TOOL  # noqa: E402
from app.settings_tool import SETTINGS_RULE, SETTINGS_TOOL  # noqa: E402

BASELINE_GBP = {"web search": 0.6225, "speech-to-text": 0.2547, "text-to-speech": 0.2602, "LLM": 0.156397}
OLD_WEB_SEARCH_RULE_CHARS = 537  # the rule the old pipeline appended when web search was on
SPOKEN_RULE_CHARS = 248
CACHE_WINDOW = timedelta(minutes=5)
WEATHER = re.compile(r"weather|forecast|temperat|rain|snow|wind|sunny|vreme|ploua|grade|meteo", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parents[1] / "data" / "buddyai.db"))
    ap.add_argument("--chars-per-token", type=float, default=3.8)
    ap.add_argument("--usd-gbp", type=float, default=0.75)
    args = ap.parse_args()
    cpt = args.chars_per_token
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    prices = {(p, m, u): v for p, m, u, v in db.execute("select provider, model, unit, price_usd from pricing_rules")}
    price_in = prices.get(("openai", "gpt-6-luna", "input_token"), 1e-7)
    price_cached = prices.get(("openai", "gpt-6-luna", "cached_input_token"), price_in / 10)
    price_out = prices.get(("openai", "gpt-6-luna", "output_token"), 5e-7)
    price_search = prices.get(("openai", "gpt-6-luna", "web_search_call"), 0.01)
    price_stt = prices.get(("openai_stt", "gpt-live-transcribe", "audio_second"), 0.00028333)

    turns = {}
    for tid, created, device, conv, user_text, status in db.execute(
        "select id, created_at, device_id, conversation_id, user_text, status from turns order by id"
    ):
        turns[tid] = {"created": datetime.fromisoformat(created[:19]), "device": device, "conv": conv,
                      "text": user_text or "", "status": status, "reply": ""}
    for tid, reply in db.execute("select id, assistant_text from turns"):
        turns[tid]["reply"] = reply or ""
    usage: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for tid, unit, qty in db.execute(
        "select turn_id, unit, quantity from usage_records where turn_id is not null and provider not like 'mock%'"
    ):
        usage[tid][unit] += qty

    # Visible prompt (characters) shared by every turn, old vs new
    tools_old = len(json.dumps([SETTINGS_TOOL, *TOOL_DEFS]))
    tools_new = tools_old + len(json.dumps(SEARCH_TOOL))
    base_system = 90 + 110 + SPOKEN_RULE_CHARS + len(DISPLAY_RULE) + 120  # persona, language, spoken, display, custom
    rules = len(SETTINGS_RULE) + len(TOOLS_RULE)
    static_old = base_system + 60 + rules + OLD_WEB_SEARCH_RULE_CHARS + tools_old  # the old one had the clock inside
    static_new = base_system + rules + len(SEARCH_RULE) + tools_new
    date_note = 60

    def history_chars(tid: int, trim: bool) -> int:
        t = turns[tid]
        prev = [x for i, x in turns.items() if i < tid and x["conv"] == t["conv"] and x["status"] == "completed"][-6:]
        return sum(len(p["text"]) + (min(len(p["reply"]), 302) if trim else len(p["reply"])) for p in prev)

    real = [tid for tid in usage if usage[tid].get("input_token")]
    # Hidden per-request overhead of the hosted tool: from single-round, search-free turns after it was on.
    samples = []
    for tid in real:
        u = usage[tid]
        visible = (static_old + history_chars(tid, False) + len(turns[tid]["text"])) / cpt
        if not u.get("web_search_call") and u["input_token"] > 2000 and u["input_token"] < 7500:
            samples.append(u["input_token"] - visible)
    hidden = statistics.median(samples) if samples else 0.0

    old = defaultdict(float)
    new = defaultdict(float)
    search_new = hits = 0
    last_turn_on_device: dict[str, datetime] = {}
    weather_cache: dict[str, datetime] = {}
    for tid in sorted(real):
        u, t = usage[tid], turns[tid]
        hist_old, hist_new = history_chars(tid, False), history_chars(tid, True)
        per_round_old = hidden + (static_old + hist_old + len(t["text"])) / cpt
        searches_old = u.get("web_search_call", 0)
        rounds = max(1, round((u["input_token"] - searches_old * 700) / per_round_old))
        # --- old (as recorded)
        old["llm"] += u["input_token"] * price_in + u.get("output_token", 0) * price_out
        old["search"] += searches_old * price_search
        old["stt"] += u.get("audio_second", 0) * price_stt
        # --- new
        prefix = static_new / cpt
        variable = (hist_new + date_note + len(t["text"])) / cpt
        warm = t["device"] in last_turn_on_device and t["created"] - last_turn_on_device[t["device"]] < CACHE_WINDOW
        cached = prefix * (rounds - 1) + (prefix if warm else 0)
        total_in = rounds * (prefix + variable)
        searched = min(searches_old, 1)
        if searched:
            if WEATHER.search(t["text"]):
                key = t["device"]  # same place (the watch's area) for weather questions
                if key in weather_cache and t["created"] - weather_cache[key] < timedelta(seconds=DEFAULT_TTL_S["weather"]):
                    searched, hits = 0, hits + 1
                else:
                    weather_cache[key] = t["created"]
            if searched:
                total_in += 800  # the compact search request (query + search context)
                total_in += 60 * rounds  # the short answer fed back
        search_new += searched
        new["llm"] += (total_in - cached) * price_in + cached * price_cached + (u.get("output_token", 0) + 40 * searched) * price_out
        new["search"] += searched * price_search
        saved_s = (END_SILENCE_MS["medium"] - SILENCE_SEND_MS) / 1000 if u.get("audio_second", 0) > 1.0 else 0
        new["stt"] += max(0.0, u.get("audio_second", 0) - saved_s) * price_stt
        last_turn_on_device[t["device"]] = t["created"]

    g = args.usd_gbp
    rows = [("web search", "search"), ("speech-to-text", "stt"), ("LLM", "llm")]
    print(f"Recorded real-provider turns with an LLM call: {len(real)}")
    print(f"Hidden per-request tokens of the old hosted search tool (measured): ~{hidden:,.0f}")
    print(f"Visible prompt: old ~{static_old / cpt:,.0f} tokens, new ~{static_new / cpt:,.0f} tokens (+history)")
    print(f"Searches: old {int(sum(usage[t].get('web_search_call', 0) for t in real))}, new {search_new} "
          f"(cache hits simulated: {hits}; searches the model now skips are not counted)")
    print()
    print(f"{'component':<16}{'baseline £':>12}{'replayed old £':>16}{'optimised £':>13}{'change':>9}")
    total_b = total_o = total_n = 0.0
    for label, key in rows:
        b, o, n = BASELINE_GBP[label], old[key] * g, new[key] * g
        total_b, total_o, total_n = total_b + b, total_o + o, total_n + n
        print(f"{label:<16}{b:>12.4f}{o:>16.4f}{n:>13.4f}{(n - o) / o * 100 if o else 0:>8.0f}%")
    tts = BASELINE_GBP["text-to-speech"]
    total_b, total_o, total_n = total_b + tts, total_o + tts, total_n + tts
    print(f"{'text-to-speech':<16}{tts:>12.4f}{tts:>16.4f}{tts:>13.4f}{0:>8.0f}%   (unchanged)")
    print(f"{'total':<16}{total_b:>12.4f}{total_o:>16.4f}{total_n:>13.4f}{(total_n - total_o) / total_o * 100:>8.0f}%")
    print("\nEstimates from recorded usage x pricing rules (USD->GBP %.2f), not provider invoices." % g)
    return 0


if __name__ == "__main__":
    sys.exit(main())
