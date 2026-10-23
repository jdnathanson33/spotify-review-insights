# Decision memo: where to focus next quarter

**Recommendation: billing/support, starting with free-tier skip and playback restrictions.**

## What the evidence shows
The pack covers 100 reviews from May 2022 to Nov 2023. 99 were classified and 1 was quarantined because of invalid model output after retry. None are pending.

The top-ranked issue is `billing.premium_only_controls` (free tier skip and playback restrictions). It has 11 complaints [C1], a mean severity of 3.000000 [C3], and a priority score of 33 [C2]. Reviewers say features were made premium-only or that restrictions are excessive, for example `21915fcd-9a35-48ba-b277-6296c4154603` and `89a8e180-7846-4421-a2b6-fa7787e9d0d2`. Three of its reviews use cancellation language, which is expressed intent and not observed churn.

The next real product issue is `usability.ads_frequency_volume`. It has 7 complaints [C4] and a priority score of 15 [C5]. Reviewers describe ads every one or two songs (`3e8bdc92-4934-457e-85cc-8b9d14bf51c0`). The leading issue's score is more than double this.

## Recommended work
1. **Review the free-tier restriction experience.** Look at the messaging, discoverability and flow around skip and replay limits. Whether to change the restrictions themselves is a business decision outside this evidence.
2. **Look at ad frequency as a secondary item**, since it is the second-ranked issue.
3. **Keep a small triage lane for severe reliability issues.** `access.login_failure` [C6] and `playback.crash_or_wont_open` [C7] each have a mean severity of 4.000000, but with only 2 complaints each they do not justify leading the quarter.

## Why not the other areas first
- **Usability:** the top usability issue scores 15, well below the leader.
- **Access:** only 2 complaints in the sample.
- **Playback:** severe items, but low volume and spread across many issues.

## Caveats
- The sample is small and self-selected.
- The leading issue's complaints cluster in October 2023 (7 of 11). This may reflect a single product change and not a steady trend, so confirm with other data before committing a quarter of effort.
- Cancellation language is intent, not churn. No revenue or retention effect is claimed.
- The 'billing/support' area label here covers product restrictions, not payment or support problems.
- Spot-check verification on 20 reviews showed 0.95 agreement on intent and severity, 0.9 on topic, and 4 disagreements.

**Next step:** validate the October 2023 cluster against first-party data, then scope the restriction-experience review.

---

## Claims (checked by code against ranking.csv)

| claim | issue | metric | value |
|---|---|---|---|
| C1 | `billing.premium_only_controls` | complaint_count | 11 |
| C2 | `billing.premium_only_controls` | priority_score | 33 |
| C3 | `billing.premium_only_controls` | mean_severity | 3.000000 |
| C4 | `usability.ads_frequency_volume` | complaint_count | 7 |
| C5 | `usability.ads_frequency_volume` | priority_score | 15 |
| C6 | `access.login_failure` | mean_severity | 4.000000 |
| C7 | `playback.crash_or_wont_open` | mean_severity | 4.000000 |

**Automated validation:** passed — all claims match the deterministic ranking, all IDs are in the evidence pack, all numbers appear in the pack.

_Model: claude-sonnet-5-5+effort-low+adaptive+memo-v1. Input: `evidence_pack.json` (bounded, code-built)._
