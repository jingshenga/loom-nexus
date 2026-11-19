-- 存量库迁移：动态黑话词表 + 审核纠偏差分（全新表 + 记录函数，可重复执行）
CREATE TABLE IF NOT EXISTS dynamic_slang_terms (
    id BIGSERIAL PRIMARY KEY,
    category VARCHAR(24) NOT NULL,
    source_term VARCHAR(64) NOT NULL,
    target_action VARCHAR(64),
    factor INT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (category, source_term)
);
CREATE INDEX IF NOT EXISTS idx_dynamic_slang_enabled
    ON dynamic_slang_terms(enabled, category);

CREATE TABLE IF NOT EXISTS listing_correction_diffs (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    field_name VARCHAR(64) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    raw_snippet TEXT,
    operator BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_correction_diffs_recent
    ON listing_correction_diffs(created_at DESC, field_name);

-- 差分批量记录：diff 数组整体作为 jsonb 参数绑定，值在函数内展开
CREATE OR REPLACE FUNCTION correction_diffs_record(
    p_listing_id BIGINT,
    p_diffs JSONB,
    p_operator BIGINT
) RETURNS INT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT;
BEGIN
    WITH inserted AS (
        INSERT INTO listing_correction_diffs
            (listing_id, field_name, old_value, new_value, raw_snippet, operator)
        SELECT p_listing_id,
               left(d->>'field_name', 64),
               d->>'old_value',
               d->>'new_value',
               left(COALESCE(d->>'raw_snippet', ''), 300),
               p_operator
        FROM jsonb_array_elements(p_diffs) AS d
        RETURNING 1
    )
    SELECT count(*) INTO v_count FROM inserted;
    RETURN v_count;
END
$fn$;
