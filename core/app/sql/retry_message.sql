SELECT outbox_retry_message($1::bigint, $2::bigint, $3::bigint, $4::boolean) AS ok;
