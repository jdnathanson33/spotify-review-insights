# Spotify Review Insights: a multi-agent pipeline over 660,622 reviews

**Question:** Spotify's review window runs from May 2022 to November 2023. Where should the next quarter of product effort go: access, usability, playback, or billing/support?
**Answer (memo, AI-written and code-checked):** **billing/support**, led by `billing.premium_only_controls`, the free-tier controls locked behind Premium. It has 52,736 complaint and cancellation reviews with mean severity 2.837549, for priority 149,641. That is rank 1 of 40 issues. See [MEMO.md](MEMO.md).

| | |
|---|---|
| **Live dashboard** | **https://spotify-review-insights-gamma.vercel.app**. Public, no login. Backend: Vercel serverless API → Neon Postgres. |
| Grading export | [`grading/`](grading/) (contract `a5-audit-v1`). The self-check result is in [`evals/self_check_summary.json`](evals/self_check_summary.json). |
| Cost calculator | [`cost/report.md`](cost/report.md) (measured cold/warm 100-review pilot plus projections). Offline replay: `python cost/calculator.py` |
| Architecture | [`docs/architecture.md`](docs/architecture.md) (diagram, code/model boundaries) |
| Labels | [`LABELS.md`](LABELS.md) (shared definitions, examples, 40 subtopics) and [`prompts/`](prompts/) |

## Results summary

| | measured |
|---|---|
| Source rows ingested and accounted for | **660,622 / 660,622** (profile matches the official `check_submission.py profile` exactly: 13 empty texts, 159,701 missing app versions, 0 duplicate IDs) |
| Nonempty reviews classified (`completed`) | **660,536 / 660,609 = 99.989%** |
| Quarantined | **86** = 13 `empty_review_text` + 73 `invalid_model_output_after_retry` (no exact-substring evidence quote after retry and requeue). 0 pending. |
| Exact-text cache reuse | 484,189 distinct nonempty texts were sent to the model. **176,420** rows reuse a direct `cache_source_id`. |
| Self-check (zero-API checker) | `working_coverage_point_candidate` **0.9999**. The only flag is `unfinished_classification` (the 73 quarantines above, shown on purpose). |
| Model calls | 10,457 logged attempts (deduplicated) (enrich, verify, group and memo roles). 54.1M input and 34.4M output tokens. |
| **API spend** | **$10.94** for the full run including dev checkpoints ([`cost/scaling.md`](cost/scaling.md)), plus **$0.13** for the two 100-review pilots and **<$0.01** for injection and smoke tests. The budget cap was $40, enforced in code. |
| Full-run elapsed time | About 41 minutes from the start of full-corpus enrichment to the final memo (sync interrupt/resume demo, then the Message Batches API for the bulk, then deferred retries and downstream stages) |
| Golden 50 (see the disclosure below) | topic **82%**, intent **86%**, severity exact **88%**, severity MAE **0.16**, sentiment MAE 0.126, joint 70%. 16 of 50 cases are marked ambiguous. |
| Independent verifier (seeded 1% sample, n=4,841) | topic **84.3%**, intent **91.0%**, severity **88.8%** agreement, severity MAE 0.115. 1,400 disagreement rows are saved. |
| Planted-error test | **40 / 40** deliberately wrong labels detected by the verifier comparison |
| Prompt-injection test | **7 / 7** checks passed (synthetic batch; injected text was labeled `unclear` and not obeyed) |
| Interrupt/resume (full corpus, real calls) | 134,904 → 137,856 completed IDs. **0** completed IDs re-sent. Recording: [`evals/resume_demo.cast`](evals/resume_demo.cast) |

> **Disclosure: golden labels.** At the student's request, the 50 golden reference labels in [`evals/golden_reference_labels.csv`](evals/golden_reference_labels.csv) were written by **Claude (the AI assistant that helped build this repo), not hand-labeled by the student.** Every row says so in a `labeler` column. They were written before any pipeline prediction on those 50 reviews existed and were never used as model inputs, examples, or thresholds. Because the labeler is itself an LLM, the agreement numbers above measure **agreement between two models**, not human-validated accuracy.

## Setup

```bash
git clone https://github.com/jdnathanson33/spotify-review-insights && cd spotify-review-insights
pip install -r requirements.txt          # anthropic, python-dotenv, psycopg (Python 3.11+)
cp .env.example .env                     # only needed for paid runs or DB loading; offline commands need no key
# Raw data (not committed): download the course ZIP and place spotify_reviews_18months.csv in data/
#   SHA-256 1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6 (97,400,616 bytes)
#   Source: BwandoWando, "3.4 Million Spotify Google Store Reviews" v2 (CC0), Kaggle; course extract per data/manifest.json
```

## Commands

