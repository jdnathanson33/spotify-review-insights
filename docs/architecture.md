# Architecture

```mermaid
flowchart TD
    CSV[("Input CSV<br/>any path · 660,622 rows")] --> P1

    subgraph CODE1["Code only (no model)"]
      P1["1 · PREPARE  pipeline/ingest.py<br/>checker-identical CSV parser · row SHA-256<br/>profile · exact-text dedupe · scope · queue"]
    end
    P1 -->|"state.sqlite: reviews, texts, inputs<br/>out/ingestion_report_*.json"| ORCH

    ORCH{{"ORCHESTRATOR  pipeline/cli.py<br/>dispatch · state · validation · spend ledger (reserve worst case)<br/>shared OTPM limiter · bounded retries · SIGINT = finish in-flight + save"}}

    ORCH -->|"≤50 distinct pending texts / request"| P2
    subgraph M2["Model role: enrich  (Haiku 5.5, effort low, no thinking, enrich-v2)"]
      P2["2 · CLASSIFY<br/>topic · subtopic · intent · severity · sentiment · needs_review · quote"]
    end
    P2 --> V2["Code validation  pipeline/enrich.py<br/>every ref · allowed labels · ranges · exact quote substring<br/>entities = deterministic lexicon"]
    V2 -->|"valid"| SAVE[("results table<br/>1 transaction / batch<br/>cache key = text hash + label_config")]
    V2 -->|"invalid → ONE retry with error feedback"| P2
    V2 -->|"still invalid"| Q[("quarantine + reason")]
    P2 -.->|"429 / 5xx: backoff + jitter, max 6 attempts<br/>then leave PENDING (not completed)"| ORCH
    BATCH["Message Batches API path  pipeline/batch.py<br/>same prompt/schema/validation · 50% price<br/>invalid items → deferred parallel retry"] --> V2
    ORCH -->|"bulk of full run"| BATCH

    SAVE --> P3
    subgraph M3["Model role: verify  (Haiku 5.5, verify-v1, different rubric wording)"]
      P3["3 · VERIFY seeded 1% sample (cap 5,000)<br/>sees ONLY original text"]
    end
    P3 --> C3["Code compares fields → verify_report.json<br/>planted-error copy → evals/planted_error_test.json"]

    SAVE --> P4C["4 · GROUP (code) membership = topic.subtopic<br/>complaint + cancellation only · one issue per review"]
    P4C --> P4M
    subgraph M4["Model role: group  (Haiku 5.5, group-v1)"]
      P4M["names + summaries from ≤12 sampled quotes per issue<br/>flags misfits · cannot change membership"]
    end
    P4C -->|"membership.csv"| P5
    subgraph CODE5["Code only"]
      P5["5 · RANK  priority = Σ severity = count × mean<br/>sort −score, +issue_id · six-decimal half-up means"]
    end
    P5 -->|"ranking.csv · aggregates.csv"| PACK["Evidence pack (code): aggregates, top issues,<br/>monthly shares w/ denominators, 4 reviews/issue"]
    PACK --> P6
    subgraph M6["Model role: memo  (Sonnet 5.5, effort low, memo-v1)"]
      P6["6 · RECOMMEND structured JSON memo + claims"]
    end
    P6 --> C6["Code checks claims vs ranking, cited IDs ⊂ pack,<br/>numbers ⊂ pack · one repair attempt"]
    C6 -->|"memo.md · claims.csv"| EXP

    SAVE --> EXP["export  → grading/ (records.jsonl.gz, membership, ranking, claims, calls, checkpoints)"]
    EXP --> LOAD["dashboard/load_db.py"] --> DB[("Neon Postgres<br/>reviews · issues · monthly · metrics · recommendations · claims")]
    DB --> API["Vercel serverless API  /api/overview · ranking · recommendations · issue · review"]
    API --> UI["Deployed dashboard (public/index.html)"]
```

**Where code decides vs where a model judges.** Code: which texts are pending, batch boundaries, all counting, dedupe and cache reuse, validation of every returned field, retry/quarantine routing, spend and rate limits, membership, ranking arithmetic, evidence selection, and claim checking. Models: reading messy multilingual customer language (enrich), an independent second reading (verify), naming issues (group), and writing the argument (memo).

**Why each model call is needed.** Enrich: topic/intent/severity need language judgment across 40+ languages, slang, sarcasm — no keyword rule reaches acceptable agreement. Verify: an independent estimate of label error on a random sample. Group: human-readable issue names from bounded evidence (cosmetic; ranking never depends on it). Memo: synthesis of a business argument from aggregates — numbers are copied from the pack and checked by code.
