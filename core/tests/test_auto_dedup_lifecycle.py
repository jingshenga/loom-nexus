"""Run the real intake SQL and incremental migration against PostgreSQL."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import asyncpg

from app import db
from app.api import auth, review
from app.extractor.media_ocr import MediaOcrProcessor

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("NEXUS_TEST_DATABASE_URL")
MIGRATION = ROOT / "scripts/migrations/2026-11-19-auto-dedup-lifecycle.sql"
INTAKE = (ROOT / "core/app/sql/upsert_newest.sql").read_text()
CANDIDATES = (ROOT / "core/app/sql/duplicate_candidates.sql").read_text()
IDENTITIES = (ROOT / "core/app/sql/duplicate_identity_groups.sql").read_text()
PROFILE_INTAKE = (ROOT / "bridge/runner/sql/profile_collection_listing.sql").read_text()
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
ALBUM = [{"kind": "photo", "path": f"approved-{index}.jpg"} for index in range(3)]


@unittest.skipUnless(DSN, "requires isolated nexus_test PostgreSQL")
class AutoDedupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertEqual(urlparse(DSN).path, "/nexus_test")
        conn = await asyncpg.connect(DSN)
        self.addAsyncCleanup(conn.close)
        await conn.execute("DROP SCHEMA IF EXISTS auto_dedup_test CASCADE")
        self.addAsyncCleanup(conn.execute, "DROP SCHEMA IF EXISTS auto_dedup_test CASCADE")
        await conn.execute("CREATE SCHEMA auto_dedup_test; SET search_path TO auto_dedup_test")
        schema = (ROOT / "scripts/schema.sql").read_text()
        migration = MIGRATION.read_text()
        self.assertTrue(schema.rstrip().endswith(migration.rstrip()))
        await conn.execute(schema[:schema.rindex(migration)])
        await conn.execute((MIGRATION.parent / "2026-10-31-ocr-classifier-training.sql").read_text())
        await conn.execute("""
            INSERT INTO ingest_sources(id,peer_id,title) VALUES (81,-81,'来源 A'), (82,-82,'来源 B');
        """)
        self.conn = conn
        self.pool = await asyncpg.create_pool(
            DSN, min_size=1, max_size=4, server_settings={"search_path": "auto_dedup_test"},
        )
        self.addAsyncCleanup(self.pool.close)

    async def migrate(self):
        await self.conn.execute(MIGRATION.read_text())

    async def raw(self, raw_id, body, days=0, source=81):
        posted_at = T0 + timedelta(days=days)
        await self.conn.execute("""
            INSERT INTO raw_messages(id,source_id,platform_msg_id,raw_text,posted_at)
            VALUES ($1,$2,$1,$3,$4)
        """, raw_id, source, body, posted_at)
        return posted_at

    async def intake(self, raw_id, body, *, peer=1001, username="ExactUser", name="示例 A",
                     district=None, price=600, publish=False, media=(), evidence=None,
                     custom=None, pool=None):
        return await (pool or self.conn).fetchrow(
            INTAKE, raw_id, peer, username, name, district, price, "CNY", body,
            json.dumps(media), json.dumps(evidence or {}), 0.95, publish, None, json.dumps(custom or {}),
        )

    async def card(self, listing_id):
        row = dict(await self.conn.fetchrow("SELECT * FROM listings WHERE id=$1", listing_id))
        for key in ("evidence_json", "media_json", "custom_fields"):
            row[key] = json.loads(row[key])
        return row

    async def test_new_card_archives_older_published_and_pending_and_inherits_reviewed_values(self):
        await self.migrate()
        await self.raw(101, "first text")
        old = await self.intake(101, "first text", district="海曙区", publish=True,
                                media=ALBUM + [{"kind": "photo", "path": "private.jpg", "visibility": "admin"}],
                                custom={"height_cm": 168, "bust_size": "C"},
                                evidence={"weight_kg": {"value": 48.5}})
        self.assertEqual(old["status"], "published")
        self.assertTrue(await self.conn.fetchval("SELECT listings_publish($1)", old["listing_id"]))
        self.assertIsNotNone((await self.card(old["listing_id"]))["manually_approved_at"])
        # An additional pending card predates this feature. Its unreviewed values are never inherited.
        pending_id = await self.conn.fetchval("""
            INSERT INTO listings(contact_peer_id, contact_username, nickname, source_posted_at,
                                 evidence_json, status)
            VALUES (1001, 'exactuser', 'older draft', $1,
                    '{"height_cm":{"value":199},"bust_size":{"value":"I"}}', 'pending')
            RETURNING id
        """, T0 + timedelta(days=1))
        await self.raw(102, "updated second text", days=2)
        new = await self.intake(102, "updated second text", username="EXACTUSER", publish=True,
                                evidence={"bust_size": {"value": "D", "src": "regex"}})
        self.assertEqual((new["status"], new["conflicted"]), ("published", 2))
        new_id = new["listing_id"]
        for listing_id in (old["listing_id"], pending_id):
            row = await self.card(listing_id)
            self.assertEqual(row["status"], "archived")
            self.assertEqual(row["evidence_json"]["lifecycle"], {
                "reason": "superseded_by_newer_listing", "superseded_by": new_id,
            })
        row = await self.card(new_id)
        self.assertEqual(row["district"], "海曙区")
        self.assertEqual(row["media_json"], ALBUM)
        self.assertEqual(row["evidence_json"]["bust_size"]["value"], "D")
        for key, value in (("height_cm", 168), ("weight_kg", 48.5), ("district", "海曙区"), ("media", 3)):
            self.assertEqual(row["evidence_json"][key], {
                "value": value, "src": "inherited", "inherited_from": old["listing_id"],
            })
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM listing_person_group_members"), 3)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM listing_person_groups"), 1)
        self.assertFalse(await self.conn.fetch(CANDIDATES))
        self.assertFalse(await self.conn.fetch(IDENTITIES))

    async def test_repost_attaches_across_sources_without_mutating_card_and_survives_retry(self):
        await self.migrate()
        await self.raw(101, "示例 文案 Ａ")
        first = await self.intake(101, "示例 文案 Ａ", name="已校正", media=ALBUM)
        posted = await self.raw(102, "\n示例文案 a", days=29, source=82)
        for _ in range(2):
            repeat = await self.intake(102, "示例文案 a", username="EXACTUSER", name="转发的新显示名",
                                       evidence={"new": "must not change canonical"})
            self.assertEqual(repeat["listing_id"], first["listing_id"])
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM listings"), 1)
        row = await self.card(first["listing_id"])
        self.assertEqual(row["nickname"], "已校正")
        self.assertEqual(row["media_json"], ALBUM)
        self.assertEqual(row["last_active_at"], posted)
        self.assertEqual(row["source_msg_id"], 101)
        links = await self.conn.fetch("SELECT listing_id,raw_id FROM listing_repost_evidence")
        self.assertEqual([(link["listing_id"], link["raw_id"]) for link in links], [(first["listing_id"], 102)])
        await self.raw(103, "示例文案 a", days=60)
        outside = await self.intake(103, "示例文案 a")
        self.assertNotEqual(outside["listing_id"], first["listing_id"])
        self.assertEqual((await self.card(first["listing_id"]))["status"], "archived")

    async def test_concurrent_reposts_have_one_canonical_listing(self):
        await self.migrate()
        for raw_id in (101, 102, 103):
            await self.raw(raw_id, "exact text", source=81 if raw_id == 101 else 82)
        rows = await asyncio.gather(*(self.intake(raw_id, "exact text", pool=self.pool)
                                      for raw_id in (101, 102, 103)))
        self.assertEqual(len({row["listing_id"] for row in rows}), 1)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM listing_repost_evidence"), 2)

    async def test_cross_account_profile_or_similar_text_is_held_but_nickname_alone_is_not(self):
        await self.migrate()
        first_text = "今天的资料更新以及说明，附近地点使用示例文本。请联系安排时间和查看相关说明，图片仅用于测试。"
        await self.raw(101, first_text)
        first = await self.intake(101, first_text, district="海曙区", publish=True, media=ALBUM)
        await self.raw(102, "different description", days=1)
        conflicting = await self.intake(102, "different description", peer=2002, username="EXACTUSER",
                                        district=" 海曙区 ", publish=True, media=ALBUM)
        self.assertEqual(conflicting["status"], "duplicate_pending")
        self.assertEqual((await self.card(first["listing_id"]))["status"], "published")
        self.assertIsNone((await self.card(conflicting["listing_id"]))["published_at"])
        await self.raw(103, "irrelevant description", days=1)
        unrelated = await self.intake(103, "irrelevant description", peer=2003, username="OtherAccount",
                                      district="江北区")
        self.assertEqual(unrelated["status"], "pending")
        similar_text = first_text.replace("今天", "今晚")
        await self.raw(104, similar_text)
        missing_contact = await self.intake(104, similar_text, peer=None, username=None, name="另外的昵称")
        self.assertEqual(missing_contact["status"], "duplicate_pending")
        pairs = await self.conn.fetch(CANDIDATES)
        self.assertEqual({(row["left_id"], row["right_id"], row["match_reason"]) for row in pairs}, {
            (first["listing_id"], conflicting["listing_id"], "昵称、城区及资费一致"),
            (first["listing_id"], missing_contact["listing_id"], "原文高度相似"),
        })

    async def test_review_can_keep_both_for_a_similar_text_conflict(self):
        await self.migrate()
        text = "这是一段用于比较跨账号相似度的完整中文文案，展示界面应允许管理员记录两个账号均可保留。"
        await self.raw(101, text)
        await self.raw(102, text.replace("界面", "页面"), days=1)
        first = await self.intake(101, text, name="昵称一")
        second = await self.intake(102, text.replace("界面", "页面"), peer=2002,
                                   username="AnotherAccount", name="昵称二")
        self.assertEqual(second["status"], "duplicate_pending")
        token, _ = auth.issue_token(42, True)
        with patch.object(db, "_pool", self.pool):
            await review.decide_duplicate(first["listing_id"], second["listing_id"],
                                          review.DuplicateDecision(decision="keep_both"), f"Bearer {token}")
        self.assertEqual((await self.card(second["listing_id"]))["status"], "pending")
        self.assertFalse(await self.conn.fetch(CANDIDATES))

    async def test_older_history_does_not_replace_newer_account_card(self):
        await self.migrate()
        await self.raw(101, "newest original", days=4)
        first = await self.intake(101, "newest original", publish=True, media=ALBUM)
        await self.raw(102, "older original")
        older = await self.conn.fetchrow("""
            SELECT * FROM listings_upsert_history(102,NULL,'exactuser','older',NULL,
                                                  600,'CNY','older original','[]','{}',0.95,TRUE,NULL)
        """)
        self.assertEqual(older["status"], "archived")
        self.assertEqual((await self.card(first["listing_id"]))["status"], "published")
        self.assertEqual((await self.card(older["listing_id"]))["evidence_json"]["lifecycle"]["superseded_by"],
                         first["listing_id"])

    async def test_migration_releases_legacy_duplicate_holds_and_is_idempotent(self):
        await self.raw(101, "first message")
        await self.raw(102, "second message", days=1)
        await self.conn.execute("""
            INSERT INTO listings(id,source_msg_id,status,contact_username,nickname)
            VALUES (101,101,'published','ExactUser','test nickname'),
                   (102,102,'duplicate_pending','exactuser','test nickname'),
                   (103,NULL,'duplicate_pending','different','test nickname');
        """)
        await self.migrate()
        await self.migrate()
        rows = await self.conn.fetch("SELECT id,status FROM listings ORDER BY id")
        self.assertEqual([(row["id"], row["status"]) for row in rows],
                         [(101, "archived"), (102, "pending"), (103, "pending")])
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM listing_person_groups"), 1)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM listing_person_group_members"), 2)

    async def test_bridge_profile_intake_uses_the_same_fingerprint_and_supersede_rules(self):
        await self.migrate()
        await self.raw(101, "A channel text")
        first = await self.intake(101, "A channel text")
        await self.conn.execute("""
            INSERT INTO bridge_accounts(id,account_uid,display_name,session_file) VALUES (41,4100,'fixture','fixture.session');
            INSERT INTO ingest_rules(id,rule_name,config_json) VALUES (51,'fixture','{}');
            INSERT INTO profile_collection_jobs(id,rule_id,duty_date,source_id,account_id,contact_username)
            VALUES (61,51,'2026-10-02',81,41,'exactuser'), (62,51,'2026-10-03',82,41,'exactuser');
            INSERT INTO profile_collection_results(job_id,contact_peer_id,contact_username,
                                                   description,evidence_json,collected_at)
            VALUES (61,1001,'exactuser','A channel text','{"nickname":"示例 A"}','2026-10-02'),
                   (62,1001,'exactuser','Changed profile introduction','{"nickname":"示例 B"}','2026-10-03');
        """)
        self.assertEqual(await self.conn.fetchval(PROFILE_INTAKE, 61, "tg_profile_v2"), first["listing_id"])
        self.assertEqual(await self.conn.fetchval(
            "SELECT listing_id FROM listing_repost_evidence WHERE profile_job_id=61"), first["listing_id"])
        replacement = await self.conn.fetchval(PROFILE_INTAKE, 62, "tg_profile_v2")
        self.assertEqual((await self.card(replacement))["status"], "pending")
        self.assertEqual((await self.card(first["listing_id"]))["status"], "archived")


    async def test_profile_enrichment_holds_cross_account_conflict_and_ignores_archived_card(self):
        await self.migrate()
        await self.raw(101, "original", days=1)
        old = await self.intake(101, "original", district="海曙区", publish=True, media=ALBUM)
        await self.conn.execute("""
            INSERT INTO bridge_accounts(id,session_file) VALUES (41,'fixture.session');
            INSERT INTO ingest_rules(id,rule_name,config_json)
            VALUES (51,'fixture','{"extract":{"price":["价格:([0-9]+)"]}}');
            INSERT INTO source_rule_bindings(source_id,rule_id) VALUES (82,51);
            INSERT INTO profile_collection_jobs(id,rule_id,duty_date,source_id,account_id,contact_username)
            VALUES (61,51,'2026-10-03',82,41,'otheraccount');
        """)
        result_id = await self.conn.fetchval("""
            INSERT INTO profile_collection_results(job_id,contact_peer_id,contact_username,
                                                   description,media_json,evidence_json,collected_at)
            VALUES (61,2002,'otheraccount','另一个来源 价格:600',$1,
                    '{"nickname":"示例 A","district":"海曙区"}','2026-10-03')
            RETURNING id
        """, json.dumps(ALBUM))
        listing_id = await self.conn.fetchval(PROFILE_INTAKE, 61, "tg_profile_v2")
        self.assertEqual((await self.card(listing_id))["status"], "pending")
        processor = MediaOcrProcessor(self.pool, "", None)
        await processor._sync_profile_listing(result_id)
        self.assertEqual((await self.card(listing_id))["status"], "duplicate_pending")
        self.assertEqual((await self.card(old["listing_id"]))["status"], "published")
        await self.conn.execute("UPDATE listings SET status='archived' WHERE id=$1", listing_id)
        await processor._sync_profile_listing(result_id)
        self.assertEqual((await self.card(listing_id))["status"], "archived")
