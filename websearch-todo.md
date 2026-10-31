# Web search (ddgs_brave): status and TODO

Branch `feature/ddgs-brave-search` (based on master `95e0707`). Not merged, not deployed. The default stays
`BUDDYAI_SEARCH_BACKEND=legacy_openai`, so production behaves as before.

## Where we are (benchmark 31 October 2026, 12:51: after A7, B8 and A2 / B11 / B12)

`python tools/search_bench.py` on the dev laptop (home connection). Full results:
`server/out/search_bench_20261031_0950.json` (with legacy) and `server/out/search_bench_20261031_1209.json`
(after A7 + B8) and `server/out/search_bench_20261031_1251.json` (after the page changes). Legacy was not
re-run; its 09:50 figures are used. All gitignored, local only.

| | legacy_openai | ddgs_brave 30 Oct | 31 Oct 09:50 | 31 Oct 12:09 | 31 Oct 12:51 | Target |
|---|---|---|---|---|---|---|
| Answered | 29 / 30 | 14 / 30 | 27 / 30 (25 correct) | 26 / 30 (26 correct) | 26 / 30 (26 correct) | ≥ 95% correct |
| p50 | 4.0 s | 9.1 s | 2.5 s | 3.0 s | **2.4 s** | |
| p95 | 6.3 s | 12.4 s | 9.5 s | 7.3 s | **6.3 s** (max 6.75 s) | ≤ legacy + 10% (≈ 7.0 s) |
| Cost / question | $0.0124 | $0.0012 | $0.0035 (−71%) | $0.0037 (−70%) | $0.0040 (−68%) | ≥ 70% cheaper |

12:51 unanswered: UK headlines and Romania news (news pages time out; each answered in some earlier runs, so
flaky), Greater Anglia (transport: goes to hosted search with the hybrid), Tesco Extra. The cost is a little
higher because more questions reached Brave (38 search requests vs 33); it varies run to run. A 12:48 run with
page replacements not bounded by one page timeout was worse (24 / 30, slower "other" questions): that is why
replacements share the round's 2.5 s.

12:09 vs 09:50: iPhone now £899 and Bucharest the 2021 census (B8); no question waits more than 7.5 s (A7).
"Romania latest news today" became NOT_FOUND (09:50 it answered at 9.2 s; on a 10:30 check it was NOT_FOUND
after 11.4 s: the news pages time out, so this one is flaky either way).

Per search on 31 Oct: 20 Brave requests (3 of them structured sport data), 14 DDGS, 8 MET Norway, 4 Nominatim,
40 answering calls for 30 questions.

Verdict: every answer given was correct, p50 beats legacy, p95 is about at the target, and cost is −70%.
Still short: 4 questions unanswered (2 live train status, 1 store, Romania news), so ≈ 87% vs legacy's ≈ 97%.
Not to be switched on in production yet.

## What changed on 31 October (and why)

Research findings (measured from the dev laptop):

- **DDGS was the main cause of the low answer rate.** ddgs 9.16 `backend="auto"` always asks Wikipedia and
  Grokipedia first, then waits out slow engines: 3.4 s p50, and 19/30 queries got any results at all. Google, Bing
  and Yandex are disabled in the library. The scraped brave, mojeek and startpage engines mostly return nothing
  (blocked). `duckduckgo,yahoo` gave 18/30 at 1.5 s. DDGS cannot reach 95% on its own, even for "height of Mount
  Everest".
- **Brave Search plan** (verified on brave.com/search/api): $5 / 1,000 requests, **$5 free credit every month**
  (≈ 1,000 free requests), 50 req/s, and it includes extra snippets. Brave answers in ~0.7 s. Its extra snippets
  alone held the answer for ~10 of the 14 questions that failed on 30 Oct.
- **Brave rich data** (`enable_rich_callback=1` + `/res/v1/web/rich`): API-Sports standings, team games incl. cup
  games, and the F1 calendar. It takes 1.5–2 s. It sometimes returns the wrong subject (Premier League games for
  "Romania national team"); the relevance filter drops that.
- **Weather:** the free Open-Meteo API is non-commercial only. **MET Norway** (api.met.no) is free for commercial
  use under CC BY 4.0, and needs a User-Agent and caching. Geocoding uses **Nominatim** (OpenStreetMap, ODbL, at most
  1 req/s, cached).
