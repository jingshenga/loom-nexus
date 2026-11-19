SELECT id, category, source_term, target_action, factor, enabled,
       created_at, updated_at
FROM dynamic_slang_terms
ORDER BY category, source_term;