```bash
# ---- zero-cost, no API key ----
python cost/calculator.py                                   # offline replay of the measured 100-review pilot + projections
python -m pipeline rerank --state state/main                # (needs state.sqlite) deterministic ranking from saved records
python scripts/rerank_from_grading.py                       # deterministic ranking from the COMMITTED grading/ files only
python vendor/check_submission.py profile --full data/spotify_reviews_18months.csv --out grading/ingestion.json
python vendor/check_submission.py reference --full data/spotify_reviews_18months.csv --analysis data/spotify_reviews_18months.csv --out local-reference.json
python vendor/check_submission.py check --reference local-reference.json --submission grading --out self-check.json
python evals/run_evals.py failures                          # mock-provider failure paths (transient error, malformed JSON, dropped IDs, interrupt)

# ---- paid (explicit) ----
python cost/calculator.py pilot --execute                   # real cold + warm 100-review pilot (~$0.08)
python -m pipeline run --input <ANY.csv> --state state/<name> --workers 4 --budget-usd 40   # all six stages
python -m pipeline batch-submit --input <ANY.csv> --state state/<name> --phase resume  # bulk via Message Batches API
python -m pipeline batch-collect --state state/<name> --wait
bash scripts/resume_demo.sh                                 # recorded SIGINT + resume on the full corpus
python evals/run_evals.py injection --execute               # synthetic prompt-injection batch (~$0.001)
DATABASE_URL=... python dashboard/load_db.py                # load processed outputs into Postgres (no model calls)
```

`run` accepts **any CSV path** with the six source columns. It runs ingest → enrich → verify → group → rank → memo with no chat pasting. `--stages` selects a subset, `--max-batches` bounds work, and `--phase resume` labels post-interruption calls.

## Architecture (six stages, four model roles, code in control)

See [`docs/architecture.md`](docs/architecture.md) for the full Mermaid diagram. In short:

| stage | owner | input → output | failure behaviour / stop condition |
|---|---|---|---|
| 1 Prepare | code (`pipeline/ingest.py`) | CSV → `state.sqlite` reviews/texts/scope, `ingestion_report_*.json` | strict CSV parser (same as the checker); duplicate IDs reported, never merged |
| 2 Classify | **enrich** model (Haiku 5.5, effort low, thinking off, `enrich-v2`), validated by code | ≤50 distinct pending texts → labels | every ref, label, range and exact quote is checked; **one** retry with the validation error; then quarantine with a reason. 429/5xx: exponential backoff with jitter (6 attempts), then left **pending**. Budget: worst-case reservation before dispatch. Shared output-tokens/min limiter. SIGINT finishes in-flight work, saves, and stops. |
| 3 Verify | **verify** model (Haiku 5.5, `verify-v1`, different rubric wording) | seeded 1% sample of original texts only → `verify_report.json` | code compares fields; never sees the first prediction |
| 4 Group | code membership + **group** model for names (`group-v1`) | complaint/cancellation records → `membership.csv` (`topic.subtopic`, one issue per review) + `issues.json` names | model sees ≤12 sampled quotes per issue; unknown IDs rejected; it can't change membership |
| 5 Rank | code (`pipeline/group_rank.py`) | membership + severities → `ranking.csv`, `aggregates.csv` | priority = Σ severity = count × mean; ties by issue_id; six-decimal half-up means |
| 6 Recommend | **memo** model (Sonnet 5.5, effort low, `memo-v2`) | code-built evidence pack → `memo.md`, `claims.csv` | code checks every claim against the ranking, every cited ID against the pack, and every number against the pack; one repair attempt |

**Why a model at all, and where it is not used.** Language judgment (40+ languages, slang, sarcasm, mixed reviews) needs a model. Counting, dedupe, caching, filtering, membership, ranking, arithmetic, evidence selection, budgets and validation are code. `entities` are a deterministic lexicon match in code. Short reviews (≤200 chars) and `unclear` reviews may use their full text as the evidence quote; everything else must be an exact substring, checked in code.

**Scaling path and why it changed.** 100-review pilot → 500 (2 workers) → 10K (8 workers) → full. At 10K the account's **80,000 output-tokens/minute** limit produced 429s (199 failed attempts; affected texts were left pending and re-run, never counted as completed). The pipeline then gained a shared token-bucket limiter. Because a rate-limited sync run of 475K texts would take about 8 hours, the bulk ran on the **Message Batches API**: same prompt, schema, validation and atomic saves, 50% price. Invalid batch items got the single sync retry with feedback in parallel. Fallback to Sonnet is implemented but **disabled** (fraction 0), because measured quarantines were rare (0.011%). One fallback call ran by accident in a 3-request batch test ($0.008, failed, logged); the default was fixed.

## Evidence map (rubric → artifacts)

