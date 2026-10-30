UPDATE ingest_rules
SET rule_name = CASE rule_name
    WHEN '天一今日在岗' THEN '天一今日签到'
    WHEN '小木屋今日在岗' THEN '小木屋今日签到'
END
WHERE rule_type = 'on_duty'
  AND rule_name IN ('天一今日在岗', '小木屋今日在岗')
  AND NOT EXISTS (
      SELECT 1
      FROM ingest_rules existing
      WHERE existing.rule_name = CASE ingest_rules.rule_name
          WHEN '天一今日在岗' THEN '天一今日签到'
          WHEN '小木屋今日在岗' THEN '小木屋今日签到'
      END
  );
