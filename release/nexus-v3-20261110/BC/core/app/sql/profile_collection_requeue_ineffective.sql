WITH requeued AS (
    UPDATE profile_collection_jobs j
    SET status = 'pending', next_attempt_at = NULL, error = NULL, updated_at = NOW()
    WHERE j.rule_id = $1::bigint
      AND EXISTS (
          SELECT 1 FROM ingest_rules rule
          WHERE rule.id = j.rule_id AND rule.rule_type = 'profile_collection' AND rule.is_active
      )
      AND j.status IN ('failed', 'not_found', 'stale_source', 'fetched')
      AND NOT EXISTS (
          SELECT 1 FROM profile_collection_results result
          WHERE result.job_id = j.id
            AND (BTRIM(result.description) <> '' OR jsonb_array_length(result.media_json) > 0)
      )
    RETURNING j.id
)
SELECT COUNT(*)::int AS requeued FROM requeued;
