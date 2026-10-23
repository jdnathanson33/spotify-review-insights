-- Processed review data, aggregates and analysis outputs served by the dashboard backend.
DROP TABLE IF EXISTS reviews, issues, monthly, metrics, recommendations, claims CASCADE;
CREATE TABLE reviews (
  review_id text PRIMARY KEY, review_ts text, rating text, likes int, app_version text,
  status text NOT NULL, reason text, topic text, subtopic text, intent text, severity int, sentiment real,
  needs_review boolean, evidence_quote text, entities text[], label_config text, cache_source_id text, issue_id text);
CREATE INDEX reviews_issue ON reviews(issue_id, likes DESC);
CREATE TABLE issues (
  issue_id text PRIMARY KEY, rank int, name text, summary text, area text, topic text,
  complaint_count int, severity_sum int, mean_severity text, priority_score int,
  cancellation_count int, severity_4plus int, mean_sentiment real);
CREATE TABLE monthly (issue_id text, month text, issue_complaints int, all_complaints int, PRIMARY KEY(issue_id, month));
CREATE TABLE metrics (key text PRIMARY KEY, value jsonb);
CREATE TABLE recommendations (id int PRIMARY KEY, priority_area text, headline text, body jsonb, memo_markdown text, validation_errors jsonb, model text, created_at timestamptz DEFAULT now());
CREATE TABLE claims (claim_id text PRIMARY KEY, issue_id text, metric text, value text);
