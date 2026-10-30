-- Idempotent production rules for the two verified Ningbo on-duty feeds.
DO $seed$
DECLARE
    v_account_id BIGINT;
    v_source_id BIGINT;
    v_rule_id BIGINT;
BEGIN
    SELECT id INTO v_account_id
    FROM bridge_accounts
    WHERE status = 'active'
    ORDER BY id
    LIMIT 1;
    IF v_account_id IS NULL THEN
        RAISE EXCEPTION 'no active bridge account available';
    END IF;

    SELECT ingest_ensure_source(
        -1002131995218, '宁波天一生水💦', 'nbtyss'
    ) INTO v_source_id;
    PERFORM ingest_assign_source(v_source_id, v_account_id);
    SELECT rule_id INTO v_rule_id FROM rule_upsert(
        '天一今日签到'::varchar, 'on_duty'::varchar,
        $json${
          "sender_username": "tyssbd_bot",
          "sender_peer_id": 8393755837,
          "match_prefix": "💖今日开课老师",
          "search_text": "今日开课老师",
          "update_mode": "edited_snapshot",
          "identity_mode": "direct_username_links",
          "profile_query_required": true,
          "exclude_usernames": ["tyssbd_bot", "nbtyss"],
          "business_cutoff_hour": 4
        }$json$::jsonb, TRUE
    );
    PERFORM rule_bind_source(v_rule_id, v_source_id, 100);

    SELECT ingest_ensure_source(
        -1002594930789, '宁波小木屋', 'xiaomuwu88'
    ) INTO v_source_id;
    PERFORM ingest_assign_source(v_source_id, v_account_id);
    SELECT rule_id INTO v_rule_id FROM rule_upsert(
        '小木屋今日签到'::varchar, 'on_duty'::varchar,
        $json${
          "sender_username": "XMWGJR_BOT",
          "sender_peer_id": 8758449377,
          "match_prefix": "🔶 在线老师，按地区展示",
          "search_text": "在线老师",
          "update_mode": "replace_snapshot",
          "identity_mode": "linked_listing_messages",
          "target_channel_username": "NBXMWBGJ",
          "profile_query_required": false,
          "exclude_usernames": ["XMWGJR_BOT", "xiaomuwu88"],
          "business_cutoff_hour": 4
        }$json$::jsonb, TRUE
    );
    PERFORM rule_bind_source(v_rule_id, v_source_id, 100);
END
$seed$;
