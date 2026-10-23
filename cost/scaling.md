# Scaling ladder — measured usage and cost per run (recomputed offline)

| run_id | enrich texts sent | enrich / verify / group / memo calls | failed attempts | input | cache write | cache read | output | API $ | $ per 1k texts | wall s |
|---|---|---|---|---|---|---|---|---|---|---|
| `scale-500-w2` | 479 | 14 / 1 / 5 / 1 | 0 | 45,340 | 0 | 42,544 | 37,494 | 0.0604 | 0.1260 | 85.884 |
| `scale-500-requeue-quarantine` | 1 | 2 / 0 / 0 / 0 | 0 | 279 | 0 | 5,894 | 129 | 0.0002 | 0.1513 | 3.164 |
| `scale-10k-w8` | 7,719 | 396 / 4 / 7 / 2 | 199 | 421,174 | 1,286 | 583,147 | 555,175 | 0.4037 | 0.0523 | 431.163 |
| `scale-10k-finish-pending` | 250 | 6 / 0 / 0 / 0 | 0 | 11,504 | 0 | 17,682 | 17,634 | 0.0101 | 0.0406 | 24.514 |
| `scale-10k-requeue` | 20 | 2 / 0 / 0 / 0 | 0 | 1,525 | 0 | 5,894 | 1,642 | 0.0010 | 0.0516 | 7.948 |
| `golden50-enrich` | 44 | 2 / 0 / 0 / 0 | 0 | 2,152 | 0 | 5,894 | 3,175 | 0.0019 | 0.0423 | 12.164 |
| `full-initial-sync` | 1,650 | 41 / 0 / 0 / 0 | 0 | 75,232 | 0 | 120,827 | 114,008 | 0.0657 | 0.0398 | 169.739 |
| `full-resume-sync` | 1,200 | 28 / 0 / 0 / 0 | 0 | 52,640 | 0 | 82,516 | 79,445 | 0.0458 | 0.0382 | 104.383 |
| `batch-test-collect` | 151 | 5 / 0 / 0 / 0 | 0 | 7,570 | 11,789 | 2,947 | 9,935 | 0.0118 | 0.0784 |  |
| `full-batch-collect` | 6,200 | 160 / 0 / 0 / 0 | 0 | 297,372 | 38,311 | 433,209 | 432,694 | 0.1323 | 0.0213 |  |
| `full-batch-collect-2` | 466,497 | 9557 / 0 / 0 / 0 | 0 | 23,109,724 | 2,935,212 | 25,229,267 | 32,934,209 | 9.9411 | 0.0213 |  |
| `full-final-stages` | 597 | 23 / 0 / 0 / 0 | 0 | 81,575 | 0 | 67,781 | 53,076 | 0.0354 | 0.0593 |  |
| `full-final-stages-2` | 0 | 0 / 191 / 7 / 1 | 0 | 272,665 | 3,239 | 121,737 | 155,847 | 0.1443 | 0.0000 | 329.295 |
| `full-memo-v2` | 0 | 0 / 0 / 0 / 2 | 0 | 18,799 | 834 | 834 | 4,618 | 0.0859 | 0.0000 | 119.694 |

**Total API spend in the main state: $10.9396** (pilot state reported separately in `cost/report.md`).

Notes: `full-batch-*` runs used the Message Batches API (rates key `claude-haiku-5-5@batch`, 50% off). 429 failures in `scale-10k-w8` came from the account's 80,000 output-tokens/minute limit; they were retried with backoff or left pending and re-run, never counted as completed. After that run the pipeline gained a shared token-bucket limiter (`--otpm`).
