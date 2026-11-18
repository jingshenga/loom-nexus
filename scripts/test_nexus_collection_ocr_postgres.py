"""Run directly against the disposable nexus_collection database used by this release."""

import asyncio
import os

import asyncpg

from app.extractor.media_ocr import MediaOcrProcessor, SQL_MEDIA_FLAGS


async def main():
    conn = await asyncpg.connect(os.environ["NEXUS_COLLECTION_TEST_DSN"])
    tx = conn.transaction()
    await tx.start()
    try:
        if await conn.fetchval("SELECT current_database()") != "nexus_collection":
            raise RuntimeError("Use the disposable nexus_collection database")
        account = await conn.fetchval(
            "INSERT INTO bridge_accounts(session_file) VALUES('fixture.session') RETURNING id"
        )
        source = await conn.fetchval("INSERT INTO ingest_sources(peer_id) VALUES(-10088991003) RETURNING id")
        rule = await conn.fetchval(
            "INSERT INTO ingest_rules(rule_name,rule_type,config_json) "
            "VALUES('fixture checked collection','checkin_reverse','{}') RETURNING id"
        )
        job = await conn.fetchval(
            "INSERT INTO profile_collection_jobs(rule_id,source_id,account_id,duty_date,contact_username,collection_plan_json) "
            "VALUES($1,$2,$3,CURRENT_DATE,'fixture_ocr_contact','{\"decision\":\"supplement\"}') RETURNING id",
            rule, source, account,
        )
        result = await conn.fetchval(
            "INSERT INTO profile_collection_results(job_id,contact_username,description,media_json) "
            "VALUES($1,'fixture_ocr_contact','fixture text','[{\"kind\":\"photo\",\"path\":\"one.jpg\"},"
            "{\"kind\":\"photo\",\"path\":\"two.jpg\"},{\"kind\":\"photo\",\"path\":\"three.jpg\"}]') RETURNING id",
            job,
        )
        listing = await conn.fetchval(
            "INSERT INTO listings(profile_collection_job_id,contact_username,nickname,price_amount,description,manual_fields,status) "
            "VALUES($1,'fixture_ocr_contact','fixture manual name',100,'fixture text','{\"nickname\":false}','pending') RETURNING id",
            job,
        )
        await conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('autopublish_enabled','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )
        processor = MediaOcrProcessor(conn, "/tmp", None)
        await processor._sync_profile_listing(result)
        assert await conn.fetchval("SELECT status FROM listings WHERE id=$1", listing) == "pending"

        await conn.execute(
            "INSERT INTO listing_media_ocr(profile_result_id,media_path,status,classification,fields_json) "
            "VALUES($1,'one.jpg','done','info_card','{\"stage_name\":\"different OCR name\"}')", result,
        )
        await processor._sync_profile_listing(result)
        assert await conn.fetchval("SELECT nickname FROM listings WHERE id=$1", listing) == "fixture manual name"

        await conn.execute("UPDATE listings SET status='published' WHERE id=$1", listing)
        await conn.execute("UPDATE profile_collection_results SET description='late evidence' WHERE id=$1", result)
        await processor._sync_profile_listing(result)
        after = await conn.fetchrow("SELECT status,description,nickname FROM listings WHERE id=$1", listing)
        assert tuple(after.values()) == ("published", "fixture text", "fixture manual name"), dict(after)

        media = '[{"kind":"photo","path":"one.jpg","visibility":"public"}]'
        raw = await conn.fetchval(
            "INSERT INTO raw_messages(source_id,platform_msg_id,media_json) VALUES($1,991,$2::jsonb) RETURNING id",
            source, media,
        )
        await conn.execute("UPDATE listings SET source_msg_id=$1,media_json=$2::jsonb WHERE id=$3", raw, media, listing)
        assert await conn.fetchval(SQL_MEDIA_FLAGS, raw, "one.jpg", "admin", "done", "info_card")
        assert await conn.fetchval("SELECT media_json->0->>'visibility' FROM listings WHERE id=$1", listing) == "public"
        assert await conn.fetchval("SELECT media_json->0->>'visibility' FROM raw_messages WHERE id=$1", raw) == "admin"
        print("Collection OCR sync keeps guarded candidates in review and protects published cards")
    finally:
        await tx.rollback()
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
