-- run_group ties together the per-kind jobs created by a single bulk
-- "Annotate selected" action (one job per media kind) so the Batch filter
-- can present them as a single run. NULL for single-clip / studio jobs.
ALTER TABLE jobs ADD COLUMN run_group TEXT;
