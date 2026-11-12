SELECT COALESCE('username:' || NULLIF(lower(l.contact_username), ''), 'peer:' || l.contact_peer_id::text) AS identity_key,
       l.id, l.status, l.contact_peer_id, l.contact_username, l.nickname,
       l.created_at, rm.posted_at, s.title AS source_title,
       pgm.group_id AS person_group_id
FROM listings l
LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id
LEFT JOIN ingest_sources s ON s.id=rm.source_id
LEFT JOIN listing_person_group_members pgm ON pgm.listing_id=l.id
WHERE l.status IN ('published','pending','duplicate_pending','archived')
  AND (l.contact_peer_id IS NOT NULL OR NULLIF(l.contact_username,'') IS NOT NULL)
  AND EXISTS (
      SELECT 1 FROM listings other
      LEFT JOIN listing_person_group_members other_group ON other_group.listing_id=other.id
      WHERE other.id<>l.id
        AND other.status IN ('published','pending','duplicate_pending','archived')
        AND COALESCE('username:' || NULLIF(lower(other.contact_username), ''),
                     'peer:' || other.contact_peer_id::text)
            = COALESCE('username:' || NULLIF(lower(l.contact_username), ''),
                       'peer:' || l.contact_peer_id::text)
        AND COALESCE('g:' || other_group.group_id::text,'l:' || other.id::text)
            <> COALESCE('g:' || pgm.group_id::text,'l:' || l.id::text)
  )
ORDER BY identity_key, rm.posted_at DESC NULLS LAST, l.created_at DESC, l.id DESC;
