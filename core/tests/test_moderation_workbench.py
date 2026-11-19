import asyncio
import json
from datetime import datetime
import os
from pathlib import Path
import unittest
from urllib.parse import urlencode, urlparse

import asyncpg
from fastapi import FastAPI

from app import db
from app.api import auth, listings, moderation, rules


DSN = os.environ.get("NEXUS_TEST_DATABASE_URL")
SCHEMA = Path(__file__).resolve().parents[2] / "scripts/schema.sql"
ENDPOINT = "/api/admin/moderation/workbench"
app = FastAPI()
app.include_router(moderation.router)
app.include_router(rules.router)


async def request(method, path, *, params=None, body=None, actor=None):
    headers = [(b"content-type", b"application/json")]
    if actor:
        token, _ = auth.issue_token(*actor)
        headers.append((b"authorization", f"Bearer {token}".encode()))
    sent = []

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b""}

    async def send(event):
        sent.append(event)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "scheme": "http", "path": path, "root_path": "", "method": method,
        "headers": headers, "query_string": urlencode(params or {}).encode(),
    }, receive, send)
    return sent[0]["status"], json.loads(sent[1]["body"])


class ModerationAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_routes_reject_anonymous_and_regular_users(self):
        for actor in (None, (42, False)):
            for method, path in (("GET", f"{ENDPOINT}/next"), ("POST", f"{ENDPOINT}/1/decision")):
                status, _ = await request(method, path, actor=actor)
                self.assertEqual(status, 401)

    async def test_invalid_action_and_invalid_attributes_return_422(self):
        for body in (
            {"action": "delete", "expected_updated_at": None},
            {"action": "save", "expected_updated_at": None, "attributes": {"height_cm": -10}},
            {"action": "save", "expected_updated_at": None, "attributes": {"unexpected": 17}},
        ):
            status, _ = await request("POST", f"{ENDPOINT}/1/decision", body=body, actor=(42, True))
            self.assertEqual(status, 422)


