"""Integration regression for the review worker against the disposable release database."""
import asyncio
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncpg
from PIL import Image, ImageDraw

from app.extractor import review_automation as automation


async def main():
    async with asyncpg.create_pool(os.environ["NEXUS_COLLECTION_TEST_DSN"], min_size=1) as pool:
        assert await pool.fetchval("SELECT current_database()") == "nexus_collection"
        source_a = await pool.fetchval("INSERT INTO ingest_sources(peer_id) VALUES(-10088991151) RETURNING id")
        source_b = await pool.fetchval("INSERT INTO ingest_sources(peer_id) VALUES(-10088991152) RETURNING id")

        async def card(source, message, username, status="duplicate_pending", peer=None, nickname="Fixture", price=None):
            raw = await pool.fetchval(
                "INSERT INTO raw_messages(source_id,platform_msg_id,raw_text) VALUES($1,$2,$3) RETURNING id",
                source, message, f"联系 @{username}",
            )
            return await pool.fetchval("""
                INSERT INTO listings(source_msg_id,contact_username,nickname,status,contact_peer_id,price_amount)
                VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
                """, raw, username, nickname, status, peer, price)

        first = await card(source_a, 1, "review_fixture", "published")
        second = await card(source_b, 1, "review_fixture", price=100)
        bad_a = await card(source_a, 2, "review_conflict", peer=77101)
        bad_b = await card(source_b, 2, "review_conflict", peer=77102)
        richer = await card(source_b, 3, "review_rich", price=100)
        thin = await card(source_a, 3, "review_rich")
        alias_a = await card(source_a, 4, "review_old_alias", peer=77103)
        alias_b = await card(source_b, 4, "review_new_alias", peer=77103)
        keep_a = await card(source_a, 5, "review_keep")
        keep_b = await card(source_b, 5, "review_keep")
        await pool.execute("INSERT INTO listing_duplicate_decisions(left_listing_id,right_listing_id,decision) VALUES($1,$2,'keep_both')",
                           keep_a, keep_b)
        assert not await automation.group_cards(pool, [keep_a, keep_b])
        labeled_a = await card(source_a, 6, "review_labeled")
        labeled_b = await card(source_b, 6, "review_labeled")
        await pool.execute("""
            UPDATE raw_messages SET raw_text='发布者 @source_notice，TG：@review_labeled'
            WHERE id=(SELECT source_msg_id FROM listings WHERE id=$1)
            """, labeled_a)
        await pool.execute("""
            UPDATE listings SET source_msg_id=NULL,evidence_json='{
                "collection_method":"daily_checkin_source_username_search","profile_url":"https://t.me/review_labeled",
                "nickname":"Fixture","source_messages":[{"message_id":42,"matched_by":"exact_contact_username_in_message"}]
            }'::jsonb WHERE id=$1
            """, labeled_b)
        assert await automation.group_cards(pool, [labeled_a, labeled_b])
        assert await automation.group_cards(pool, [first, second])
        assert not await automation.group_cards(pool, [first, second])
        assert not await automation.group_cards(pool, [bad_a, bad_b])
        assert await automation.group_cards(pool, [thin, richer])
        assert await automation.group_cards(pool, [alias_a, alias_b])
        assert await pool.fetchval("SELECT nexus_auto_duplicate_folded($1)", thin)
        assert await pool.fetchval("SELECT status FROM listings WHERE id=$1", richer) == "pending"
        assert await pool.fetchval("SELECT nexus_auto_duplicate_folded($1)", second)
        assert not await pool.fetchval("SELECT nexus_auto_duplicate_folded($1)", first)
        assert await pool.fetchval("SELECT status FROM listings WHERE id=$1", first) == "published"
        group_id = await pool.fetchval("SELECT group_id FROM listing_person_group_members WHERE listing_id=$1", second)
        async with pool.acquire() as conn, conn.transaction():
            assert await automation.undo_group(conn, group_id, 1)
        assert not await pool.fetchval("SELECT nexus_auto_duplicate_folded($1)", second)
        assert not await automation.group_cards(pool, [first, second])
        await automation.group_pending(pool)

        await pool.execute("""
            INSERT INTO ocr_classification_categories(code,name,is_active,is_trainable,review_required)
            VALUES ('profile_photo','Fixture image',true,true,true) ON CONFLICT DO NOTHING
            """)
        raw = await pool.fetchval("SELECT source_msg_id FROM listings WHERE id=$1", first)

        async def asset(path, category, score, fields="{}", text=""):
            return await pool.fetchval("""
                INSERT INTO listing_media_ocr(raw_id,media_path,status,review_status,classification,
                    predicted_classification,predicted_confidence,predicted_model_sha,fields_json,raw_text)
                VALUES ($1,$2,'done','pending',$3,$3,$4,$5,$6::jsonb,$7) RETURNING id
                """, raw, path, category, score, automation.MODEL_SHA, fields, text)

        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for i, name in enumerate(("photo.png", "other.png", "protected.png", "learned.png", "card.png")):
                image = Image.new("RGB", (96, 96), (225, 80, 20))
                canvas = ImageDraw.Draw(image)
                canvas.rectangle((5 + i * 12, 12, 19 + i * 12, 80), fill="black")
                image.save(root / name)
            shutil.copyfile(root / "learned.png", root / "replica.png")
            assert automation.compare_photos(root, [{"kind":"photo","path":"learned.png"}],
                                                  [{"kind":"photo","path":"replica.png"}])["exact_images"] == 1

            photo = await asset("photo.png", "profile_photo", 0.999)
            other = await asset("other.png", "other", 0.999)
            protected = await asset("protected.png", "profile_photo", 0.999, '{"price_text":"价格 100 元"}')
            learning = await asset("learned.png", "other", 0.4)
            replica = await asset("replica.png", "other", 0.4)
            document = await asset("card.png", "info_card", 0.999, '{"stage_name":"Fixture"}', "昵称: Fixture")
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("INSERT INTO ocr_classification_labels(ocr_id,category_code,label_source) VALUES($1,'profile_photo','manual')", learning)
                await automation.record_learning(conn, learning, automation.fingerprint(root, "learned.png"))
            assert await automation.apply_ocr(pool, root, photo, "photo.png")
            assert not await automation.apply_ocr(pool, root, other, "other.png")
            assert not await automation.apply_ocr(pool, root, protected, "protected.png")
            assert await automation.apply_ocr(pool, root, replica, "replica.png")
            assert await automation.apply_ocr(pool, root, document, "card.png")
            assert not await automation.apply_ocr(pool, root, photo, "photo.png")
            assert await pool.fetchval("SELECT count(*) FROM ocr_classification_labels WHERE ocr_id=$1", replica) == 0
            assert await pool.fetchval("SELECT reason FROM nexus_auto_ocr_decisions WHERE ocr_id=$1", replica) == "human_exact_image"
            assert await pool.fetchval("SELECT status FROM listings WHERE id=$1", first) == "published"
        print("REVIEW_AUTOMATION_GATE_OK")


if __name__ == "__main__":
    asyncio.run(main())
