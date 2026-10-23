# Shared labels, examples and subtopics

Primary labels are the course's common labels (GRADING_CONTRACT.md); subtopics are our optional extension. The exact model-facing wording is in `prompts/enrich-v2.md`.

## Topics

| topic | definition | product area |
|---|---|---|
| `access` | Login, signup, password or account access | access |
| `usability` | Navigation, controls, layout, queue/playlist management, ad interruptions | usability |
| `playback` | Playback failure, crashes, lag, connection failures, audio quality, resource use | playback |
| `downloads` | Downloading, saved music, offline listening, disappearing downloads | playback |
| `catalog` | Missing songs/artists, search/discovery, recommendations, lyrics availability | usability |
| `billing` | Price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls go here | billing/support |
| `support` | Contacting support and the support response | billing/support |
| `other` | General praise/criticism, unrelated content, or no supported specific topic | none |

Rules: highest-severity problem wins; tie → first mentioned; praise → first specific praised feature (general praise = `other`); a paid-plan mention alone is not billing; ads are usability unless the complaint is about price/paywall.

## Intent (precedence order)

1. `cancellation` — explicitly leaving, uninstalling, cancelling, switching, or threatening to
2. `complaint` — any negative experience, including mixed praise/criticism ("bad app" counts)
3. `request` — desired change with no reported failure
4. `praise`
5. `unclear` — meaningless/unrelated text; bare boycott slogans without a product complaint or personal departure

## Severity

| level | meaning | our worked examples |
|---|---|---|
| 1 | no reported problem: praise, unclear, pure request | "Amazing"; "please add a sleep timer"; "Boycott!" |
| 2 | dislike, generic criticism, annoyance, cosmetic | "Worst app"; "too many ads"; loud ads |
| 3 | degraded or restricted function, partial use remains | skip limits / can't choose songs on free tier; music stops intermittently; lyrics missing on some songs |
| 4 | clearly blocked core task | can't log in; app won't open; downloads won't play offline |
| 5 | explicit serious financial, privacy or data harm | charged twice / after cancelling; hacked account; lost library |

Tone, stars and cancellation intent never raise severity. A complaint that states any problem is at least 2.

## Other fields
- `sentiment` −1…1 (one decimal), evaluated with MAE and a ±0.3 tolerance
- `evidence_quote` exact source substring (code-checked). Reviews ≤200 chars, or `unclear` reviews, may use the full text.
- `entities` deterministic lexicon matches in code (`pipeline/entities.py`), never model-generated
- `needs_review` model flag for genuine ambiguity (sarcasm, partial language understanding, missing context)

## Subtopics (issue = `topic.subtopic`)

| issue_id | rubric |
|---|---|
| `access.login_failure` | cannot log in / logged out and cannot get back in / password not accepted |
| `access.account_lost_or_hacked` | account taken over, lost, merged, deleted, or data on it lost |
| `access.signup_verification` | cannot sign up, verification code/email/phone problems |
| `access.other` | other access problem |
| `usability.ads_frequency_volume` | too many / too long / too loud / unskippable ads |
| `usability.ui_redesign_navigation` | layout, home screen, navigation, design changes, hard to find things |
| `usability.playlist_queue_management` | queue, playlist, liked songs, shuffle behaviour, unwanted songs inserted into playlists (not paywall) |
| `usability.controls_settings` | buttons, gestures, settings, widgets/notification controls behaving badly |
| `usability.other` | other usability problem |
| `playback.crash_or_wont_open` | app crashes, freezes, will not open, stuck on loading screen |
| `playback.playback_interrupted` | music stops, pauses, skips, won't play, cuts out |
| `playback.connectivity_errors` | 'no internet'/'offline' errors while online, wifi/data connection failures |
| `playback.audio_quality` | sound quality, volume, equalizer, audio glitches |
| `playback.device_integration` | bluetooth, car, Android Auto, assistant, smart speakers, Chromecast, watch |
| `playback.performance_resource` | lag, slowness, battery drain, storage or data use |
| `playback.other` | other playback problem |
| `downloads.offline_playback_failure` | downloaded music will not play offline / needs internet |
| `downloads.downloads_disappearing` | downloads deleted or must be re-downloaded |
| `downloads.download_process` | downloading stuck, slow, fails, limits |
| `downloads.other` | other downloads problem |
| `catalog.missing_content` | songs/artists/albums missing, greyed out or removed |
| `catalog.search_discovery` | search results, finding music |
| `catalog.recommendations_quality` | quality/relevance of recommendations, mixes, algorithm |
| `catalog.lyrics_availability` | lyrics missing, not loading or out of sync (not a paywall complaint) |
| `catalog.podcasts_audiobooks` | podcast/audiobook content |
| `catalog.other` | other catalog problem |
| `billing.premium_only_controls` | free tier restrictions: skip limits, cannot choose/replay/rewind/seek songs, forced shuffle, lyrics or other features moved behind Premium |
| `billing.price_too_high` | price, price increases, value for money |
| `billing.unexpected_charges_refunds` | charged unexpectedly, after cancelling, double charged, refunds |
| `billing.subscription_entitlement` | premium/family/student plan not activating, lost benefits, plan management |
| `billing.payment_methods` | payment method, card, gift card problems |
| `billing.other` | other billing problem |
| `support.unresponsive_unhelpful` | support unreachable, slow or unhelpful |
| `support.other` | other support problem |
| `other.generic_criticism` | bad/worst app, generic dislike with no specific defect |
| `other.update_regression_generic` | 'new update is bad' with no specific feature named |
| `other.boycott_political` | political, religious or boycott content |
| `other.praise_general` | general praise with no specific feature |
| `other.unclear_unrelated` | meaningless, unrelated or uninterpretable text |
| `other.other` | anything else |
