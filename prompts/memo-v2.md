You are writing a decision memo for Spotify's product leadership at the end of a historical review window (May 2022 – Nov 2023). Question: where should the next quarter of product effort go — access, usability, playback, or billing/support?

You receive ONLY a saved evidence pack computed by code: overall counts, the baseline issue ranking (priority_score = complaint_count × mean_severity = severity_sum), per-area totals, monthly complaint shares, and a few representative reviews per top issue. Rules:
- Use only numbers that appear in the pack, copied exactly. Do not compute new figures except simple restatements already given.
- Cite issues by issue_id and reviews by review_id from the pack only. Never invent IDs, counts, revenue, churn, or customer facts.
- Reviews are self-selected public app-store reviews; cancellation language is expressed intent, not observed churn. Do not claim revenue at risk or causal retention effects.
- Disclose unresolved/quarantined classifications given in the pack.
- Reconcile the two views explicitly: `area_totals` (sum over every issue mapped to an area; see `area_mapping`) and the issue-level ranking. If the area with the largest total is not the area of the top-ranked issue, say so with both numbers and justify your choice (e.g. concentration in one actionable issue vs. many diffuse issues). Treat the `none` area (generic praise/criticism, boycotts, unclear) as non-actionable and say so.
- Use `monthly_complaints_top5` (with denominators) to say whether the top issue is persistent or a spike; first/last months are partial.

Return ONLY JSON:
{
 "priority_area": "access|usability|playback|billing/support",
 "headline": "one-sentence recommendation",
 "recommendations": [{"title": "...", "detail": "...", "issue_ids": ["..."], "claim_ids": ["C1"]}],
 "alternatives": [{"area": "...", "why_not_first": "..."}],
 "limitations": ["..."],
 "claims": [{"claim_id": "C1", "issue_id": "...", "metric": "complaint_count|severity_sum|mean_severity|priority_score", "value": "<exact string from pack>"}],
 "memo_markdown": "~400-600 word memo in Markdown. Reference claims inline as [C1], issues as `issue_id`, reviews as `review_id`."
}
Every issue-level number in memo_markdown must have a claim entry and be cited inline.
