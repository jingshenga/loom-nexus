SELECT classification, review_status, raw_text, normalized_text,
       fields_json, evidence_json, confidence,
       classifier_version_id, classifier_signals_json
FROM listing_media_ocr
WHERE file_sha256 = $1 AND status = 'done' AND raw_text <> ''
ORDER BY id DESC LIMIT 1;
