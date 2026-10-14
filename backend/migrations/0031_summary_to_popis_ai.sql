-- #123 / ADR 0127: the AI summary moves off the HUMAN field
-- pragafilm.popis.materialu (append) onto the AI-owned pragafilm.popis.ai
-- (replace). For every production prompt version that still targets
-- popis.materialu this does what "Create new version" + "Promote" does in the
-- UI: clone it as the next version with the target_map rewritten, archive the
-- old production version, promote the clone. Prompts that don't target
-- popis.materialu are untouched; re-running finds nothing to do.

CREATE TEMP TABLE _m0031 AS
SELECT v.id AS old_id,
       v.prompt_id,
       (SELECT MAX(version_num) FROM prompt_versions WHERE prompt_id = v.prompt_id) + 1
         AS new_num
FROM prompt_versions v
WHERE v.state = 'production'
  AND EXISTS (
    SELECT 1 FROM json_each(v.target_map) je
    WHERE je.type = 'object'
      AND json_extract(je.value, '$.target') = 'pragafilm.popis.materialu'
  );

INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map,
                            output_schema, model, media_resolution, created_at, updated_at)
SELECT v.prompt_id, m.new_num, 'draft', v.body,
       (SELECT json_group_object(
                 je.key,
                 CASE
                   WHEN je.type = 'object'
                    AND json_extract(je.value, '$.target') = 'pragafilm.popis.materialu'
                     THEN json_set(je.value, '$.target', 'pragafilm.popis.ai',
                                   '$.mode', 'replace')
                   WHEN je.type IN ('object', 'array') THEN json(je.value)
                   ELSE je.value
                 END)
          FROM json_each(v.target_map) je),
       v.output_schema, v.model, v.media_resolution,
       strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
       strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
FROM prompt_versions v
JOIN _m0031 m ON m.old_id = v.id;

-- One production row per prompt (idx_one_prod_per_prompt): archive first.
UPDATE prompt_versions
SET state = 'archived', updated_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
WHERE id IN (SELECT old_id FROM _m0031);

UPDATE prompt_versions
SET state = 'production', updated_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
WHERE EXISTS (
  SELECT 1 FROM _m0031 m
  WHERE m.prompt_id = prompt_versions.prompt_id AND m.new_num = prompt_versions.version_num
);

DROP TABLE _m0031;
