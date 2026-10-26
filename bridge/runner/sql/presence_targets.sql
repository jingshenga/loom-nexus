-- one row per distinct contact username among published listings
SELECT DISTINCT ON (l.contact_username)
       l.contact_username, l.contact_peer_id
FROM listings l
WHERE l.status = 'published'
  AND l.contact_username IS NOT NULL
ORDER BY l.contact_username, l.id DESC
LIMIT 200;
