# Web search (ddgs_brave): status and TODO

Branch `feature/ddgs-brave-search` (based on master `95e0707`). Not merged, not deployed. The default stays
`BUDDYAI_SEARCH_BACKEND=legacy_openai`, so production behaves as before.

## Where we are (benchmark 30 October 2026)

`python tools/search_bench.py --legacy --brave-only 5` on the dev laptop (home connection). Full results:
`server/out/search_bench_20261030_1726.json` (gitignored, local only).

| | legacy_openai | ddgs_brave | Target |
|---|---|---|---|
| Answered | 28 / 30 | 14 / 30 | ≥ 95% correct |
| p50 | 4.4 s | 9.1 s | |
| p95 | 7.9 s | 12.4 s | ≤ legacy + 10% (≈ 8.7 s) |
| Cost / question | $0.0130 | $0.0012 | ≥ 70% cheaper |

Brave only (5 questions): 3/5 answered, p50 6.5 s, $0.0071 per question. The Brave key works.

Verdict: the cost target is met (−91%), but answers and latency are not. The new backend is not to be switched on
in production yet.

Where it time goes (one search, measured): DDGS ~2 s · page download 1–4.6 s (3 pages of 700–860 KB in parallel
share the bandwidth) · Trafilatura 0.5–2 s · answering call ~1.5 s · a second round +3–5 s.

## Questions that failed (new backend said NOT_FOUND, old one answered)

| # | Category | Query | Old answer (reference) | Likely cause |
|---|---|---|---|---|
| 1 | weather | weather Chelmsford today | mostly clear, ~12°C | numbers only inside heavy pages; downloads timed out |
| 2 | weather | weather Bucharest today | cloudy, 12°C, showers later | same |
| 3 | news | UK latest news headlines today | headlines of 30 Oct | news front pages are lists / JS; snippets are generic |
| 4 | news | Romania latest news today | budget deficit 3.81% … | same |
| 5 | sport | Premier League table leader | Man City 21 pts, level with Arsenal | table pages are JS/tables; snippets have no table |
| 6 | sport | Arsenal latest match result | 1–1 v Fleetwood, 27 Oct | result pages slow / snippet without score |
| 7 | sport | Romania national football team latest match result | lost 0–1 to Sweden, 5 Oct | same |
| 8 | sport | Formula 1 next race | Mexico City GP, 1 Nov | calendar pages JS-heavy |
| 9 | transport | Elizabeth line status now | Good service (TfL) | TfL status is rendered by JavaScript |
| 10 | business | Tesco Extra Chelmsford opening hours | 6am–midnight Mon–Fri … | store pages block bots / JS; snippet has no hours |
| 11 | business | Chelmsford Central Library opening hours | Mon–Wed, Fri 9:00–17:30 … | page not read in time |
| 12 | business | IKEA Lakeside opening hours | Mon–Fri 10–21 … | JS-rendered store page |
| 13 | other | iPhone 17 price UK | from £899 | Apple page JS; snippets vary |
| 14 | other | gold price per ounce today | ~$3,902 / oz | live price widgets (JS) |

Both backends failed: "latest SpaceX launch", "Greater Anglia trains Chelmsford to London disruption today".

Answers to double-check (the two backends disagree):
- Bank of England decision date: 16 Sep (new) vs 17 Sep (old). One is wrong.
- Bucharest population: 2.1 m "2026 estimate" (new) vs 1.72 m, 2021 census (old). The new figure may be metro area.
- Chelmsford population: 188,803 (2024, ONS) vs 181,500 (2021 census): both plausible, different years.

## TODO

Re-run the benchmark after each step and add the results to this file.

### A. Speed (small or medium effort, safe)

- [ ] **A1. Download only the start of a page.** At most 400 KB is parsed anyway, but up to 1.5 MB is downloaded.
  Stop reading at ~400 KB and parse what arrived, instead of refusing large pages (`app/websearch/fetch.py`,
  `extraction_max_bytes`). Expected gain: ~1–2 s.
- [ ] **A2. Don't wait for the slowest page.** Use `asyncio.as_completed` in `orchestrator._loop`. Ask the
  answering model as soon as the first page with text arrives, and let the others continue only if that is
  NOT_FOUND. Expected gain: ~1–2 s.
- [ ] **A3. Answer from snippets in parallel with the downloads.** For news, opening hours and facts, try a snippet-only
  answer (~1 s) while the pages download, and take whichever is good first. Snippet-only answers for
  time-sensitive categories stay uncached, as now. Expected gain: up to 3–4 s on those questions.
