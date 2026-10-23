# Decision memo: next-quarter product focus

**Recommendation: billing/support.** Focus on `billing.premium_only_controls`.

## Why
This issue ranks first, with 9 complaints [C1], mean severity 3.000000 [C3] and a priority score of 27 [C2]. Reviewers say basic player features were removed or restricted: `7084ced1-0428-4093-9328-442f74719b4f` ("they removed basic features of music player") and `89a8e180-7846-4421-a2b6-fa7787e9d0d2` ("Too much restrictions"). Two of the 9 include cancellation language, which is expressed intent and not observed churn.

## Parallel small fixes
`access.login_failure` (priority score 8 [C4], mean severity 4.000000 [C6]) and `playback.crash_or_wont_open` (priority score 8 [C5]) are few in number but severe. Examples: `32fb340f-4f9f-4551-8dae-755221e58d6a` (cannot get into account) and `55cc3abf-c295-4591-8c3a-78085cfdefaa` (app crashes after update). They are good candidates for a low-cost fix alongside the main work.

## Alternatives considered
- **Usability:** `usability.ads_frequency_volume` has 7 complaints [C7] spread over many months; worth monitoring but lower severity.
- **Access / playback:** severe but low volume.

## Caveats
- The sample is 100 self-selected public reviews, so this is directional only.
- Most of the top issue's complaints fall in 2023-10, so it may reflect one update event rather than a sustained trend; verify whether it persists.
- The second-ranked item, `other.generic_criticism`, is non-specific and not actionable.
- A 20-review verification showed 0.9 agreement, with 5 disagreements.
- No reviews were quarantined or left unresolved.
- We make no revenue or retention claims.

---

## Claims (checked by code against ranking.csv)

| claim | issue | metric | value |
|---|---|---|---|
| C1 | `billing.premium_only_controls` | complaint_count | 9 |
| C2 | `billing.premium_only_controls` | priority_score | 27 |
| C3 | `billing.premium_only_controls` | mean_severity | 3.000000 |
| C4 | `access.login_failure` | priority_score | 8 |
| C5 | `playback.crash_or_wont_open` | priority_score | 8 |
| C6 | `access.login_failure` | mean_severity | 4.000000 |
| C7 | `usability.ads_frequency_volume` | complaint_count | 7 |

**Automated validation:** passed — all claims match the deterministic ranking, all IDs are in the evidence pack, all numbers appear in the pack.

_Model: claude-sonnet-5-5+effort-low+adaptive+memo-v1. Input: `evidence_pack.json` (bounded, code-built)._