- **TfL Unified API:** free with a key, 500 req/min, attribution "Powered by TfL Open Data". Commercial use is not
  stated explicitly.

Implemented:

- **Weather first from MET Norway** (`app/websearch/weather.py`, setting `weather_source=metno`). Nominatim
  searches settlements worldwide; the watch's country wins unless its town is far less known (Richmond → London,
  Paris stays in France). The forecast is cached until `Expires`, with sunrise / sunset. On any error, normal
  search runs. All 5 weather questions were answered in 0.9–2.6 s.
- **Brave first** for sport, news, transport and business (`BRAVE_FIRST`), then DDGS for further wordings.
  Brave again only when time runs short.
- **Brave `extra_snippets`**, plus local results (address, opening hours, phone) and infobox as results.
- **Snippet-first answering** after a Brave round; pages are downloaded only on NOT_FOUND.
- **Rich sport data** for `sport` (counted as a Brave request, inside `brave_max_requests_per_search`).
- **DDGS engines named** (`ddgs_backends="duckduckgo,yahoo"`) plus `timelimit` for live categories. Sport
  freshness is a month (a national team plays about monthly).
- A1 pages read up to `extraction_max_bytes` (now 800 KB) and truncated instead of refused · A5 a keep-alive client
  for Brave, MET and Nominatim · A6 answering `max_tokens` 300 → 200 · B5 "opening times" and "BBC" wordings.
- Costs: new zero-price units `metno:weather:request` and `nominatim:geocode:request`.

## Still wrong or missing (31 Oct)

| # | Query | ddgs_brave | Legacy | Likely cause / next step |
|---|---|---|---|---|
| 1 | Elizabeth line status now | NOT_FOUND | NOT_FOUND | live status is JavaScript → B6 (TfL API) |
| 2 | Greater Anglia trains Chelmsford to London disruption today | NOT_FOUND | delays 7–15 min | live status pages → National Rail Darwin / B6 |
| 3 | Tesco Extra Chelmsford opening hours | NOT_FOUND | Princes Road, 6am–midnight… | Brave's local result was a Tesco *Express*; store page needs JS |
| 4 | Romania latest news today | NOT_FOUND (12:09) | EU energy prices item | news pages time out (libertatea, aljazeera, rotimes); A2 / B4 |

Fixed at 12:09 by B8: iPhone 17 (was "from £29.96 / month", now £899), Bucharest population (was the 2.12 m
estimate, now 1.7 m, 2021).

IKEA Lakeside: the answer says sources disagree on Saturday hours (the legacy answer differs on weekday hours too).
Bank of England: both backends now say 17 September (C1, first half resolved).

## TODO

Re-run the benchmark after each step and add the results to this file.

### A. Speed

- [x] A1. Read only the start of a page (truncate at `extraction_max_bytes`, 800 KB).
- [x] A2. Don't wait for the slowest page (`orchestrator._read`): once one page has text, the others get
  `PAGE_GRACE_S` (0.5 s) more, then are cancelled (`pages_cut` in the log line).
- [x] B11. Failed pages are replaced by the next relevant result, all within one page timeout per round (2.5 s),
  so only fast failures (403, our own per-site rate limit, errors) get a replacement.
- [x] B12. Sites to avoid (`ContentExtractor.skip`): 401 / 403 / 429 / 451 → skipped for 6 h; two full-length
  timeouts in a row → 15 min (a timeout from our own short budget does not count). Their snippets still count.
  In-process memory (lost on restart, 2,000 sites at most).
- [~] A3. Snippet answer first (done for Brave rounds, sequential). Running it in parallel with the downloads is
  still open.
- [ ] **A4. "Let me check" on the watch right away** (prompt rule + a test that it is spoken once).
- [x] A5. Keep-alive clients for Brave, MET Norway and Nominatim. The page fetcher stays per request (pinned IPs).
- [x] A6. Answering `max_tokens` 300 → 200 (reasoning effort is already "none").
- [x] A7. Give-up time per search (`search_give_up_s`, 6 s): no new search round starts and no page download is
  waited for after it; the answering call may still use the rest of the turn deadline. p95 9.5 s → 7.3 s.
  Measured cause: the slow questions were not slow searches but page downloads timing out (2.5 s per round, all
  pages failing). Tune with `BUDDYAI_SEARCH_GIVE_UP_S` (0 = off).

