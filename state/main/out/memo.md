# Decision memo: where to focus next quarter

**Recommendation: billing/support, led by `billing.premium_only_controls`.**

## Why this issue
`billing.premium_only_controls` (free-tier features locked behind Premium) ranks first, with complaint_count 52736 [C1] and severity_sum 149641 [C2]. Its mean severity is 2.837549 [C3] and its priority_score is 149641 [C4]. Representative reviews include `f524e032-145f-4584-9d86-cab4b1c97a7e` (shuffle) and `9857dc61-331a-4e0d-b6d7-8e2445097170` (lyrics).

## Reconciling the two views
By area total, usability is largest among actionable areas (complaint_count 88824, severity_sum 201658), ahead of billing/support (65875, 182683). The top-ranked issue, however, belongs to billing/support. Usability's total is spread over 11 issues, while billing/support's weight sits in one clear issue. Its top issue, `usability.ads_frequency_volume`, has complaint_count 36829 [C7], well below the top billing issue. We therefore prefer the concentrated, actionable issue. The `none` area (complaint_count 95528) is generic praise, criticism and unclear content, and is not actionable. That is why `other.generic_criticism` ranks second but is not recommended.

## Persistence versus spike
The issue was present every month, but the all-window total is dominated by a late surge: 2023-10 had 26233 of 67034 complaints. Before that, monthly counts were far lower (for example 575 in 2023-04 of 8777). Treat it as a persistent baseline with a recent spike, so the team should check what changed in September to November 2023 before sizing the work. First and last months are partial.

## Other priorities
- `access.login_failure` has the highest mean severity at 3.847327 [C5], and `playback.playback_interrupted` has 3.183657 [C6], but both are much lower volume. Keep a small parallel fix going.
- Ads (`usability.ads_frequency_volume`) are steady month to month. Keep them on the watchlist.

## Caveats
These are self-selected app-store reviews. Cancellation language is expressed intent, not observed churn, and we make no revenue or retention claims. 86 reviews were quarantined (73 invalid model output, 13 empty text), and none are pending. Topic agreement in the verification sample was 0.843, so labels carry some noise.

---

## Claims (checked by code against ranking.csv)

| claim | issue | metric | value |
|---|---|---|---|
| C1 | `billing.premium_only_controls` | complaint_count | 52736 |
| C2 | `billing.premium_only_controls` | severity_sum | 149641 |
| C3 | `billing.premium_only_controls` | mean_severity | 2.837549 |
| C4 | `billing.premium_only_controls` | priority_score | 149641 |
| C5 | `access.login_failure` | mean_severity | 3.847327 |
| C6 | `playback.playback_interrupted` | mean_severity | 3.183657 |
| C7 | `usability.ads_frequency_volume` | complaint_count | 36829 |

**Automated validation:** passed — all claims match the deterministic ranking, all IDs are in the evidence pack, all numbers appear in the pack.

_Model: claude-sonnet-5-5+effort-low+adaptive+memo-v2. Input: `evidence_pack.json` (bounded, code-built)._
