SELECT outbox_finish_job($1::bigint, $2::boolean, $3::text, $4::bigint, $5::varchar) AS outcome;