- [ ] **A4. "Let me check" on the watch right away.** A prompt rule makes the model say a short line before calling
  `web_search`, so the user hears something in ~1.5 s. It doesn't speed up the search but changes how it feels most,
  and helps legacy too. Needs a test that the text before the tool call is spoken once.
- [ ] **A5. Reuse connections.** Keep one `httpx.AsyncClient` per process for Brave and the page fetcher (keep-alive),
  while still pinning every request to the vetted IP. Expected gain: ~0.2–0.5 s.
- [ ] **A6. Smaller answering call.** Lower `max_tokens` (300 → ~150) and keep reasoning effort at the minimum for
  the answering model.

### B. Answer rate (the bigger problem)

- [ ] **B1. A dedicated weather source.** Open-Meteo (free, no key, ~0.2 s) for current conditions and the forecast,
  plus sunrise and sunset. Geocode the location with Open-Meteo's geocoding API. Fixes #1, #2, and probably makes
  weather faster than legacy. Needs an owner decision (a new external source).
- [ ] **B2. Let Brave in earlier for categories DDGS handles badly** (sport results, business hours, live status),
  e.g. after the first DDGS round without page text. Brave costs $0.005 per request and the monthly budget cap stays.
  Measure the answer-rate gain against the extra Brave spend.
- [ ] **B3. Brave `extra_snippets`.** Brave can return extra snippets per result (plan-dependent; check the plan).
  More text without downloading pages could fix hours, results and prices (#6, #7, #10–#13).
- [ ] **B4. Prefer pages that render without JavaScript.** Down-rank known JS-only domains (tfl.gov.uk status,
  accuweather, apple.com store) and up-rank text-friendly ones (bbc.co.uk/sport, wikipedia, the council's own site)
  in `evidence.py`.
- [ ] **B5. Better query wordings per category** in `query_variants`. Sport: "<team> result <opponent>", "<team> fixtures".
  Business: "<name> <town> opening times". News: add "BBC" or "Reuters". Then re-check #3–#8, #10–#12.
- [ ] **B6. Live data sources where an official free API exists.** TfL Unified API for line status (#9, free key),
  maybe others later. Each one is a separate owner decision.
- [ ] **B7. Look into why DDGS sometimes returns 0 results** on the first request after a pause (seen in testing;
  now retried once). Check whether a different DDGS backend (`backend=` parameter) is more stable.

### C. Checks and clean-up

- [ ] **C1.** Resolve the BoE date and the Bucharest population discrepancies above (which source was wrong, and does
  the evidence ranking need a fix?).
- [ ] **C2.** One run of `tests/test_websearch.py` failed once and could not be reproduced in 21 runs since. Watch
  for it. The suspects are the timing-based tests (concurrency, cancellation, the budget threads on SQLite).
- [ ] **C3.** The answering call may finish up to ~2 s after the 12 s deadline (it gets at least 2 s). Decide
  whether to keep that or reserve more time before it.
- [ ] **C4.** Benchmark again from the test server (datacenter bandwidth), not only the laptop: page downloads should
  be much faster there.
- [ ] **C5.** Verify the Brave price ($5 / 1k requests) and whether failed requests are billed, in the Brave
  dashboard. Update `pricing_defaults` if needed.
- [ ] **C6.** Robots/terms: the fetcher identifies itself (`olaCompanionBot`), rate-limits per site and respects
  401/403/429, but does not read robots.txt. Decide whether it should (+1 request per new site, cacheable).

### D. Decision point (after A + B)

Acceptance targets: ≥ 95% correct on the 30-question set, p95 ≤ legacy + 10%, ≥ 70% cheaper. If they are still
not met, consider a **hybrid**: legacy_openai for weather, sport, transport and live news, and ddgs_brave for
facts, prices and businesses where it already matches legacy. That still saves most of the cost on those
categories.

## How to resume

```
git switch feature/ddgs-brave-search
cd server
.venv\Scripts\python -m pytest -q tests/test_websearch.py      # 57 tests, mocked
.venv\Scripts\python tools/search_bench.py --legacy --brave-only 5   # real, ~10 min, ~$0.45
```

The Brave key is in `server/.env` (`BUDDYAI_BRAVE_API_KEY`). To try the backend locally, set
`BUDDYAI_SEARCH_BACKEND=ddgs_brave` and restart the server. To roll back, remove that line or set it to
`legacy_openai`.
