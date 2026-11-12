SELECT contact_username, contact_peer_id FROM listings
WHERE id = $1::bigint AND status = 'published';
