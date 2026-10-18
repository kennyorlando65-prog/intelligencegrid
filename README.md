# IntelligenceGrid

An open-source global intelligence and situational-awareness platform. Fourteen
analysis modules over **free, key-less public sources**, built around one
unusual design commitment: **the platform reports how well it can see a
territory, not just what it found there.**

A quiet region on this dashboard is labelled as *thinly observed*, never as
*calm*. Every module response carries the health of the feeds behind it, so an
empty panel is distinguishable from a dead collector. Confidence values express
the strength of the available evidence and are never presented as certainty.

---

## Why this exists

Most OSINT aggregations share the same failure: they confuse **absence of
signal** with **absence of activity**. A country with no English-language wire
coverage is not a peaceful country — it is an unobserved one. When a dashboard
renders both as an empty map, it teaches its reader to treat blind spots as
good news, and that error compounds silently. That failure is why I built this.

I built three concrete mechanisms to address it:

1. **Coverage confidence per territory.** Every one of the ~198 territories in
   the reference table is scored on how well it is observed, from source
   diversity, volume, feed-pack diversity and recency — deliberately *not*
   from population, which would bake in a bias rather than measure one.
2. **Syndication-aware counting.** Near-identical copies of one wire story are
   grouped, so six outlets republishing a single report is one event, not six
   independent confirmations.
3. **Provenance on every signal.** Each signal carries the collection time, the
   original publication time, its evidence references and its caveats.

---

## Architecture

```
                    ┌─────────────────────────────┐
   Public sources   │  BRONZE  raw, append-only   │  immutable archive
   (no API keys) ──▶│  audit trail, replayable    │
                    └──────────────┬──────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │  SILVER  cleansed, deduped  │  quality-scored,
                    │  country-resolved, conformed│  syndication-grouped
                    └──────────────┬──────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │  GOLD   coverage, signals,  │  SLA-backed views
                    │  country stats, graph       │  evidence-linked
                    └──────────────┬──────────────┘
                                   ▼
        ┌──────────────────────────────────────────────────┐
        │  14 modules  ·  FastAPI service  ·  Next.js UI    │
        └──────────────────────────────────────────────────┘
```

| Layer | Contents | Contract |
|---|---|---|
| **Bronze** | Raw upstream payloads, never mutated | Append-only, replayable, carries `_ingested_at` and source name |
| **Silver** | Cleansed, deduplicated, country-resolved, quality-scored | Joinable across domains; schema drift alerts rather than silently corrupting |
| **Gold** | Coverage matrix, signals, country statistics, relationship graph | Business-ready, evidence-linked, carries caveats |

### Repository layout

```
intelligencegrid/
  backend/                  Python service root (Vercel service)
    main.py                 FastAPI app — entrypoint `main:app`
    ig/
      config.py             Settings and tuning knobs
      models.py             Domain models, Severity ladder, audit columns
      http.py               Politeness-aware HTTP client (retries, rate limits)
      store.py              Medallion store (bronze / silver / gold) + audit
      quality.py            Dedup, SimHash syndication, URL canonicalisation, scoring
      geo.py                Country reference table (~198 territories)
      coverage.py           Coverage-confidence scoring per territory
      pipeline.py           Collection → silver → gold orchestration
      engine/
        stats.py            Rolling z-scores, spike detection, clusters
        nlp.py              Sentiment, keywords, entities, narrative divergence
        graph.py            Relationship graph with evidence-weighted edges
        backtest.py         Precision / recall / lead time evaluation
      sources/              gdelt, rss, cisa_kev, nvd, ofac_sdn, eu_sanctions,
                            un_sanctions, opensky, ais
      modules/              The 14 product modules + dispatch registry
    tests/                  168 tests — quality, stats, coverage, graph, API
  frontend/                 Next.js dashboard (Vercel service)
    app/                    Layout, single dashboard page
    components/             UI kit, charts, map, 14 module panels
    lib/                    API client, types, formatting, hooks
  .github/workflows/ci.yml  Backend lint+test, frontend lint+typecheck+build,
                            and deployment-config validation
  vercel.json               Services routing (frontend + backend)
```

---

## The fourteen modules

