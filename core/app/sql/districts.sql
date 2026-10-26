SELECT DISTINCT district FROM listings
WHERE status = 'published' AND district IS NOT NULL
ORDER BY district;
