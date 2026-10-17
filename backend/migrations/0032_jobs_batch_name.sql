-- Batches redesign (docs/specs/2026-10-17-batches-redesign-design.md): a batch
-- gets a human name instead of being labelled by its prompt. The name lives on
-- every job of the batch (the jobs sharing a run_group, or one job); readers
-- take the primary (lowest id) job's.
--
-- Existing batches are named after their contents with the same rule new ones
-- get by default (services/batch_names.py::default_batch_name): the first
-- clip's cached name, else the name its latest annotation recorded (clip_cache
-- only holds clips someone opened), else "Clip <id>", plus "and N more" for the
-- batch's other distinct clips. Studio jobs never show on the Batches page and stay NULL, as
-- do jobs with no items.

ALTER TABLE jobs ADD COLUMN batch_name TEXT;

CREATE TEMP TABLE _m0032 AS
WITH items AS (
  SELECT COALESCE(j.run_group, 'job:' || j.id) AS bk,
         ji.id AS item_id,
         ji.catdv_clip_id AS clip_id
  FROM job_items ji
  JOIN jobs j ON j.id = ji.job_id
  WHERE COALESCE(j.kind, '') != 'studio'
),
firsts AS (
  SELECT bk, clip_id FROM (
    SELECT bk, clip_id, ROW_NUMBER() OVER (PARTITION BY bk ORDER BY item_id) AS rn
    FROM items
  ) WHERE rn = 1
),
counts AS (
  SELECT bk, COUNT(DISTINCT clip_id) AS n FROM items GROUP BY bk
)
SELECT f.bk AS bk,
       COALESCE(
         cc.name,
         (SELECT a.catdv_clip_name FROM annotations a
          WHERE a.catdv_clip_id = f.clip_id ORDER BY a.id DESC LIMIT 1),
         'Clip ' || f.clip_id
       )
         || CASE WHEN c.n > 1 THEN ' and ' || (c.n - 1) || ' more' ELSE '' END AS name
FROM firsts f
JOIN counts c ON c.bk = f.bk
LEFT JOIN clip_cache cc
  ON cc.provider_id = 'catdv'
 AND cc.provider_clip_id = CAST(f.clip_id AS TEXT);

UPDATE jobs
SET batch_name = (
  SELECT name FROM _m0032 WHERE bk = COALESCE(jobs.run_group, 'job:' || jobs.id)
)
WHERE COALESCE(kind, '') != 'studio';

DROP TABLE _m0032;
