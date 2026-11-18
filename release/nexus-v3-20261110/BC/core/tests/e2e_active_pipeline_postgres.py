"""Real Active extractor SQL integration against the isolated release database."""
import asyncio
import hashlib
import json
import os
import uuid
from decimal import Decimal
from unittest import mock

import asyncpg
from app.config import get_settings
from app.extractor import pipeline
from app.extractor.pipeline import Extractor


SOURCE_ID = int(os.environ.get("NEXUS_ACTIVE_ROUTER_SOURCE_IDS", "987650001").split(",")[0])
RULE_BASE = {
    "parser_key": "ast_rules_v1",
    "gate": {},
    "extract": {},
    "ocr": {"enabled": False, "trigger_when_missing": []},
    "lifecycle_terms": ["已售"],
}


async def main():
    settings = get_settings()
    assert settings.db_host == "127.0.0.1" and settings.db_name == "nexus_collection", "isolated database required"
    assert settings.active_router_enabled and not settings.active_router_kill_switch
    assert SOURCE_ID in {int(x.strip()) for x in settings.active_router_source_ids.split(",") if x.strip()}
    pool = await asyncpg.create_pool(**settings.db_kwargs(), min_size=1, max_size=5)
    if await pool.fetchval("SELECT EXISTS(SELECT 1 FROM ingest_sources WHERE id=$1)", SOURCE_ID):
        await pool.close()
        raise RuntimeError("isolated fixture source ID is already in use")
    suffix = uuid.uuid4().hex[:10]
    peer_id = -int(uuid.uuid4().int % 8_000_000_000) - 1
    raw_ids, listing_ids, rule_ids = [], [], []
    extractor = Extractor(pool, "", False, ocr_worker=object())

    async def add_rule(name, extract, group, priority=10):
        config = {**RULE_BASE, "extract": extract}
        digest = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        rule_id = await pool.fetchval(
            "INSERT INTO ingest_rules(rule_name,rule_type,config_json,is_active) "
            "VALUES($1,'listing',$2::jsonb,TRUE) RETURNING id",
            f"active-e2e-{suffix}-{name}", json.dumps(config, ensure_ascii=False),
        )
        rule_ids.append(rule_id)
        version_id = await pool.fetchval(
            "INSERT INTO rule_instance_versions(rule_id,version,rule_type,config_json,config_hash,"
            "hash_algorithm,parser_key,snapshot_origin,history_complete,created_by,change_summary) "
            "VALUES($1,1,'listing',$2::jsonb,$3,'sha256','ast_rules_v1','create',TRUE,'e2e','synthetic') "
            "RETURNING id", rule_id, json.dumps(config, ensure_ascii=False), digest,
        )
        await pool.execute(
            "INSERT INTO source_rule_bindings(source_id,rule_id,priority,rule_version_id) VALUES($1,$2,$3,$4)",
            SOURCE_ID, rule_id, priority, version_id,
        )
        await pool.execute(
            "INSERT INTO source_rule_binding_policies(source_id,rule_id,purpose,match_group,match_mode) "
            "VALUES($1,$2,'listing',$3,'additive')", SOURCE_ID, rule_id, group,
        )
        return rule_id

    async def add_raw(text, rule_config=RULE_BASE):
        raw_id = await pool.fetchval(
            "INSERT INTO raw_messages(source_id,platform_msg_id,sender_peer_id,raw_text,media_json,posted_at,extract_state) "
            "VALUES($1,$2,$3,$4,'[]'::jsonb,NOW(),'dropped') RETURNING id",
            SOURCE_ID, int(uuid.uuid4().int % 2_000_000_000), peer_id, text,
        )
        raw_ids.append(raw_id)
        await extractor._process_one({
            "raw_id": raw_id, "source_id": SOURCE_ID, "platform_msg_id": raw_id,
            "sender_peer_id": peer_id, "grouped_id": None, "raw_text": text,
            "media_json": [], "posted_at": None, "rule_config": rule_config,
        })
        return raw_id

    try:
        await pool.execute(
            "INSERT INTO ingest_sources(id,peer_id,title,username,router_mode) "
            "VALUES($1,$2,$3,$4,'active')",
            SOURCE_ID, peer_id, f"isolated Active release {suffix}", f"activee2e{suffix}",
        )
        await pool.execute(
            "INSERT INTO active_router_guard_state(source_id,heartbeat_at,health_status,active_started_at) "
            "VALUES($1,NOW(),'healthy',NOW())", SOURCE_ID,
        )

        await add_rule("normal", {
            "username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"],
            "nickname": [r"昵称[:：]\s*([^\s]+)"],
            "price": [r"价格[:：]\s*(\d+)"],
        }, "normal")
        normal_raw = await add_raw("TG:@alphauser 昵称：小林 价格：300")
        normal = await pool.fetchrow(
            "SELECT reason,candidate_json FROM rule_active_review_queue WHERE raw_id=$1", normal_raw,
        )
        assert normal and normal["reason"] == "human_review_required"
        normal_candidate = normal["candidate_json"]
        if isinstance(normal_candidate, str):
            normal_candidate = json.loads(normal_candidate)
        assert normal_candidate["fields"]["username"] == "alphauser"
        assert normal_candidate["fields"]["nickname"] == "小林"
        assert Decimal(str(normal_candidate["fields"]["price_amount"])) == Decimal("300")
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", normal_raw)

        legacy_config = {**RULE_BASE, "extract": {
            "username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"],
            "nickname": [r"昵称[:：]\s*([^\s]+)"],
            "price": [r"价格[:：]\s*(\d+)"],
        }}
        disabled = settings.model_copy(update={"active_router_enabled": False, "active_router_kill_switch": True})
        with mock.patch.object(pipeline, "get_settings", return_value=disabled):
            denied_raw = await add_raw("TG:@denieduser 昵称：关闭保护 价格：320", legacy_config)
        denied_reason = await pool.fetchval(
            "SELECT reason FROM rule_active_review_queue WHERE raw_id=$1", denied_raw,
        )
        assert denied_reason == "active_router_disabled"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", denied_raw)

        await pool.execute("UPDATE ingest_sources SET router_mode='legacy' WHERE id=$1", SOURCE_ID)
        with mock.patch.object(pipeline, "get_settings", return_value=disabled):
            legacy_raw = await add_raw("TG:@legacyuser 昵称：Legacy路径 价格：320", legacy_config)
        legacy = await pool.fetchrow("SELECT status,contact_username FROM listings WHERE source_msg_id=$1", legacy_raw)
        assert legacy and legacy["status"] == "pending" and legacy["contact_username"] == "legacyuser"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM rule_parse_runs WHERE run_reason='active' "
                                       "AND raw_revision_id IN (SELECT id FROM raw_message_revisions WHERE raw_id=$1))", legacy_raw)
        listing_ids.extend(row["id"] for row in await pool.fetch(
            "SELECT id FROM listings WHERE source_msg_id=$1", legacy_raw,
        ))
        await pool.execute("UPDATE ingest_sources SET router_mode='active' WHERE id=$1", SOURCE_ID)

        original_listing_id = await pool.fetchval(
            "INSERT INTO listings(source_msg_id,contact_username,nickname,price_amount,status,"
            "manual_fields,published_at) VALUES($1,'alphauser','人工锁定',300,'published',"
            "'{\"nickname\":true}'::jsonb,NOW()) RETURNING id", normal_raw,
        )
        listing_ids.extend([original_listing_id])
        edited_text = "TG:@alphauser 昵称：新候选 价格：350"
        await pool.execute("UPDATE raw_messages SET raw_text=$2 WHERE id=$1", normal_raw, edited_text)
        await extractor._process_one({
            "raw_id": normal_raw, "source_id": SOURCE_ID, "platform_msg_id": normal_raw,
            "sender_peer_id": peer_id, "grouped_id": None, "raw_text": edited_text,
            "media_json": [], "posted_at": None, "rule_config": RULE_BASE,
        })
        protected = await pool.fetchrow(
            "SELECT status,contact_username,nickname,price_amount,manual_fields,published_at "
            "FROM listings WHERE id=$1", original_listing_id,
        )
        assert protected["status"] == "published" and protected["contact_username"] == "alphauser"
        assert protected["nickname"] == "人工锁定" and str(protected["price_amount"]) == "300.00"
        protected_fields = protected["manual_fields"]
        if isinstance(protected_fields, str):
            protected_fields = json.loads(protected_fields)
        assert protected_fields == {"nickname": True} and protected["published_at"] is not None

        edited_candidate = await pool.fetchrow(
            "SELECT candidate_json FROM rule_active_review_queue WHERE raw_id=$1 "
            "ORDER BY id DESC LIMIT 1", normal_raw,
        )
        edited_fields = edited_candidate["candidate_json"]
        if isinstance(edited_fields, str):
            edited_fields = json.loads(edited_fields)
        assert Decimal(str(edited_fields["fields"]["price_amount"])) == Decimal("350")

        await pool.execute("DELETE FROM source_rule_bindings WHERE source_id=$1", SOURCE_ID)
        await add_rule("merge-identity", {"username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"]}, "identity")
        await add_rule("merge-profile", {"nickname": [r"昵称[:：]\s*([^\s]+)"]}, "profile")
        merged_raw = await add_raw("TG:@mergeuser 昵称：合并候选")
        merged = await pool.fetchrow("SELECT candidate_json FROM rule_active_review_queue WHERE raw_id=$1", merged_raw)
        merged_candidate = merged["candidate_json"]
        if isinstance(merged_candidate, str):
            merged_candidate = json.loads(merged_candidate)
        assert merged_candidate["fields"]["username"] == "mergeuser"
        assert merged_candidate["fields"]["nickname"] == "合并候选"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", merged_raw)

        await pool.execute("DELETE FROM source_rule_bindings WHERE source_id=$1", SOURCE_ID)
        await add_rule("identity-a", {"username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"]}, "identity-a")
        await add_rule("identity-b", {"username": [r"备用号[:：]\s*@?([A-Za-z0-9_]{5,32})"]}, "identity-b")
        identity_raw = await add_raw("TG:@firstuser 备用号:@seconduser")
        identity_reason = await pool.fetchval("SELECT reason FROM rule_active_review_queue WHERE raw_id=$1", identity_raw)
        assert identity_reason == "rule_conflict"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", identity_raw)

        await pool.execute("DELETE FROM source_rule_bindings WHERE source_id=$1", SOURCE_ID)
        await add_rule("field-a", {"nickname": [r"主昵称[:：]\s*([^\s]+)"]}, "field-a")
        await add_rule("field-b", {"nickname": [r"别名[:：]\s*([^\s]+)"]}, "field-b")
        field_raw = await add_raw("主昵称：小青 别名：小蓝")
        field_reason = await pool.fetchval("SELECT reason FROM rule_active_review_queue WHERE raw_id=$1", field_raw)
        assert field_reason == "rule_conflict"

        await pool.execute("DELETE FROM source_rule_bindings WHERE source_id=$1", SOURCE_ID)
        await add_rule("missing-identity", {"nickname": [r"昵称[:：]\s*([^\s]+)"]}, "missing")
        missing_raw = await add_raw("昵称：无身份候选")
        missing_reason = await pool.fetchval("SELECT reason FROM rule_active_review_queue WHERE raw_id=$1", missing_raw)
        assert missing_reason == "identity_unresolved"

        await pool.execute("DELETE FROM source_rule_bindings WHERE source_id=$1", SOURCE_ID)
        await add_rule("lifecycle", {"username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"]}, "lifecycle")
        lifecycle_raw = await add_raw("TG:@lifeuser 已售")
        lifecycle = await pool.fetchrow(
            "SELECT status,review_reason,candidate_username FROM lifecycle_review_events WHERE raw_id=$1", lifecycle_raw,
        )
        assert lifecycle and lifecycle["status"] == "pending" and lifecycle["candidate_username"] == "lifeuser"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", lifecycle_raw)

        # OCR completion is handled through its own durable work queue. It may
        # enrich the open Active review item but must never re-claim a raw row.
        await pool.execute("DELETE FROM source_rule_bindings WHERE source_id=$1", SOURCE_ID)
        await add_rule("ocr-fields", {
            "username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"],
            "nickname": [r"昵称[:：]\s*([^\s]+)"],
            "price": [r"价格[:：]\s*(\d+)"],
        }, "ocr")
        await pool.execute("UPDATE app_settings SET value='1' WHERE key='autopublish_enabled'")
        ocr_media_path = f"e2e/{suffix}/card.jpg"
        ocr_detail_path = f"e2e/{suffix}/detail.jpg"
        ocr_raw = await pool.fetchval(
            "INSERT INTO raw_messages(source_id,platform_msg_id,sender_peer_id,raw_text,media_json,posted_at,extract_state) "
            "VALUES($1,$2,$3,'见图片',jsonb_build_array(jsonb_build_object('kind','photo','path',$4::text),"
            "jsonb_build_object('kind','photo','path',$5::text)),"
            "NOW(),'processing') RETURNING id",
            SOURCE_ID, int(uuid.uuid4().int % 2_000_000_000), peer_id, ocr_media_path, ocr_detail_path,
        )
        raw_ids.append(ocr_raw)
        ocr_row = await pool.fetchrow(
            "SELECT id AS raw_id,source_id,platform_msg_id,sender_peer_id,grouped_id,raw_text,media_json,posted_at,"
            "(SELECT config_json FROM ingest_rules WHERE rule_name LIKE $1 ORDER BY id DESC LIMIT 1) AS rule_config "
            "FROM raw_messages WHERE id=$2", f"active-e2e-{suffix}-ocr-fields", ocr_raw,
        )
        ocr_settings = settings.model_copy(update={"ocr_review_assist_enabled": True})
        with mock.patch.object(pipeline, "get_settings", return_value=ocr_settings):
            await extractor._process_one(ocr_row)
        ocr_bindings = [dict(item) for item in await pool.fetch(pipeline.SQL_SHADOW_BINDINGS, SOURCE_ID)]
        await extractor._schedule_ocr_recheck(ocr_row, ocr_bindings)
        assert await pool.fetchval("SELECT count(*) FROM rule_active_ocr_rechecks WHERE raw_id=$1", ocr_raw) == 1
        pending_queue = await pool.fetchrow(
            "SELECT reason FROM rule_active_review_queue WHERE raw_id=$1", ocr_raw,
        )
        assert pending_queue and pending_queue["reason"] == "no_rule_match"
        await pool.execute(
            "UPDATE listing_media_ocr SET status='done',classification='info_card',review_status='pending',"
            "raw_text='',fields_json=$3::jsonb,evidence_json=$4::jsonb,confidence=0.91,"
            "engine_version='e2e-structured-ocr' WHERE raw_id=$1 AND media_path=$2",
            ocr_raw, ocr_detail_path,
            json.dumps({"price_text": ["价格：900元"]}, ensure_ascii=False),
            json.dumps({"price_text": {"line": "价格：900元", "score": 0.9}}, ensure_ascii=False),
        )
        await pool.execute(
            "UPDATE listing_media_ocr SET status='done',classification='info_card',review_status='pending',"
            "raw_text='',fields_json=$3::jsonb,evidence_json=$4::jsonb,confidence=0.91,"
            "engine_version='e2e-structured-ocr' WHERE raw_id=$1 AND media_path=$2",
            ocr_raw, ocr_media_path,
            json.dumps({"stage_name": "阿青", "contact_handles": ["ocruser"],
                        "location_text": ["地区：宁波市海曙区"]}, ensure_ascii=False),
            json.dumps({"stage_name": {"line": "昵称：阿青", "score": 0.9}}, ensure_ascii=False),
        )
        assert await pool.fetchval("SELECT extract_state FROM raw_messages WHERE id=$1", ocr_raw) == "dropped"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='trg_active_raw_after_ocr' AND NOT tgisinternal)")
        ocr_tasks = await pool.fetch(pipeline.SQL_OCR_RECHECK_CLAIM, 10)
        ocr_task = next(task for task in ocr_tasks if task["raw_id"] == ocr_raw)
        restarted_extractor = Extractor(pool, "", False, ocr_worker=object())
        with mock.patch.object(pipeline, "get_settings", return_value=ocr_settings):
            await restarted_extractor._process_ocr_recheck(ocr_task)
        refreshed = await pool.fetchrow(
            "SELECT reason,candidate_json,status FROM rule_active_review_queue WHERE raw_id=$1", ocr_raw,
        )
        refreshed_candidate = refreshed["candidate_json"]
        if isinstance(refreshed_candidate, str):
            refreshed_candidate = json.loads(refreshed_candidate)
        assert refreshed["reason"] == "human_review_required" and refreshed["status"] == "open"
        assert refreshed_candidate["fields"]["username"] == "ocruser"
        assert refreshed_candidate["fields"]["nickname"] == "阿青"
        assert Decimal(str(refreshed_candidate["fields"]["price_amount"])) == Decimal("900")
        assert await pool.fetchval("SELECT extract_state FROM raw_messages WHERE id=$1", ocr_raw) == "dropped"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", ocr_raw)
        assert await pool.fetchval("SELECT status FROM rule_active_ocr_rechecks WHERE raw_id=$1", ocr_raw) == "done"

        # OCR failure is terminal until an explicit retry. The retry endpoint's
        # failed->pending transition reopens only this still-open review task.
        retry_path = f"e2e/{suffix}/retry.jpg"
        retry_raw = await pool.fetchval(
            "INSERT INTO raw_messages(source_id,platform_msg_id,sender_peer_id,raw_text,media_json,posted_at,extract_state) "
            "VALUES($1,$2,$3,'见图片',jsonb_build_array(jsonb_build_object('kind','photo','path',$4::text)),"
            "NOW(),'processing') RETURNING id",
            SOURCE_ID, int(uuid.uuid4().int % 2_000_000_000), peer_id, retry_path,
        )
        raw_ids.append(retry_raw)
        retry_row = dict(ocr_row)
        retry_row.update({"raw_id": retry_raw, "platform_msg_id": retry_raw,
                          "media_json": [{"kind": "photo", "path": retry_path}]})
        with mock.patch.object(pipeline, "get_settings", return_value=ocr_settings):
            await extractor._process_one(retry_row)
        await pool.execute(
            "UPDATE listing_media_ocr SET status='failed',error_text='synthetic OCR failure' "
            "WHERE raw_id=$1 AND media_path=$2", retry_raw, retry_path,
        )
        failed_task = next(task for task in await pool.fetch(pipeline.SQL_OCR_RECHECK_CLAIM, 10)
                           if task["raw_id"] == retry_raw)
        with mock.patch.object(pipeline, "get_settings", return_value=ocr_settings):
            await restarted_extractor._process_ocr_recheck(failed_task)
        assert await pool.fetchval("SELECT status FROM rule_active_ocr_rechecks WHERE raw_id=$1", retry_raw) == "failed"
        assert await pool.fetchval("SELECT extract_state FROM raw_messages WHERE id=$1", retry_raw) == "dropped"
        await pool.execute(
            "UPDATE listing_media_ocr SET status='pending',error_text=NULL WHERE raw_id=$1 AND media_path=$2",
            retry_raw, retry_path,
        )
        assert await pool.fetchval("SELECT status FROM rule_active_ocr_rechecks WHERE raw_id=$1", retry_raw) == "waiting_ocr"
        await pool.execute(
            "UPDATE listing_media_ocr SET status='done',classification='info_card',review_status='pending',"
            "raw_text='',fields_json=$3::jsonb,evidence_json='{}'::jsonb,engine_version='e2e-retry' "
            "WHERE raw_id=$1 AND media_path=$2",
            retry_raw, retry_path,
            json.dumps({"stage_name": "小青", "contact_handles": ["retryuser"],
                        "price_text": ["价格：700元"]}, ensure_ascii=False),
        )
        retried_task = next(task for task in await pool.fetch(pipeline.SQL_OCR_RECHECK_CLAIM, 10)
                            if task["raw_id"] == retry_raw)
        with mock.patch.object(pipeline, "get_settings", return_value=ocr_settings):
            await restarted_extractor._process_ocr_recheck(retried_task)
        retry_candidate = await pool.fetchrow(
            "SELECT reason,candidate_json FROM rule_active_review_queue WHERE raw_id=$1", retry_raw,
        )
        assert retry_candidate["reason"] == "human_review_required"
        assert await pool.fetchval("SELECT status FROM rule_active_ocr_rechecks WHERE raw_id=$1", retry_raw) == "done"
        assert await pool.fetchval("SELECT extract_state FROM raw_messages WHERE id=$1", retry_raw) == "dropped"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", retry_raw)

        # A manual terminal review is immutable under a later OCR retry.
        before_review = await pool.fetchval(
            "SELECT candidate_json FROM rule_active_review_queue WHERE raw_id=$1", ocr_raw,
        )
        await pool.execute("UPDATE rule_active_review_queue SET status='resolved' WHERE raw_id=$1", ocr_raw)
        await pool.execute(
            "UPDATE listing_media_ocr SET status='pending' WHERE raw_id=$1 AND media_path=$2",
            ocr_raw, ocr_media_path,
        )
        assert await pool.fetchval("SELECT status FROM rule_active_ocr_rechecks WHERE raw_id=$1", ocr_raw) == "done"
        assert await pool.fetchval(
            "SELECT candidate_json FROM rule_active_review_queue WHERE raw_id=$1", ocr_raw,
        ) == before_review

        # A task created in Active becomes stale after Legacy cutover. Even
        # with autopublish enabled it cannot enter the Legacy listing path.
        stale_path = f"e2e/{suffix}/stale.jpg"
        stale_raw = await pool.fetchval(
            "INSERT INTO raw_messages(source_id,platform_msg_id,sender_peer_id,raw_text,media_json,posted_at,extract_state) "
            "VALUES($1,$2,$3,'见图片',jsonb_build_array(jsonb_build_object('kind','photo','path',$4::text)),"
            "NOW(),'processing') RETURNING id",
            SOURCE_ID, int(uuid.uuid4().int % 2_000_000_000), peer_id, stale_path,
        )
        raw_ids.append(stale_raw)
        stale_row = dict(ocr_row)
        stale_row.update({"id": stale_raw, "raw_id": stale_raw, "platform_msg_id": stale_raw,
                          "media_json": [{"kind": "photo", "path": stale_path}]})
        with mock.patch.object(pipeline, "get_settings", return_value=ocr_settings):
            await extractor._process_one(stale_row)
        await pool.execute("UPDATE ingest_sources SET router_mode='legacy' WHERE id=$1", SOURCE_ID)
        await pool.execute(
            "UPDATE listing_media_ocr SET status='done',classification='info_card',review_status='pending',"
            "raw_text='TG:@staleuser 昵称：不应发布 价格：900元' WHERE raw_id=$1 AND media_path=$2",
            stale_raw, stale_path,
        )
        stale_task = next(task for task in await pool.fetch(pipeline.SQL_OCR_RECHECK_CLAIM, 10)
                          if task["raw_id"] == stale_raw)
        with mock.patch.object(pipeline, "get_settings", return_value=ocr_settings):
            await extractor._process_ocr_recheck(stale_task)
        assert await pool.fetchval("SELECT extract_state FROM raw_messages WHERE id=$1", stale_raw) == "dropped"
        assert await pool.fetchval("SELECT status FROM rule_active_ocr_rechecks WHERE raw_id=$1", stale_raw) == "stale"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM listings WHERE source_msg_id=$1)", stale_raw)
        await pool.execute("UPDATE ingest_sources SET router_mode='active' WHERE id=$1", SOURCE_ID)

        active_runs = await pool.fetchval("SELECT count(*) FROM rule_parse_runs WHERE run_reason='active' AND raw_revision_id IN "
                                          "(SELECT id FROM raw_message_revisions WHERE raw_id=ANY($1::bigint[]))", raw_ids)
        assert active_runs >= len(raw_ids), (active_runs, len(raw_ids))
        print("Active Core/PostgreSQL pipeline E2E: review-first candidate, kill-switch review parking, additive field merge, "
              "identity/field conflict review, missing identity review, Lifecycle review, structured OCR fusion, "
              "delayed recheck isolation, Active-to-Legacy stale-task protection, no unsafe Listing creation: OK")
    finally:
        if raw_ids:
            await pool.execute("DELETE FROM listings WHERE source_msg_id=ANY($1::bigint[])", raw_ids)
            await pool.execute("DELETE FROM raw_messages WHERE id=ANY($1::bigint[])", raw_ids)
        if listing_ids:
            await pool.execute("DELETE FROM listings WHERE id=ANY($1::bigint[])", listing_ids)
        if rule_ids:
            await pool.execute("DELETE FROM ingest_rules WHERE id=ANY($1::bigint[])", rule_ids)
        await pool.execute("DELETE FROM ingest_sources WHERE id=$1", SOURCE_ID)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
