BEGIN;

CREATE OR REPLACE FUNCTION nexus_tg_username(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT CASE WHEN cleaned ~ '^[a-z][a-z0-9_]{3,30}$' AND cleaned !~ 'bot$' THEN cleaned END
    FROM (SELECT regexp_replace(lower(btrim(value)), '^(https?://(www\.)?(t\.me|telegram\.me)/|@)|/$', '', 'g') AS cleaned) s
$$;

ALTER TABLE listing_person_groups ADD COLUMN IF NOT EXISTS primary_listing_id bigint REFERENCES listings(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS nexus_auto_person_decisions (
    id bigserial PRIMARY KEY,
    group_id bigint NOT NULL,
    primary_listing_id bigint NOT NULL,
    member_ids bigint[] NOT NULL,
    rule_version integer NOT NULL DEFAULT 1,
    evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    undone_at timestamptz,
    undone_by bigint
);
CREATE INDEX IF NOT EXISTS nexus_auto_person_decisions_group ON nexus_auto_person_decisions(group_id,id DESC);

ALTER TABLE listing_media_ocr ADD COLUMN IF NOT EXISTS asset_sha256 text;
ALTER TABLE listing_media_ocr ADD COLUMN IF NOT EXISTS auto_checked_at timestamptz;
CREATE INDEX IF NOT EXISTS nexus_media_ocr_asset_sha256 ON listing_media_ocr(asset_sha256) WHERE asset_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS nexus_media_ocr_auto_pending ON listing_media_ocr(id)
    WHERE status='done' AND review_status='pending' AND auto_checked_at IS NULL;

CREATE TABLE IF NOT EXISTS nexus_auto_ocr_decisions (
    id bigserial PRIMARY KEY,
    ocr_id bigint NOT NULL REFERENCES listing_media_ocr(id) ON DELETE CASCADE,
    category text NOT NULL,
    previous_category text,
    reason text NOT NULL,
    model_sha text,
    score numeric,
    asset_sha256 text,
    rule_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    undone_at timestamptz,
    undone_by bigint
);
CREATE UNIQUE INDEX IF NOT EXISTS nexus_auto_ocr_decisions_current ON nexus_auto_ocr_decisions(ocr_id) WHERE undone_at IS NULL;

-- The application worker consumes these switches. Missing settings use the release defaults.
CREATE OR REPLACE FUNCTION nexus_auto_duplicate_folded(card_id bigint) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM listing_person_group_members m
        JOIN listing_person_groups g ON g.id=m.group_id
        JOIN listings primary_card ON primary_card.id=g.primary_listing_id
        JOIN listing_person_group_members primary_member
          ON primary_member.listing_id=primary_card.id AND primary_member.group_id=g.id
        WHERE m.listing_id=card_id AND m.listing_id<>g.primary_listing_id
          AND primary_card.status IN ('pending','duplicate_pending','published')
          AND EXISTS (SELECT 1 FROM nexus_auto_person_decisions a WHERE a.group_id=g.id AND a.undone_at IS NULL)
    )
$$;

CREATE OR REPLACE FUNCTION nexus_auto_duplicate_grouped(card_id bigint) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM listing_person_group_members m
        JOIN listing_person_groups g ON g.id=m.group_id
        WHERE m.listing_id=card_id AND g.primary_listing_id IS NOT NULL)
$$;

COMMIT;
