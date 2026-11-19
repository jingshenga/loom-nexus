-- Compatibility endpoint for active legacy identity holds only.
SELECT COALESCE('username:' || NULLIF(lower(l.contact_username), ''), 'peer:' || l.contact_peer_id::text) AS identity_key,
       l.id, l.status, l.contact_peer_id, l.contact_username, l.nickname,
       l.created_at, COALESCE(l.source_posted_at, rm.posted_at) AS posted_at, s.title AS source_title,
       member.group_id AS person_group_id
FROM listings l
LEFT JOIN raw_messages rm ON rm.id=l.source_msg_id
LEFT JOIN ingest_sources s ON s.id=rm.source_id
LEFT JOIN listing_person_group_members member ON member.listing_id=l.id
WHERE l.status IN ('published','pending','duplicate_pending')
  AND EXISTS (
      SELECT 1 FROM listings other
      LEFT JOIN listing_person_group_members other_member ON other_member.listing_id=other.id
      WHERE other.id<>l.id
        AND other.status IN ('published','pending','duplicate_pending')
        AND (l.status='duplicate_pending' OR other.status='duplicate_pending')
        AND listing_same_account(l.contact_peer_id, l.contact_username,
                                 other.contact_peer_id, other.contact_username)
        AND (member.group_id IS NULL OR other_member.group_id IS DISTINCT FROM member.group_id)
  )
ORDER BY identity_key, posted_at DESC NULLS LAST, l.created_at DESC, l.id DESC;
