SELECT su.chat_alias, su.chat_alias_is_custom, su.chat_alias_changed_at,
       CASE WHEN su.chat_alias_is_custom AND su.chat_alias_changed_at IS NOT NULL
            THEN su.chat_alias_changed_at + INTERVAL '6 months' END AS next_change_at
FROM site_users su WHERE su.id = $1::bigint;