@unittest.skipUnless(DSN, "requires isolated nexus_test PostgreSQL")
class ModerationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    # fixed schema name keeps every statement a constant string (the
    # repo bans assembling SQL via f-strings/concatenation); isolation
    # comes from DSN pointing at a dedicated nexus_test database
    SCHEMA_NAME = "moderation_test"

    async def asyncSetUp(self):
        self.assertEqual(urlparse(DSN).path, "/nexus_test")
        connection = await asyncpg.connect(DSN)
        self.addAsyncCleanup(connection.close)
        await connection.execute("DROP SCHEMA IF EXISTS moderation_test CASCADE")
        await connection.execute("CREATE SCHEMA moderation_test")
        self.addAsyncCleanup(
            connection.execute, "DROP SCHEMA IF EXISTS moderation_test CASCADE",
        )
        await connection.execute("SET search_path TO moderation_test")
        await connection.execute(SCHEMA.read_text())
        for name in ("2026-10-31-ocr-classifier-training.sql", "2026-11-08-listing-person-groups.sql"):
            await connection.execute((SCHEMA.parent / "migrations" / name).read_text())
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4,
                                              server_settings={"search_path": "moderation_test"})
        self.addAsyncCleanup(self.pool.close)
        old_pool, db._pool = db._pool, self.pool
        self.addCleanup(setattr, db, "_pool", old_pool)
        await self.pool.execute("""
            INSERT INTO site_users(id, username, password_hash, is_admin) VALUES (42, 'reviewer', 'fixture', true);
            INSERT INTO ingest_sources(id, peer_id, title) VALUES (801, -801, '示例来源群');
            INSERT INTO raw_messages(id, source_id, platform_msg_id, raw_text, media_json, posted_at)
            VALUES (811, 801, 100, '示例卡片：价格未知', '[{"kind":"photo","path":"album.jpg"}]', '2026-10-01 10:00:00+00');
            INSERT INTO listings(id, source_msg_id, status, nickname, district, price_amount, description,
                                 contact_username, media_json, confidence, custom_fields, manual_fields)
            VALUES
                (821, 811, 'pending', '示例卡片', '海曙区', NULL, '已有介绍', 'testuser',
                 '[{"kind":"photo","path":"cover.jpg"}]', 0.78, '{"keep_key":"原值"}', '{"district":true}'),
                (822, NULL, 'pending', '另一张', NULL, 100, '有介绍', 'otheruser', '[]', 0.79, '{}', '{}'),
                (823, NULL, 'duplicate_pending', '重复卡片', NULL, NULL, NULL, NULL, '[]', 0.70, '{}', '{}');
            INSERT INTO listing_media_ocr(id, raw_id, media_path, status, classification, review_status,
                                          raw_text, fields_json, evidence_json, confidence)
            VALUES (831, 811, 'album.jpg', 'done', 'info_card', 'pending', E'身高：168\\n附加文字',
                    '{"height_cm":168,"age_years":23,"price_1p_text":["套餐示例"],"fixed_intro_terms":["禁无套"]}',
                    '{"height_cm":{"line":"身高：168","score":0.92}}', 0.86);
            INSERT INTO listing_field_definitions(field_key, label, required_for_publish)
            VALUES ('source_check', '来源核对', true);
        """)
        self.actor = (42, True)

    async def get(self, **params):
        return await request("GET", f"{ENDPOINT}/next", params=params, actor=self.actor)

    async def decide(self, listing_id, **body):
        return await request("POST", f"{ENDPOINT}/{listing_id}/decision", body=body, actor=self.actor)

    async def test_aggregates_original_album_and_scored_ocr_and_applies_filters(self):
        for filter_name in ("low_confidence", "missing_price", "has_ocr"):
            status, data = await self.get(filter=filter_name)
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in data["queue"]], [821])
        status, data = await self.get(listing_id=821, limit=1)
        self.assertEqual(status, 200)
        self.assertTrue(data["has_more"])
        card = data["card"]
        self.assertEqual(card["source_title"], "示例来源群")
        self.assertEqual(card["original_text"], "示例卡片：价格未知")
        self.assertEqual(card["original_text_source"], "telegram")
        self.assertEqual([item["path"] for item in card["media"]], ["album.jpg", "cover.jpg"])
        self.assertEqual(card["attributes"]["height_cm"], 168)
        self.assertEqual(card["ocr"][0]["lines"], [
            {"text": "身高：168", "score": 0.92, "fields": ["height_cm"]},
            {"text": "附加文字", "score": None, "fields": []},
        ])
        self.assertEqual(card["missing_publish_fields"], ["价格", "来源核对"])
        status, _ = await self.get(listing_id=823)
        self.assertEqual(status, 404)

    async def test_profile_only_card_finds_source_album_and_ocr(self):
        await self.pool.execute("""
            INSERT INTO bridge_accounts(id,session_file) VALUES (803,'fixture.session');
            INSERT INTO ingest_rules(id,rule_name,rule_type,config_json)
            VALUES (804,'fixture profile','profile_collection','{}');
            INSERT INTO profile_collection_jobs(id,rule_id,duty_date,source_id,account_id,contact_username)
            VALUES (805,804,CURRENT_DATE,801,803,'otheruser');
            INSERT INTO profile_collection_results(id,job_id,contact_username,description,media_json)
            VALUES (806,805,'otheruser','资料采集原文','[{"kind":"photo","path":"profile_card.jpg"}]');
            UPDATE listings SET profile_collection_job_id=805 WHERE id=822;
            INSERT INTO listing_media_ocr(profile_result_id,media_path,status,classification,fields_json)
            VALUES (806,'profile_card.jpg','done','info_card','{"age_years":23}');
        """)
        status, data = await self.get(listing_id=822, filter="has_ocr")
        self.assertEqual(status, 200)
        self.assertIn(822, [item["id"] for item in data["queue"]])
        card = data["card"]
        self.assertEqual(card["source_title"], "示例来源群")
        self.assertEqual(card["original_text_source"], "profile")
        self.assertEqual(card["original_text"], "资料采集原文")
        self.assertEqual([item["path"] for item in card["media"]], ["profile_card.jpg"])
        self.assertEqual(card["attributes"]["age_years"], 23)

    async def test_save_then_approve_records_manual_fields_and_uses_original_date(self):
        _, data = await self.get(listing_id=821)
        stamp = data["card"]["updated_at"]
        status, saved = await self.decide(
            821, action="save", expected_updated_at=stamp, price_amount=680,
            attributes={"height_cm": 169}, required_fields={"source_check": "已核实"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["status"], "pending")
        stale, _ = await self.decide(821, action="approve", expected_updated_at=stamp)
        self.assertEqual(stale, 409)
        status, approved = await self.decide(821, action="approve", expected_updated_at=saved["updated_at"])
        self.assertEqual(status, 200)
        self.assertEqual(approved["decision"], "approved")
        row = await self.pool.fetchrow("SELECT * FROM listings WHERE id=821")
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["published_at"], await self.pool.fetchval("SELECT posted_at FROM raw_messages WHERE id=811"))
        self.assertEqual(json.loads(row["manual_fields"]), {"district": True, "price_amount": True, "custom_fields": True})
        self.assertEqual(json.loads(row["custom_fields"]), {"keep_key": "原值", "height_cm": 169, "source_check": "已核实"})
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM listing_edit_history WHERE listing_id=821 AND editor_user_id=42"), 2)
        self.assertEqual(await self.pool.fetchval("SELECT raw_text FROM raw_messages WHERE id=811"), "示例卡片：价格未知")

    async def test_clearing_extracted_fields_survives_public_ocr_fallback(self):
        _, data = await self.get(listing_id=821)
        status, _ = await self.decide(
            821, action="approve", expected_updated_at=data["card"]["updated_at"], price_amount=680,
            attributes={"age_years": None, "fixed_intro_terms": []}, required_fields={"source_check": "已核实"},
        )
        self.assertEqual(status, 200)
        [row] = await self.pool.fetch(listings.SQL_DETAIL, 821)
        ocr = await listings._ocr_by_listing(self.pool, [row])
        item = listings._row_to_item(row, is_admin=False, ocr_entries=ocr[821])
        self.assertIsNone(item["attributes"]["profile"]["age"])
        self.assertEqual(item["attributes"]["restrictions"], [])
        for filters in ((20, 30, None), (None, None, "禁无套")):
            result = await self.pool.fetch(listings.SQL_LIST, None, None, None, None, 10, 0, None, 42, "all", *filters)
            total = await self.pool.fetchval(listings.SQL_COUNT, None, None, None, None, None, "all", *filters)
            self.assertEqual((result, total), ([], 0))

    async def test_invalid_approval_and_failed_audit_roll_back_entire_decision(self):
        _, data = await self.get(listing_id=821)
        stamp = data["card"]["updated_at"]
        status, blocked = await self.decide(821, action="approve", expected_updated_at=stamp, nickname="未提交改动")
        self.assertEqual(status, 409)
        self.assertIn("价格", blocked["detail"])
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await request("POST", f"{ENDPOINT}/821/decision", actor=(99, True), body={
                "action": "reject", "expected_updated_at": stamp, "nickname": "回滚改动",
            })
        self.assertEqual(dict(await self.pool.fetchrow("SELECT nickname,status FROM listings WHERE id=821")),
                         {"nickname": "示例卡片", "status": "pending"})
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM listing_edit_history WHERE listing_id=821"), 0)

    async def test_golden_sample_snapshots_unsaved_edits_and_reports_replay_difference(self):
        _, data = await self.get(listing_id=821)
        stamp = data["card"]["updated_at"]
        status, saved = await self.decide(
            821, action="sample", expected_updated_at=stamp, price_amount=680,
            attributes={"height_cm": 169},
        )
        self.assertEqual((status, saved["decision"]), (200, "sample_saved"))
        listing = await self.pool.fetchrow("SELECT status,price_amount,updated_at FROM listings WHERE id=821")
        self.assertEqual(listing["status"], "pending")
        self.assertIsNone(listing["price_amount"])
        self.assertEqual(listing["updated_at"], datetime.fromisoformat(stamp))
        status, replay = await request(
            "POST", "/api/admin/rules/dryrun", body={"config": {}, "sample_set": "golden"}, actor=self.actor,
        )
        self.assertEqual((status, replay["total"], replay["passed"]), (200, 1, 0))
        result = replay["results"][0]
        self.assertEqual(result["differences"]["height_cm"], {"expected": 169, "actual": 168})
        self.assertIn("album.jpg", [asset["path"] for asset in result["media"]])

    async def test_concurrent_rejection_has_one_winner_and_removes_card(self):
        _, data = await self.get(listing_id=821)
        results = await asyncio.gather(*(
            self.decide(821, action="reject", expected_updated_at=data["card"]["updated_at"],
                        attributes={"restriction_text": ["管理员纠偏"]})
            for _ in range(2)
        ))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        self.assertEqual(await self.pool.fetchval("SELECT status FROM listings WHERE id=821"), "rejected")
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM listing_edit_history WHERE listing_id=821"), 1)
        _, remaining = await self.get()
        self.assertEqual([item["id"] for item in remaining["queue"]], [822])
