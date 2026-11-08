CREATE TABLE IF NOT EXISTS listing_person_groups (
    id BIGSERIAL PRIMARY KEY,
    group_key TEXT UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS listing_person_group_members (
    group_id BIGINT NOT NULL REFERENCES listing_person_groups(id) ON DELETE CASCADE,
    listing_id BIGINT NOT NULL UNIQUE REFERENCES listings(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_listing_person_group_members_group
    ON listing_person_group_members(group_id, listing_id);

-- Seed the two clusters the administrator explicitly identified as the same people.
INSERT INTO listing_person_groups (group_key, display_name)
VALUES
    ('confirmed-jennie-kim', '金珍妮'),
    ('confirmed-qianqian', '倩倩')
ON CONFLICT (group_key) DO UPDATE
SET display_name = EXCLUDED.display_name;

INSERT INTO listing_person_group_members (group_id, listing_id)
SELECT g.id, l.id
FROM (VALUES
    ('confirmed-jennie-kim', 745::bigint),
    ('confirmed-jennie-kim', 1636::bigint),
    ('confirmed-jennie-kim', 1637::bigint),
    ('confirmed-qianqian', 749::bigint),
    ('confirmed-qianqian', 1083::bigint),
    ('confirmed-qianqian', 1632::bigint),
    ('confirmed-qianqian', 1635::bigint)
) AS seed(group_key, listing_id)
JOIN listing_person_groups g ON g.group_key = seed.group_key
JOIN listings l ON l.id = seed.listing_id
ON CONFLICT (listing_id) DO UPDATE SET group_id = EXCLUDED.group_id;
