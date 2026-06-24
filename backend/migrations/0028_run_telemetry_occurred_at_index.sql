-- 0028: index run_telemetry.occurred_at.
-- The Usage & Budget feature (#30, ADR 0122) sums cost_usd over the current
-- calendar month on every full-page render (the always-present topbar spend
-- pill, via the in-memory topbar_counts refresh) and on the Usage tab. Those
-- reads filter `occurred_at >= <month start>`; without an index they full-scan
-- run_telemetry, which grows ~1 KB/run forever. This range index keeps the
-- month aggregates O(rows-in-month) instead of O(all-rows).
CREATE INDEX IF NOT EXISTS idx_run_telemetry_occurred_at
  ON run_telemetry (occurred_at);
