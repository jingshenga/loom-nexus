CREATE TABLE IF NOT EXISTS listing_recollect_jobs (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_listing_recollect_jobs_pending
    ON listing_recollect_jobs(status, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_recollect_jobs_active_listing
    ON listing_recollect_jobs(listing_id)
    WHERE status IN ('pending', 'processing');

CREATE OR REPLACE FUNCTION listings_batch_delete(p_ids BIGINT[])
RETURNS INT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT;
BEGIN
    DELETE FROM listings l WHERE l.id = ANY(p_ids);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    UPDATE listing_recollect_jobs
    SET status = 'failed', error = 'card deleted', updated_at = NOW()
    WHERE listing_id = ANY(p_ids)
      AND status IN ('pending', 'processing');
    RETURN v_count;
END
$fn$;
