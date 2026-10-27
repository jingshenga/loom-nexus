UPDATE duty_collection_jobs j
SET status = 'cancelled', error = 'removed from current on-duty lists', updated_at = NOW()
WHERE j.duty_date = $1::date
  AND j.rule_id = $2::bigint
  AND j.status = 'pending'
  AND NOT EXISTS (
      SELECT 1 FROM duty_snapshots ds
      CROSS JOIN LATERAL jsonb_array_elements_text(ds.contact_usernames) u(username)
      WHERE ds.duty_date = j.duty_date
        AND lower(u.username) = lower(j.contact_username)
  );
