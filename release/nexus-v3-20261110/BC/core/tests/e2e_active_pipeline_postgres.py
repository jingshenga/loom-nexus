"""Real Active extractor SQL integration against the isolated release database."""
import asyncio
import hashlib
import json
import os
import uuid
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
    assert settings.active_router_enabled and not settings.active_router_kill_switch
    assert SOURCE_ID in {int(x.strip()) for x in settings.active_router_source_ids.split(",") if x.strip()}
    pool = await asyncpg.create_pool(**settings.db_kwargs(), min_size=1, max_size=5)
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
            "VALUES($1,$2,$3,$4,'active') ON CONFLICT(id) DO UPDATE SET "
            "peer_id=EXCLUDED.peer_id,title=EXCLUDED.title,username=EXCLUDED.username,router_mode='active'",
            SOURCE_ID, peer_id, f"isolated Active release {suffix}", f"activee2e{suffix}",
        )

        await add_rule("normal", {
            "username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"],
            "nickname": [r"昵称[:：]\s*([^\s]+)"],
            "price": [r"价格[:：]\s*(\d+)"],
        }, "normal")
        normal_raw = await add_raw("TG:@alphauser 昵称：小林 价格：300")
        normal = await pool.fetchrow("SELECT status,contact_username,nickname,price_amount FROM listings WHERE source_msg_id=$1", normal_raw)
        assert normal and normal["status"] == "pending" and normal["contact_username"] == "alphauser"
        assert normal["nickname"] == "小林" and str(normal["price_amount"]) == "300.00"
        listing_ids.extend(await pool.fetch("SELECT id FROM listings WHERE source_msg_id=$1", normal_raw))

        legacy_config = {**RULE_BASE, "extract": {
            "username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"],
            "nickname": [r"昵称[:：]\s*([^\s]+)"],
            "price": [r"价格[:：]\s*(\d+)"],
        }}
        disabled = settings.model_copy(update={"active_router_enabled": False, "active_router_kill_switch": True})
        with mock.patch.object(pipeline, "get_settings", return_value=disabled):
            legacy_raw = await add_raw("TG:@legacyuser 昵称：Legacy路径 价格：320", legacy_config)
        legacy = await pool.fetchrow("SELECT status,contact_username FROM listings WHERE source_msg_id=$1", legacy_raw)
        assert legacy and legacy["status"] == "pending" and legacy["contact_username"] == "legacyuser"
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM rule_parse_runs WHERE run_reason='active' "
                                       "AND raw_revision_id IN (SELECT id FROM raw_message_revisions WHERE raw_id=$1))", legacy_raw)
        listing_ids.extend(await pool.fetch("SELECT id FROM listings WHERE source_msg_id=$1", legacy_raw))

        await pool.execute(
            "UPDATE listings SET status='published',nickname='人工锁定',manual_fields=$2::jsonb,"
            "published_at=NOW() WHERE source_msg_id=$1", normal_raw, '{"nickname":true}',
        )
        edited_text = "TG:@alphauser 昵称：新候选 价格：350"
        await pool.execute("UPDATE raw_messages SET raw_text=$2 WHERE id=$1", normal_raw, edited_text)
        await extractor._process_one({
            "raw_id": normal_raw, "source_id": SOURCE_ID, "platform_msg_id": normal_raw,
            "sender_peer_id": peer_id, "grouped_id": None, "raw_text": edited_text,
            "media_json": [], "posted_at": None, "rule_config": RULE_BASE,
        })
        protected = await pool.fetchrow(
            "SELECT status,contact_username,nickname,price_amount,manual_fields,published_at "
            "FROM listings WHERE source_msg_id=$1", normal_raw,
        )
        assert protected["status"] == "published" and protected["contact_username"] == "alphauser"
        assert protected["nickname"] == "人工锁定" and str(protected["price_amount"]) == "350.00"
        protected_fields = protected["manual_fields"]
        if isinstance(protected_fields, str):
            protected_fields = json.loads(protected_fields)
        assert protected_fields == {"nickname": True} and protected["published_at"] is not None

        await pool.execute("DELETE FROM source_rule_bindings WHERE source_id=$1", SOURCE_ID)
        await add_rule("merge-identity", {"username": [r"TG[:：]\s*@?([A-Za-z0-9_]{5,32})"]}, "identity")
        await add_rule("merge-profile", {"nickname": [r"昵称[:：]\s*([^\s]+)"]}, "profile")
        merged_raw = await add_raw("TG:@mergeuser 昵称：合并候选")
        merged = await pool.fetchrow("SELECT status,contact_username,nickname FROM listings WHERE source_msg_id=$1", merged_raw)
        assert merged and merged["status"] == "pending" and merged["contact_username"] == "mergeuser"
        assert merged["nickname"] == "合并候选"
        listing_ids.extend(await pool.fetch("SELECT id FROM listings WHERE source_msg_id=$1", merged_raw))

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

        active_runs = await pool.fetchval("SELECT count(*) FROM rule_parse_runs WHERE run_reason='active' AND raw_revision_id IN "
                                          "(SELECT id FROM raw_message_revisions WHERE raw_id=ANY($1::bigint[]))", raw_ids)
        assert active_runs >= len(raw_ids), (active_runs, len(raw_ids))
        print("Active Core/PostgreSQL pipeline E2E: pending Listing, kill-switch Legacy fallback, additive field merge, "
              "identity/field conflict review, missing identity review, Lifecycle review, no unsafe Listing creation: OK")
    finally:
        if raw_ids:
            await pool.execute("DELETE FROM listings WHERE source_msg_id=ANY($1::bigint[])", raw_ids)
            await pool.execute("DELETE FROM raw_messages WHERE id=ANY($1::bigint[])", raw_ids)
        if listing_ids:
            await pool.execute("DELETE FROM listings WHERE id=ANY($1::bigint[])", [row["id"] for row in listing_ids])
        if rule_ids:
            await pool.execute("DELETE FROM ingest_rules WHERE id=ANY($1::bigint[])", rule_ids)
        await pool.execute("DELETE FROM ingest_sources WHERE id=$1", SOURCE_ID)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
