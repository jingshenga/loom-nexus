, listing_entries AS (
    SELECT l.id, l.source_msg_id, l.contact_username, l.nickname, l.status, l.created_at,
        COALESCE(r.source_id,j.source_id) AS source_id,
        COALESCE('person:'||person.group_id, 'tg:'||NULLIF(lower(ltrim(btrim(l.contact_username),'@')),''),
                 'raw:'||l.source_msg_id, 'listing:'||l.id) AS subject
    FROM listings l
    LEFT JOIN raw_messages r ON r.id=l.source_msg_id
    LEFT JOIN profile_collection_jobs j ON j.id=l.profile_collection_job_id
    LEFT JOIN listing_person_group_members person ON person.listing_id=l.id
    WHERE l.status IN ('pending','duplicate_pending')
      AND NOT nexus_auto_duplicate_folded(l.id)
), entries AS (
    SELECT subject,'listing' AS kind,id,source_id,contact_username,nickname AS label,status,created_at
    FROM listing_entries
    UNION ALL
    SELECT COALESCE((SELECT l.subject FROM listing_entries l WHERE l.source_msg_id=q.raw_id ORDER BY l.id LIMIT 1),
                    'tg:'||NULLIF(lower(ltrim(q.candidate_json->>'identity','@')),''),'raw:'||q.raw_id),
        'rule',q.id,q.source_id,q.candidate_json->>'identity',q.candidate_json->'fields'->>'nickname',q.reason,q.created_at
    FROM classified q WHERE q.status='open' AND q.category='review'
    UNION ALL
    SELECT COALESCE((SELECT l.subject FROM listing_entries l WHERE lower(l.contact_username)=lower(b.contact_username)
                         ORDER BY l.id LIMIT 1), 'tg:'||lower(b.contact_username)),
        'bot',b.job_id,j.source_id,b.contact_username,NULL,
        COALESCE(b.discovery_json->>'outcome',j.status),b.collected_at
    FROM bot_collection_results b JOIN profile_collection_jobs j ON j.id=b.job_id
    WHERE b.verification_status='unverified' AND (b.bot_url<>'' OR j.status='needs_review')
    UNION ALL
    SELECT COALESCE((SELECT l.subject FROM listing_entries l WHERE l.id=(j.collection_plan_json->>'listing_id')::bigint),
                    'tg:'||NULLIF(lower(j.contact_username),''),'job:'||j.id),
        'checkin',j.id,j.source_id,j.contact_username,j.contact_nickname,
        COALESCE(j.collection_plan_json#>>'{result,reason}',j.collection_plan_json->>'reason','needs_review'),j.updated_at
    FROM profile_collection_jobs j JOIN ingest_rules rule ON rule.id=j.rule_id AND rule.rule_type='checkin_reverse'
    WHERE j.status='needs_review'
), groups AS (
    SELECT e.subject, max(e.created_at) AS last_update,
           jsonb_agg(jsonb_build_object('kind',e.kind,'id',e.id,'label',NULLIF(e.label,''),'contact_username',e.contact_username,
                        'status',e.status,'source',s.title,'created_at',e.created_at)
                     ORDER BY e.kind='listing' DESC,e.created_at DESC,e.id DESC) AS items,
           (SELECT count(*) FROM listings published WHERE published.status='published'
            AND 'tg:'||lower(ltrim(btrim(published.contact_username),'@'))=e.subject) AS published_matches
    FROM entries e LEFT JOIN ingest_sources s ON s.id=e.source_id
    WHERE ($1::bigint IS NULL OR e.source_id=$1)
      AND ($2::text IS NULL OR concat_ws(' ',e.label,e.contact_username,s.title) ILIKE '%'||$2||'%')
    GROUP BY e.subject
)
SELECT *,count(*) OVER() AS total_groups FROM groups
ORDER BY last_update DESC,subject LIMIT $3::int OFFSET $4::int;