| # | Module | What it answers |
|---|---|---|
| 1 | **News Intelligence** | What is being reported, by whom, and is it independent reporting or a republished wire story? |
| 2 | **Event Intelligence** | Where are geocoded events clustering, and where is activity spiking against a shifted baseline? |
| 3 | **Signal Desk** | What has the platform detected, and what is the sentiment and narrative shape by territory? |
| 4 | **ThreatPulse** | Which vulnerabilities are *known-exploited* versus merely catalogued? |
| 5 | **SanctionsGraph** | Who is listed, by which jurisdiction, and how do the entities connect? |
| 6 | **FlightWatch** | Which aircraft movements deviate from their own origin cohort's behaviour? |
| 7 | **MaritimeWatch** | Which vessels are sanctioned — and what does live tracking *not* cover? |
| 8 | **Timeline & Replay** | How did this situation develop, frame by frame? |
| 9 | **Watchlists & Digests** | What is happening in the specific territories I care about? |
| 10 | **Evidence Explorer** | What is the source and reasoning chain behind any given signal? |
| 11 | **Global Map** | Where is activity, overlaid on how well each place is observed? |
| 12 | **Entity Explorer** | How do people, organisations, territories, vessels and CVEs relate? |
| 13 | **Backtesting** | How well do these detectors actually perform, and at what operating point? |
| 14 | **Coverage Dashboard** | Which territories can the platform actually see, and which are blind spots? |

---

## Data sources

**All are free and require no API key.** No credentials are needed to run this
project; the knobs in `.env.example` are optional tuning only.

| Source | Feeds | Used for |
|---|---|---|
| **GDELT** | Static `gdeltv2` export files | Global event records with geocoding, tone and actor fields |
| **RSS packs** | Global wire (4) + Africa (10), Asia (4), LatAm (2), Middle East (2) | Regional reporting that wire services under-cover |
| **CISA KEV** | JSON catalogue | Vulnerabilities with *confirmed* in-the-wild exploitation |
| **NVD** | CVE API 2.0 | The full vulnerability catalogue |
| **OFAC SDN** | US Treasury | US sanctions listings |
| **EU FSD** | EU consolidated list | EU sanctions listings |
| **UN SC** | UN Security Council | UN sanctions listings |
| **OpenSky** | REST API | Aircraft state vectors via open ADS-B |
| **AIS** | — | *No key-less global feed exists*; the module reports this rather than approximating it |

> **On the Africa pack:** it carries ten regional outlets against four for the
> global wire. That weighting is deliberate. The default weighting of open news
> feeds toward English-language wire services systematically under-observes
> African territories, and a platform that measures its own blind spots has to
> correct for the ones it can.

### Attribution

GDELT, NVD, CISA, the US Treasury, the EU and the UN publish under their own
terms. Where a source requires attribution, it is credited here and in the UI;
the software links back to original records rather than redistributing them.

---

## Data-quality rules

These are enforced in code and covered by tests, not aspirational:

| Rule | Enforcement |
|---|---|
| Pipelines are idempotent | Re-running a collection produces the same gold output; bronze is append-only |
| Schema drift alerts, never corrupts | Unexpected fields are flagged on the record, not silently coerced |
| Nulls are handled deliberately | Every field-level rule imputes, flags or rejects — no implicit propagation into gold |
| Duplicates are collapsed honestly | Exact duplicates by canonical URL; near-identical copies grouped as syndications via SimHash |
| Gold carries quality scores | Every gold row has a confidence value and its evidence attached |
| Soft deletes and audit columns | `created_at`, `updated_at`, `deleted_at`, `source_system` on all records |
| Spike detection is look-ahead safe | The rolling baseline is shifted by one so a spike cannot inflate its own mean and hide itself |
| Evaluation is honestly negative | Backtest matching is one-to-one: firing five times around one event is one hit *plus four false positives* |
| Confidence is never certainty | Every payload carries a disclaimer; the UI labels bands ("strong", "weak") not decimals alone |
| Population is excluded from coverage scoring | Documented in the API response, because using it would permanently label small countries low-data regardless of observation |

### Notable engineering decisions

- **I use static GDELT exports over the live API.** The live API rate-limits
  hard enough to make interactive use unreliable; the `gdeltv2` export files
  are the same data without the throttling cliff.
- **I use centroid markers over a polygon choropleth.** A true choropleth needs
  several hundred kilobytes of boundary geometry from a third-party CDN. Marker
  colour and size convey the same coverage fact with no external dependency.
- **Link strength weights source diversity.** Twelve outlets mentioning a link
  once each is stronger evidence than one outlet repeating it twelve times;
  averaging them together is how a single outlet manufactures a consensus.
- **MaritimeWatch reports its limitation instead of hiding it.** There is no
  free global live AIS feed. The honest output is a module that says so, so a
  blank vessel map is never mistaken for an empty ocean.

---

## Running locally

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Populate the datasets (a few minutes on first run — it collects from every feed)
python -c "import asyncio; from ig.pipeline import Pipeline; asyncio.run(Pipeline().refresh())"