### B. Answer rate

- [x] B1. Weather from MET Norway + Nominatim. **Follow-up:** attribution "Weather data: MET Norway (CC BY 4.0)"
  and "Geocoding © OpenStreetMap contributors" on the site's credits / legal page, before production.
- [x] B2. Brave first for sport, news, transport and business (replaced "Brave earlier").
- [x] B3. Brave `extra_snippets` + local results + infobox + rich sport data.
- [ ] **B4. Prefer pages that render without JavaScript** (down-rank tfl.gov.uk status, store locators).
- [~] B5. Wordings: business "opening times", news "BBC" done. Sport "<team> result" still open.
- [x] B6a. **Hybrid** (`search_legacy_categories`, default `transport`): with ddgs_brave, the transport category
  still goes to OpenAI hosted search (all countries: trains, flights, metro), billed as before
  (`web_search_call`). Legacy answered Greater Anglia; neither backend answered the Elizabeth line.
  `BUDDYAI_SEARCH_LEGACY_CATEGORIES=` (empty) sends transport to ddgs_brave too.
- [x] B10. **A place abroad** (`weather.abroad`, Nominatim without the town-only filter, cached, 1 s budget): no
  country preference for Brave (`country`) and DDGS (`wt-wt`) when the tool's location is in another country than
  the watch. Checked live: Bucharest, Romania, Paris, France, Otopeni, Gare du Nord → abroad; Chelmsford,
  Richmond, Heathrow, London → local. Off when `weather_source` is off. The first lookup of a new place adds up
  to ~1 s (then cached).
- [ ] **B6. Live transport data** (owner decision, UK only; flights abroad would need a paid API): TfL Unified API (free key, 500/min, attribution) for
  #1, and National Rail Darwin for #2.
- [x] B7. DDGS `auto` was the cause (see above): engines are now named.
- [x] B8. Answer rules (`EVIDENCE_RULES`, ddgs_brave only): full price, not an instalment; official / census
  figure with its year for population and statistics.
- [ ] **B9. Store-specific hours:** when Brave's local result names a different store (Express vs Extra), the model
  should say so or search again with the store's street.

### C. Checks and clean-up

- [x] C1. BoE date: both say 17 Sep. Bucharest population: 2021 census after B8.
- [ ] C2. Watch for the flaky test (timing-based tests); 78 tests now.
- [ ] C3. The answering call may finish up to ~2 s after the deadline.
- [ ] C4. Benchmark from the test server (datacenter bandwidth).
- [ ] **C5.** In the Brave dashboard, check that `/web/rich` is billed as one request ($0.005), and whether failed
  requests are billed. The response headers show a monthly limit of "0", which probably means pay-as-you-go.
- [ ] C6. robots.txt for the page fetcher (decision).

### D. Decision point

Targets: ≥ 95% correct, p95 ≤ legacy + 10%, ≥ 70% cheaper. On 31 Oct the cost target is met and p50 beats legacy.
After A7, B8 and the page changes: p95 6.3 s (target met), p50 2.4 s, every answer correct, 26/30 answered
(transport now goes to hosted search, so the gap that remains is news pages and one store). Remaining gap: live transport
(B6, now on hosted search via the hybrid) and slow news / store pages (A2, B4, B9). A **hybrid** remains the fallback:
legacy_openai for transport only (live status), ddgs_brave for everything else.

## How to resume

```
git switch feature/ddgs-brave-search
cd server
.venv\Scripts\python -m pytest -q tests/test_websearch.py      # 78 tests, mocked
.venv\Scripts\python tools/search_bench.py --legacy             # real, ~10 min, ~$0.37 legacy + ~$0.11 ddgs_brave
```

The Brave key is in `server/.env` (`BUDDYAI_BRAVE_API_KEY`). To try the backend locally, set
`BUDDYAI_SEARCH_BACKEND=ddgs_brave` and restart the server. To roll back, remove that line or set it to
`legacy_openai`. `BUDDYAI_WEATHER_SOURCE=` (empty) switches the MET Norway weather source off;
`BUDDYAI_DDGS_BACKENDS=auto` restores the old DDGS behaviour.
