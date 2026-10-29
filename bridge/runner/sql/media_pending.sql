SELECT id, media_json FROM raw_messages
WHERE media_json <> '[]'::jsonb
  AND (media_json::text NOT LIKE '%"score"%'
       OR (media_json::text LIKE '%"kind": "photo"%'
           AND media_json::text NOT LIKE '%"orientation_checked": true%')
       OR (media_json::text LIKE '%"video"%' AND media_json::text NOT LIKE '%"poster"%'))
ORDER BY id DESC LIMIT 200;