**Deliverable quality (4)**
1. *Accessible code/setup and artifacts.* Setup and commands are above. [`requirements.txt`](requirements.txt), a blank [`.env.example`](.env.example) and [`.gitignore`](.gitignore) (`.env` was never committed). Everything is inspectable without a key.
2. *Clear architecture, shared schema, provenance.* [`docs/architecture.md`](docs/architecture.md), [`LABELS.md`](LABELS.md), [`prompts/`](prompts/), `pipeline/labels.py` (schema validation). Every record carries `label_config` = `claude-haiku-5-5+effort-low+nothink+enrich-v2+schema-v1+entities-v1` and `source_sha256`; cache reuse carries a direct `cache_source_id`. The per-run manifest is [`state/main/run_log.jsonl`](state/main/run_log.jsonl).
3. *Memo numbers linked to calculations and evidence.* [`MEMO.md`](MEMO.md) cites `[C1]…[C7]` → [`grading/claims.csv`](grading/claims.csv), and each value equals the checker's recomputation from `records` + `membership`. Representative review IDs come from [`state/main/out/evidence_pack.json`](state/main/out/evidence_pack.json).
4. *Coherent recommendation, alternatives, limitations.* The memo reconciles area totals (usability's total is larger but spread over 11 issues) with the concentrated top issue, separates the persistent baseline from the Oct-2023 spike using monthly denominators, and states limitations (self-selected reviews, intent is not churn, 86 quarantines, label noise).

**Testing & evaluation (3)**
1. *Golden 50, per-field comparison, error analysis.* [`evals/golden_results.json`](evals/golden_results.json) (agreement, MAE, per-class F1, confusion) and [`evals/golden_cases.csv`](evals/golden_cases.csv) (per-case expected vs predicted, quote check, needs_review vs ambiguity). **The labels are AI-made (see the disclosure).** The error analysis is below.
2. *Independent verification + planted-error + injection tests.* [`state/main/out/verify_report.json`](state/main/out/verify_report.json) (4,841 compared, 1,400 disagreement rows), [`evals/planted_error_test.json`](evals/planted_error_test.json) (40/40), [`evals/injection_test.json`](evals/injection_test.json) (7/7), [`evals/failure_path_tests.json`](evals/failure_path_tests.json) (mock: transient errors, malformed JSON, dropped IDs, interrupt).
3. *Real cold/warm pilot, offline calculator, retry/spend/recovery controls.* [`cost/report.md`](cost/report.md): the cold run took 63.1 s and cost $0.079157 with 100/100 completed; the warm run made **0 new calls** and finished in 1.96 s. Files: [`cost/pilot_calls.jsonl`](cost/pilot_calls.jsonl), [`cost/pilot_records.jsonl`](cost/pilot_records.jsonl), [`cost/rates.csv`](cost/rates.csv) (dated, editable; includes the batch tier), [`cost/usage.csv`](cost/usage.csv). Controls: the spend ledger (`pipeline/llm.py: Ledger`, worst-case reservation), the limiter (`RateLimiter`), bounded backoff, `--max-batches`, SIGINT-safe stop, atomic per-batch saves. The superseded first pilot (prompt v1, 1 quarantine that motivated v2) is kept in [`cost/archive_pilot_v1/`](cost/archive_pilot_v1/).

**Working result (3)**
1. *Full ingestion, coverage, classification.* [`grading/ingestion.json`](grading/ingestion.json) (official helper) and [`state/main/out/`](state/main/out/) `ingestion_report_1fc85de68a30.json`. Coverage: 660,622 accounted for, 660,536 completed, 86 quarantined.
2. *Runnable staged program, bounded calls, saved handoffs, demonstrated resume.* `pipeline/` (≤50 reviews per enrich request, validated by the checker's `unbounded_batch` test). Handoffs are the `state.sqlite` tables plus `state/main/out/*`. Resume evidence: [`grading/checkpoint_before.json`](grading/checkpoint_before.json), [`grading/checkpoint_after.json`](grading/checkpoint_after.json), [`evals/resume_check.json`](evals/resume_check.json), and the recording [`evals/resume_demo.cast`](evals/resume_demo.cast) (play with `asciinema play`, or read [`evals/resume_demo.log`](evals/resume_demo.log)).
3. *Reproducible ranking + deployed dashboard with grounded AI recommendations.* `python scripts/rerank_from_grading.py` reproduces [`grading/ranking.csv`](grading/ranking.csv) byte-for-byte with no model call. Dashboard: **https://spotify-review-insights-gamma.vercel.app** ([`dashboard/`](dashboard/)). Static page → `/api/*` serverless functions → Neon Postgres (`dashboard/schema.sql`, loaded by `dashboard/load_db.py` from committed artifacts). It shows overall metrics, area priority, the issue ranking, a monthly trend, the AI recommendation with clickable claim chips and validation status, per-issue evidence reviews, and a review-ID trace box.

## One review, end to end

| step | evidence |
|---|---|
| Source | `0bef0423-0dbc-42f1-ad91-c72f51baf987` (2023-10-25, 1★): "Very poor don't use this app asking premium. Basic listening are came into the premium package. Wink music app is more better than spotify". `source_sha256 a81dc7e4…48f9` |
| Enrichment | request `msg_011CgJHJNrtedXquwjH82QBg` (Batches API, ≤50 reviews): `billing / premium_only_controls / cancellation / severity 3 / sentiment −0.9`, quote = full text (≤200 chars), entities `["premium"]` |
| Verification | in the seeded sample; verifier request `msg_011CgJK8QrehJQbdQopd55W8` → `billing / complaint / 3`. **Topic and severity agree; intent disagrees.** "Don't use this app" plus a competitor mention reads as advice to others rather than a stated personal departure, so the verifier's `complaint` is arguably right. This is a known enrich tendency, analyzed below. |
| Membership | `grading/membership.csv`: `billing.premium_only_controls,0bef0423-…` (cancellation and complaint both count in the baseline, so the ranking is unaffected by this disagreement) |
| Ranking | contributes severity 3 to `billing.premium_only_controls`: 52,736 members, Σ 149,641, mean 2.837549, rank 1 |
| Memo | claims C1–C4 cite this issue's metrics; the dashboard trace box shows this record live from Postgres |

**Failed / ambiguous case.** `a7ef9b36-7b42-4a2b-9cfa-681e0fbcce60` ("I like the app considering I got 400+ songs all though with out premium you cannot pick your own songs…", 268 chars). The model's evidence quote wasn't an exact substring. The single retry told it the exact error and still failed, and a later requeue cycle failed again. Recorded handling: `status: quarantined`, `reason: invalid_model_output_after_retry:quote_not_exact_substring`. It's counted as **not** classified and is excluded from aggregates. 73 such records remain (0.011%).

## Error analysis (golden 50 and verifier)

- **Topic `other` vs. a specific topic** is the largest confusion source. Short generic reviews ("Spotify is awesome and very diverse") can be general praise (`other`) or catalog praise. 16 of 50 golden cases carry a documented alternative label.
- **Cancellation vs. complaint.** Enrich over-reads "don't use this app" and "forget it" as personal departure. Both intents enter the ranking, so the baseline is unchanged, but the cancellation counts shown on the dashboard are an upper bound.
- **Free-tier restrictions without the word "Premium"** ("can't rewind", "limited functionality") split between `billing` and `usability`/`playback`. The contract assigns *explicitly* premium-only controls to billing, and we follow that literally.
- **Severity** errors are mostly off by one (MAE 0.16 golden, 0.115 verifier), at the 2/3 boundary (annoyance vs. restricted function).
- **Political/boycott content** (the July 2023 Sweden boycott) is labeled `unclear` unless a product complaint or personal departure appears. It lands in the non-actionable `other.boycott_political` bucket.
- `needs_review` is a weak predictor of human-marked ambiguity. Per-case true/false positives are in `golden_results.json`.
- No prompt was tuned on golden results. Prompt changes v1→v2 (unclear reviews may quote the full text) were driven by the cost pilot and dev data only.

## Ranking rules, scope and limitations

Baseline: completed `complaint` + `cancellation` records, each in exactly one issue (`allow_multi_issue: false`); `priority = severity_sum`. **Area view** (memo/dashboard): access; usability (+catalog); playback (+downloads); billing/support. `other.*` issues are shown but treated as non-actionable. Monthly shares on the dashboard divide by all complaints that month; May 2022 and Nov 2023 are partial. The data is self-selected public reviews with no revenue, plan tier or confirmed churn. Cancellation language is intent, not churn, and nothing here estimates revenue at risk. Review volume spikes in 2023-07 (Sweden boycott) and 2023-10 (free-tier changes) dominate totals. 73 invalid-output quarantines are excluded. One provider result was logged twice when the collector was restarted; the export and cost recompute count it once.

## Repository map

```
pipeline/   orchestrator + stages (ingest, enrich, batch, verify, group_rank, memo, export, llm, state)
prompts/    enrich-v1 (superseded) · enrich-v2 · verify-v1 · group-v1 · memo-v1 (superseded) · memo-v2
grading/    standardized export for check_submission.py
cost/       calculator, measured pilot, rates, usage, report, scaling ladder
evals/      golden reference labels + results, planted, injection, failure paths, resume check + recording
state/main/out/  saved stage outputs of the final full run; run_log.jsonl; calls.jsonl.gz (raw internal call log)
dashboard/  schema.sql, load_db.py, export_for_db.py, data/, api/*.js, public/index.html
data/       dev checkpoints, golden template, manifest (full CSV not committed)
vendor/     course-provided checker, contract and calculator spec (unmodified)
```
