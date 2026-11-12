"""HTTP + PostgreSQL integration check for Active Router review actions.

Run inside an isolated Core container with Active review migrations applied.
The script creates and deletes only uniquely named synthetic records.
"""
import asyncio
import json
import os
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import asyncpg
from app.api import auth
from app.config import get_settings

BASE_URL = os.environ.get("NEXUS_E2E_BASE_URL", "http://127.0.0.1:8000")


def request_json(token, path, method, body=None):
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def post(token, queue_id, action, fields=None):
    body = {"action": action, "reason": "isolated HTTP integration test"}
    if fields is not None:
        body["confirmed_fields"] = fields
    return request_json(token, f"/api/admin/rules/active-review-queue/{queue_id}/resolve", "POST", body)


async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(**settings.db_kwargs(), min_size=1, max_size=5)
    suffix = uuid.uuid4().hex[:12]
    username = f"active_e2e_{suffix}"
    peer_id = -int(uuid.uuid4().int % 8_000_000_000) - 1
    allowed_source_ids = {int(part.strip()) for part in settings.active_router_source_ids.split(",")
                          if part.strip().isdigit()}
    assert settings.active_router_enabled and not settings.active_router_kill_switch
    assert len(allowed_source_ids) == 1
    source_id = next(iter(allowed_source_ids))
    raw_ids = listing_ids = None
    rule_id = None
    try:
        actor_id = await pool.fetchval(
            "INSERT INTO site_users(username,password_hash,display_name,is_admin) "
            "VALUES($1,$2,'isolated Active review test',TRUE) RETURNING id",
            username, auth.hash_password(uuid.uuid4().hex),
        )
        token, _ = auth.issue_token(actor_id, True)

        rule_name = f"active-rollback-e2e-{suffix}"
        config_v1 = {
            "parser_key": "ast_rules_v1", "gate": {},
            "extract": {"username": [r"@([A-Za-z0-9_]{5,32})"]},
            "ocr": {"enabled": False, "trigger_when_missing": []}, "lifecycle_terms": [],
        }
        status, created_rule = request_json(token, "/api/admin/rules", "PUT", {
            "rule_name": rule_name, "rule_type": "listing", "config": config_v1,
            "is_active": True, "parser_key": "ast_rules_v1", "change_summary": "isolated v1",
        })
        assert status == 200, (status, created_rule)
        rule_id = created_rule["rule_id"]
        config_v2 = {**config_v1, "extract": {"username": [r"TG[:：]@?([A-Za-z0-9_]{5,32})"]}}
        status, updated_rule = request_json(token, "/api/admin/rules", "PUT", {
            "rule_name": rule_name, "rule_type": "listing", "config": config_v2,
            "is_active": True, "parser_key": "ast_rules_v1", "expected_version": 1,
            "change_summary": "isolated v2",
        })
        assert status == 200 and updated_rule["version"] == 2, (status, updated_rule)
        status, rolled_back = request_json(token, f"/api/admin/rules/{rule_id}/rollback", "POST", {
            "target_version": 1, "change_summary": "isolated rollback",
        })
        assert status == 200 and rolled_back["version"] == 3, (status, rolled_back)
        status, version_list = request_json(token, f"/api/admin/rules/{rule_id}/versions", "GET")
        assert status == 200
        rollback_version = next(item for item in version_list["items"] if item["version"] == 3)
        rollback_config = rollback_version["config_json"]
        assert rollback_config == config_v1 and rollback_version["snapshot_origin"] == "rollback"
        await pool.execute(
            "INSERT INTO ingest_sources(id,peer_id,title,username,router_mode) VALUES($1,$2,$3,$4,'legacy')",
            source_id, peer_id, "Isolated Active review test", username,
        )

        mode_path = f"/api/admin/rules/sources/{source_id}/router-mode"
        status, _ = request_json("", mode_path, "PUT", {"mode": "active"})
        assert status == 401, f"unauthenticated Active mode request accepted: {status}"
        status, _ = request_json(token, f"/api/admin/rules/sources/{source_id + 999}/router-mode",
                                 "PUT", {"mode": "active"})
        assert status == 409, f"non-allowlisted source accepted: {status}"

        non_admin_id = await pool.fetchval(
            "INSERT INTO site_users(username,password_hash,display_name,is_admin) "
            "VALUES($1,$2,'isolated non-admin',FALSE) RETURNING id",
            f"active_nonadmin_{suffix}", auth.hash_password(uuid.uuid4().hex),
        )
        non_admin_token, _ = auth.issue_token(non_admin_id, False)
        status, _ = request_json(non_admin_token, mode_path, "PUT", {"mode": "active"})
        assert status == 401, f"non-admin Active mode request accepted: {status}"

        other_source = source_id + 999
        await pool.execute(
            "INSERT INTO ingest_sources(id,peer_id,title,username,router_mode) "
            "VALUES($1,$2,'isolated pre-existing Active source',$3,'active')",
            other_source, peer_id - 1, f"other{suffix}",
        )
        status, _ = request_json(token, mode_path, "PUT", {"mode": "active"})
        assert status == 409, f"second Active source was accepted: {status}"
        await pool.execute("DELETE FROM ingest_sources WHERE id=$1", other_source)

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_modes = list(executor.map(
                lambda _: request_json(token, mode_path, "PUT", {"mode": "active"}), range(2),
            ))
        assert [status for status, _ in concurrent_modes] == [200, 200], concurrent_modes
        assert await pool.fetchval("SELECT router_mode='active' FROM ingest_sources WHERE id=$1", source_id)
        assert await pool.fetchval(
            "SELECT count(*) FROM rule_router_mode_events WHERE source_id=$1 AND new_mode='active'", source_id,
        ) == 1
        status, mode_result = request_json(token, mode_path, "PUT", {"mode": "legacy"})
        assert status == 200 and mode_result["mode"] == "legacy"
        assert await pool.fetchval("SELECT router_mode='legacy' FROM ingest_sources WHERE id=$1", source_id)
        assert await pool.fetchval(
            "SELECT count(*) FROM rule_router_mode_events WHERE source_id=$1 "
            "AND previous_mode='active' AND new_mode='legacy' AND actor_user_id=$2",
            source_id, actor_id,
        ) == 1
        raw_ids, listing_ids = [], []

        async def candidate(reason, fields):
            raw_id = await pool.fetchval(
                "INSERT INTO raw_messages(source_id,platform_msg_id,raw_text,extract_state) "
                "VALUES($1,$2,'synthetic Active review evidence','dropped') RETURNING id",
                source_id, int(uuid.uuid4().int % 2_000_000_000),
            )
            raw_ids.append(raw_id)
            revision_hash = await pool.fetchval(
                "SELECT content_hash FROM raw_message_revisions WHERE raw_id=$1 ORDER BY revision DESC LIMIT 1",
                raw_id,
            )
            queue_id = await pool.fetchval(
                "INSERT INTO rule_active_review_queue(source_id,raw_id,content_hash,reason,candidate_json) "
                "VALUES($1,$2,$3,$4,$5::jsonb) RETURNING id",
                source_id, raw_id, revision_hash, reason,
                json.dumps({"fields": fields, "evidence": {}, "identity": fields.get("username", "")}),
            )
            return queue_id, raw_id

        unmatched_id, unmatched_raw = await candidate("no_rule_match", {})
        status, unmatched_list = request_json(
            token, "/api/admin/rules/active-review-queue?status=open&category=unmatched", "GET",
        )
        assert status == 200 and any(item["id"] == unmatched_id for item in unmatched_list["items"])
        status, detail = request_json(
            token, f"/api/admin/rules/active-review-queue/{unmatched_id}", "GET",
        )
        assert status == 200 and detail["raw_id"] == unmatched_raw
        status, requeued = request_json(
            token, f"/api/admin/rules/active-review-queue/{unmatched_id}/requeue", "POST",
        )
        assert status == 200 and requeued["reason"] == "manual_review_requested"
        assert await pool.fetchval(
            "SELECT count(*) FROM rule_active_review_events WHERE queue_id=$1 "
            "AND actor_user_id=$2 AND action='manual_requeue'", unmatched_id, actor_id,
        ) == 1
        status, _ = request_json(
            token, f"/api/admin/rules/active-review-queue/{unmatched_id}/requeue", "POST",
        )
        assert status == 409, "a no-match record was requeued more than once"

        ocr_id, ocr_raw = await candidate("human_review_required", {"username": f"ocr_{suffix}"})
        media_path = f"isolated/{suffix}.jpg"
        await pool.execute(
            "UPDATE raw_messages SET media_json=$2::jsonb WHERE id=$1",
            ocr_raw, json.dumps([{"path": media_path, "kind": "photo"}]),
        )
        await pool.execute(
            "INSERT INTO listing_media_ocr(raw_id,media_path,status,review_status,raw_text,fields_json) "
            "VALUES($1,$2,'done','pending','synthetic OCR evidence', '{\"nickname\":\"synthetic\"}'::jsonb)",
            ocr_raw, media_path,
        )
        status, detail = request_json(
            token, f"/api/admin/rules/active-review-queue/{ocr_id}", "GET",
        )
        assert status == 200 and detail["media"][0]["ocr_result"]["raw_text"] == "synthetic OCR evidence"
        assert detail["media"][0]["ocr_result"]["fields_json"] == {"nickname": "synthetic"}

        original_username = f"preserved_{suffix}"
        original_id = await pool.fetchval(
            "INSERT INTO listings(contact_username,nickname,status,manual_fields,published_at) "
            "VALUES($1,'Original manual card','published','{\"nickname\":true}'::jsonb,NOW()) RETURNING id",
            original_username,
        )
        listing_ids.append(original_id)

        missing_id, _ = await candidate("identity_unresolved", {})
        status, _ = post(token, missing_id, "create_pending_listing", {})
        assert status == 409, f"missing identity accepted: {status}"
        assert await pool.fetchval(
            "SELECT status='open' FROM rule_active_review_queue WHERE id=$1", missing_id,
        )

        create_id, create_raw = await candidate(
            "rule_conflict", {"username": original_username, "nickname": "Extracted"},
        )
        status, result = post(token, create_id, "create_pending_listing", {
            "username": original_username, "nickname": "Confirmed", "price_amount": 299,
        })
        assert status == 200 and result["status"] == "resolved", result
        created = await pool.fetchrow(
            "SELECT status,published_at,nickname,contact_username FROM listings WHERE source_msg_id=$1",
            create_raw,
        )
        assert created and created["status"] == "duplicate_pending" and created["published_at"] is None
        assert created["nickname"] == "Confirmed" and created["contact_username"] == original_username
        existing = await pool.fetchrow(
            "SELECT status,nickname,manual_fields,published_at FROM listings WHERE id=$1", original_id,
        )
        manual_fields = existing["manual_fields"]
        if isinstance(manual_fields, str):
            manual_fields = json.loads(manual_fields)
        assert existing["status"] == "published" and existing["nickname"] == "Original manual card"
        assert manual_fields == {"nickname": True} and existing["published_at"] is not None
        assert await pool.fetchval(
            "SELECT actor_user_id=$2 AND action='create_pending_listing' "
            "FROM rule_active_review_events WHERE queue_id=$1", create_id, actor_id,
        )
        status, _ = post(token, create_id, "no_action")
        assert status == 409, f"repeat review was not rejected: {status}"

        for action, expected in (("dismiss", "dismissed"), ("no_action", "resolved")):
            queue_id, _ = await candidate("no_rule_match", {})
            status, result = post(token, queue_id, action, {"nickname": "reviewed but not applied"})
            assert status == 200 and result["status"] == expected, (action, status, result)
            event = await pool.fetchrow(
                "SELECT count(*) AS n, decision_json FROM rule_active_review_events "
                "WHERE queue_id=$1 AND actor_user_id=$2 GROUP BY decision_json",
                queue_id, actor_id,
            )
            assert event and event["n"] == 1
            decision = event["decision_json"]
            if isinstance(decision, str):
                decision = json.loads(decision)
            assert decision == {"confirmed_fields": {"nickname": "reviewed but not applied"}}

        invalid_id, _ = await candidate("field_conflict", {"username": f"valid_{suffix}"})
        status, _ = post(token, invalid_id, "create_pending_listing", {
            "username": f"valid_{suffix}", "price_amount": "not-a-price",
        })
        assert status == 422
        assert await pool.fetchval(
            "SELECT status='open' FROM rule_active_review_queue WHERE id=$1", invalid_id,
        )

        concurrent_id, _ = await candidate("rule_conflict", {})
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: post(token, concurrent_id, "no_action"), range(2),
            ))
        assert sorted(status for status, _ in results) == [200, 409], results
        assert await pool.fetchval(
            "SELECT count(*) FROM rule_active_review_events WHERE queue_id=$1", concurrent_id,
        ) == 1
        print("Active review HTTP + PostgreSQL integration: review/unmatched split, OCR/media detail, "
              "manual requeue audit, identity guard, pending-only Listing, manual/published protection, "
              "dismiss/no_action, idempotence, concurrency: OK")
    finally:
        if raw_ids:
            await pool.execute("DELETE FROM listings WHERE source_msg_id=ANY($1::bigint[])", raw_ids)
            await pool.execute("DELETE FROM raw_messages WHERE id=ANY($1::bigint[])", raw_ids)
        if listing_ids:
            await pool.execute("DELETE FROM listings WHERE id=ANY($1::bigint[])", listing_ids)
        if source_id:
            await pool.execute("DELETE FROM ingest_sources WHERE id=$1", source_id)
        if rule_id:
            await pool.execute("DELETE FROM ingest_rules WHERE id=$1", rule_id)
        if username:
            await pool.execute("DELETE FROM site_users WHERE username=$1", username)
        await pool.execute("DELETE FROM site_users WHERE username=$1", f"active_nonadmin_{suffix}")
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