# Serve
uvicorn main:app --reload --port 8000
```

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health> — cheap, never triggers collection
- Modules: <http://localhost:8000/modules>

> `ensure_ready()` warms lazily: the first request to a data module triggers a
> collection if the store is cold. `/health` deliberately does not, so a health
> probe can never become an outage.

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```

Open <http://localhost:3000>. Each panel is linkable via the URL hash
(e.g. `#coverage`).

### Quality gates

```bash
# Backend
cd backend && ruff check . && python -m pytest -q       # 168 tests

# Frontend
cd frontend && npm run lint && npm run typecheck && npm run build
```

---

## Deployment (Vercel)

This repository ships **two services from one repository**. Both halves are
declared in the root `vercel.json`; the frontend and the Python API are built
and routed independently.

### Why the backend is not in a root `api/` directory

A top-level `api/` folder is the convention for Vercel's *zero-config* Python
runtime, and it does **not** work here. On a project whose framework preset is
Next.js, Next.js owns the entire routing table: it resolves every path, treats
`api/index.py` as an ordinary source file, never mounts it, and answers `/api/*`
with its own HTML 404 page. The Python app is never reached, and the symptom
looks like a routing problem rather than a missing deployment.

The fix is the **Services** architecture: put each half in its own directory
with its own dependencies, declare both as services, and route publicly through
rewrites.

```
backend/    →  service "backend",  framework fastapi,  entrypoint main:app
frontend/   →  service "frontend", framework nextjs
/api/*      →  rewrite to service "backend"
/*          →  rewrite to service "frontend"
```

The FastAPI app is **prefix-agnostic**: routes are declared without `/api` and
middleware strips a leading `/api`, so the same app serves both
`uvicorn main:app` locally (no prefix) and the deployed service (prefix
forwarded by the rewrite). A test asserts `/health` and `/api/health` return an
identical body, so the two can never drift apart.

### Steps

1. **Import the repository** at <https://vercel.com/new>. Leave **Root
   Directory** as the repository root — a subfolder root hides the root
   `vercel.json` and the backend service then never builds.

2. **Set Framework Preset to `Services`** (Settings → Build and Deployment).
   Leaving it on `Next.js` builds the frontend only and *silently drops the
   Python service*. This is the single most common cause of a 404 at `/api`.

3. **Clear the build cache on the first redeploy after any layout change**
   (⋯ → Redeploy → untick "Use existing Build Cache"). A cache restored across
   a directory restructure can resolve stale paths.

4. **Optionally** set `NEXT_PUBLIC_API_BASE` as an environment variable. Leave
   it unset for the default same-origin `/api/*` routing.

5. **Deploy**, then verify. The build log should show a Python service build
   step alongside the Next.js build; if it shows only Next.js, the backend was
   dropped again.

### Post-deploy verification

```bash
curl -sS -i https://<app>.vercel.app/api/health    # expect 200 + application/json
curl -sS -i https://<app>.vercel.app/api/modules   # expect 200 + application/json
curl -sS -i https://<app>.vercel.app/api/coverage  # expect 200 + application/json
```

**Read the status code *and* the content-type.** A `200` with
`application/json` means the service is live. A `404` with `text/html` means the
request fell through to Next.js and the Python service is not mounted — that is
a deployment-configuration problem, not a code problem, and the two need
opposite fixes. The frontend's error handling distinguishes them for you and
says which one it saw.

> **Not committed:** `package-lock.json`. CI therefore uses `npm install`
> rather than `npm ci`. If you add a lockfile, switch CI to `npm ci` and enable
> `cache: npm` at the same time — `npm ci` fails without a committed lockfile.

---

## Testing

168 tests, all offline. The suite seeds the pipeline directly rather than
calling upstream feeds, so it is deterministic and does not fail for reasons
unrelated to the code.

```bash
cd backend && python -m pytest -q
```

| File | Focus |
|---|---|
| `tests/test_quality.py` | Deduplication, syndication grouping, URL canonicalisation, quality scoring |
| `tests/test_stats.py` | Look-ahead safety in rolling z-scores, spike detection, clustering |
| `tests/test_coverage.py` | Coverage scoring, the 54-state Africa check, population exclusion |
| `tests/test_graph_backtest.py` | Evidence-weighted edges, one-to-one backtest matching, threshold sweeps |
| `tests/test_api.py` | All 14 modules answer; `/api/*` and `/*` return identical bodies; export; degraded-source resilience |

---

## Licence

MIT for the software (see `LICENSE`). Upstream data remains under the terms of
its respective publisher — see [Attribution](#attribution).
