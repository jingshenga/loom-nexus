"""Conservative duplicate and OCR review decisions with reversible audit records."""
import asyncio
import hashlib
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageOps
from app.extractor.ocr_fields import equivalent_field, structured_ocr_candidate

logger = logging.getLogger("nexus-core.review-automation")
MODEL_SHA = "bcce9b51ec7a1008e2126482198cc37d318f2347e374097238fceed6581f6f68"
PERSON_LOCK = 58420261119
HANDLE = re.compile(r"(?<![\w@])(?:@|(?:https?://)?(?:t\.me|telegram\.me)/)([A-Za-z][A-Za-z0-9_]{3,30})(?![\w/])", re.I)
CARD_WORDS = re.compile(r"(昵称|艺名|身高|年龄|价格|课费|收费|费用|区域|城区|套餐)\s*[:：]|(?:课费|价格|收费|费用)\s*\d")
REPORT_WORDS = re.compile(r"检验报告|检测报告|检验结果|检测结果|参考区间|检验项目")
CANDIDATE_CARDS = """
SELECT l.id, l.status, l.nickname, l.contact_peer_id, nexus_tg_username(l.contact_username) AS username,
       l.contact_username, l.district, l.price_amount, l.price_currency, l.description, l.bot_url,
       l.manual_fields, l.media_json, l.evidence_json, l.created_at, l.published_at,
       r.raw_text, r.posted_at, m.group_id
FROM listings l LEFT JOIN raw_messages r ON r.id=l.source_msg_id
LEFT JOIN listing_person_group_members m ON l.id=m.listing_id
"""


def decode(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def normalized(value):
    return "".join(c for c in (value or "").casefold() if c.isalnum())


def usable_username(card):
    username = card["username"]
    if not username:
        return bool(not card["contact_username"] and positive_peer(card))
    manual = decode(card["manual_fields"], {})
    evidence = decode(card["evidence_json"], {})
    if manual.get("contact_username") or manual.get("username"):
        return True
    username_evidence = evidence.get("username")
    if isinstance(username_evidence, dict) and username_evidence.get("src") == "ocr":
        return False
    handles = {m.lower() for m in HANDLE.findall(card["raw_text"] or "") if not m.lower().endswith("bot")}
    corroborated = handles == {username}
    if handles and not corroborated:
        return False
    return corroborated or bool(card["contact_peer_id"] and card["contact_peer_id"] > 0)


def positive_peer(card):
    return card["contact_peer_id"] if card["contact_peer_id"] and card["contact_peer_id"] > 0 else None


def person_evidence(cards):
    if not all(usable_username(card) for card in cards):
        return None
    peers = {positive_peer(card) for card in cards} - {None}
    if len(peers) > 1:
        return None
    same_peer = len(peers) == 1 and all(positive_peer(card) for card in cards)
    usernames = {card["username"] for card in cards}
    nicknames = {
        name for card in cards
        if (name := normalized(card["nickname"])) and name != card["username"]
    }
    if not same_peer and (len(usernames) > 1 or len(nicknames) > 1):
        return None
    districts = {normalized(card["district"]) for card in cards} - {""}
    prices = {
        (str(card["price_amount"]), (card["price_currency"] or "").casefold())
        for card in cards if card["price_amount"] is not None
    }
    # Distinct legitimate updates remain available for a single group-level decision.
    if len(districts) > 1 or len(prices) > 1:
        return None
    return {"reason": "verified_peer" if same_peer else "exact_contact_username",
            "nickname_variants": len(nicknames), "contact_variants": len(usernames),
            "districts": len(districts), "price_variants": len(prices)}


def card_quality(card):
    media = decode(card["media_json"], [])
    effective_media = len({
        m.get("path") or m.get("poster") for m in media
        if m.get("kind") in {"photo", "video"} and (m.get("path") or m.get("poster"))
        and m.get("visibility", "public") == "public"
        and m.get("ocr_classification") not in {"info_card", "uncertain"}
    })
    fields = sum(bool(card[key]) for key in ("nickname", "district", "price_amount", "price_currency", "description", "bot_url"))
    return len([v for v in decode(card["manual_fields"], {}).values() if v]), fields, effective_media


async def group_cards(pool, ids):
    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", PERSON_LOCK):
            return False
        cards = await conn.fetch(CANDIDATE_CARDS + """
            WHERE l.id=ANY($1::bigint[]) AND l.status IN ('published','pending','duplicate_pending')
            ORDER BY l.id FOR UPDATE OF l
            """, ids)
        if len(cards) < 2:
            return False
        group_ids = {card["group_id"] for card in cards} - {None}
        if len(group_ids) > 1:
            return False
        group_id = next(iter(group_ids), None)
        existing = None
        if group_id is not None:
            existing = await conn.fetchrow("""
                SELECT g.primary_listing_id,a.member_ids FROM listing_person_groups g
                JOIN LATERAL (SELECT member_ids FROM nexus_auto_person_decisions
                              WHERE group_id=g.id AND undone_at IS NULL ORDER BY id DESC LIMIT 1) a ON true
                WHERE g.id=$1 FOR UPDATE OF g
                """, group_id)
            if not existing:
                return False
            members = await conn.fetch("SELECT listing_id FROM listing_person_group_members WHERE group_id=$1", group_id)
            if any(member["listing_id"] not in ids for member in members):
                return False
        ids = [card["id"] for card in cards]
        evidence = person_evidence(cards)
        if not evidence or await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM listing_duplicate_decisions WHERE decision='keep_both'
                AND left_listing_id=ANY($1::bigint[]) AND right_listing_id=ANY($1::bigint[])
                UNION ALL
                SELECT 1 FROM nexus_auto_person_decisions WHERE undone_at IS NOT NULL AND member_ids && $1::bigint[]
            )
            """, ids):
            return False
        published = [card for card in cards if card["status"] == "published"]
        primary = next((card for card in published if existing and card["id"] == existing["primary_listing_id"]), None)
        if primary is None and published:
            primary = min(published, key=lambda c: (c["published_at"] or c["created_at"], c["id"]))
        if primary is None:
            primary = max(cards, key=lambda c: (*card_quality(c), c["posted_at"] or c["created_at"], c["id"]))
        if existing and existing["primary_listing_id"] == primary["id"] and existing["member_ids"] == ids:
            return False
        if group_id is None:
            group_id = await conn.fetchval("INSERT INTO listing_person_groups(display_name) VALUES ($1) RETURNING id",
                                           primary["nickname"] or "")
        await conn.execute("UPDATE listing_person_groups SET primary_listing_id=$2 WHERE id=$1", group_id, primary["id"])
        await conn.execute("""
            INSERT INTO listing_person_group_members(group_id,listing_id)
            SELECT $1, unnest($2::bigint[]) ON CONFLICT (listing_id) DO NOTHING
            """, group_id, ids)
        if await conn.fetchval("SELECT count(*) FROM listing_person_group_members WHERE group_id=$1", group_id) != len(ids):
            raise RuntimeError("Group membership changed concurrently")
        evidence["quality"] = {card["id"]: card_quality(card) for card in cards}
        evidence["primary_reason"] = "stable_published" if published else "most_complete"
        evidence["primary_promoted_to_review"] = primary["status"] == "duplicate_pending"
        if evidence["primary_promoted_to_review"]:
            await conn.execute("UPDATE listings SET status='pending',updated_at=now() WHERE id=$1", primary["id"])
        await conn.execute("""
            INSERT INTO nexus_auto_person_decisions(group_id,primary_listing_id,member_ids,evidence)
            VALUES ($1,$2,$3,$4::jsonb)
            """, group_id, primary["id"], ids, json.dumps(evidence))
    return True


async def group_pending(pool):
    groups = await pool.fetch("""
        WITH candidates AS (
            SELECT l.id, l.status, nexus_tg_username(l.contact_username) AS username,
                   CASE WHEN l.contact_peer_id>0 THEN l.contact_peer_id END AS peer, m.group_id,
                   l.id=g.primary_listing_id AND l.status='published' AS published_primary
            FROM listings l LEFT JOIN listing_person_group_members m ON m.listing_id=l.id
            LEFT JOIN listing_person_groups g ON g.id=m.group_id
            WHERE l.status IN ('published','pending','duplicate_pending')
        ), keys AS (
            SELECT c.*, k.key FROM candidates c CROSS JOIN LATERAL
            (VALUES ('peer:'||peer),('tg:'||username)) k(key) WHERE k.key IS NOT NULL
        )
        SELECT array_agg(id ORDER BY id) AS ids FROM keys GROUP BY key
        HAVING count(*) BETWEEN 2 AND 100
           AND (bool_or(group_id IS NULL)
                OR (bool_or(status='published') AND NOT COALESCE(bool_or(published_primary),false)))
        ORDER BY key
        """)
    changed = 0
    for group in groups:
        changed += await group_cards(pool, group["ids"])
        if changed >= 25:
            break
    return changed


async def undo_group(conn, group_id, actor_id):
    await conn.execute("SELECT pg_advisory_xact_lock($1)", PERSON_LOCK)
    decision = await conn.fetchrow("""
        SELECT member_ids FROM nexus_auto_person_decisions
        WHERE group_id=$1 AND undone_at IS NULL ORDER BY id DESC LIMIT 1
        """, group_id)
    if not decision:
        return False
    await conn.fetch("SELECT id FROM listings WHERE id=ANY($1::bigint[]) ORDER BY id FOR UPDATE", decision["member_ids"])
    current = await conn.fetchval("SELECT array_agg(listing_id ORDER BY listing_id) FROM listing_person_group_members WHERE group_id=$1", group_id)
    if current != decision["member_ids"]:
        return False
    await conn.execute("""
        UPDATE listings SET status='duplicate_pending',updated_at=now()
        WHERE status='pending' AND id IN (
            SELECT primary_listing_id FROM nexus_auto_person_decisions
            WHERE group_id=$1 AND undone_at IS NULL AND evidence->>'primary_promoted_to_review'='true'
        )
        """, group_id)
    await conn.execute("DELETE FROM listing_person_groups WHERE id=$1", group_id)
    await conn.execute("""
        UPDATE nexus_auto_person_decisions SET undone_at=now(),undone_by=$2
        WHERE group_id=$1 AND undone_at IS NULL
        """, group_id, actor_id)
    return True


def photo_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 24 * 1024 * 1024:
        return None
    return path


def fingerprint(root, relative, similar=False):
    try:
        path = photo_path(root, relative)
        if path is None:
            return None
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if not similar:
            return digest
        with Image.open(path) as image:
            if image.width * image.height > 24_000_000 or min(image.size) < 64:
                return None
            gray = ImageOps.exif_transpose(image).convert("L")
            pixels = list(gray.resize((9, 8), Image.Resampling.LANCZOS).getdata())
        if max(pixels) - min(pixels) < 35:
            return digest, None
        differences = [pixels[y * 9 + x] > pixels[y * 9 + x + 1] for y in range(8) for x in range(8)]
        return digest, sum(value << i for i, value in enumerate(differences))
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def compare_photos(root, left, right):
    def hashes(items):
        paths = dict.fromkeys(m["path"] for m in items if m.get("kind") == "photo" and m.get("path"))
        return {result for path in list(paths)[:12] if (result := fingerprint(root, path, True)) is not None}

    left_hashes, right_hashes = hashes(left), hashes(right)
    exact = {digest for digest, _ in left_hashes} & {digest for digest, _ in right_hashes}
    remaining = {digest: bits for digest, bits in right_hashes if bits is not None and digest not in exact}
    near = 0
    for digest, bits in sorted(left_hashes):
        if bits is None or digest in exact:
            continue
        matches = [(value ^ bits).bit_count() for value in remaining.values()]
        if matches and min(matches) <= 5:
            del remaining[list(remaining)[matches.index(min(matches))]]
            near += 1
    return {"exact_images": len(exact), "near_images": near}


def text_similarity(left, right):
    a, b = (normalized(HANDLE.sub("", value or ""))[:2000] for value in (left, right))
    return round(SequenceMatcher(None, a, b).ratio(), 2) if min(len(a), len(b)) >= 35 else None


def ocr_decision(row, manual_category):
    if manual_category:
        return manual_category, "human_exact_image"
    score = float(row["predicted_confidence"] or 0)
    category = row["predicted_classification"]
    if row["predicted_model_sha"] != MODEL_SHA or score < 0.99:
        return None
    fields = decode(row["fields_json"], {})
    text = row["normalized_text"] or row["raw_text"] or ""
    handles = HANDLE.findall(text)
    has_business_fields = any(fields.get(key) for key in ("price_text", "location_text", "stage_name", "contact_handles"))
    if category == "info_card" and CARD_WORDS.search(text) and has_business_fields:
        return category, "vision4_with_structured_card_fields"
    if category == "profile_photo" and score >= 0.995 and not has_business_fields and not handles and len(normalized(text)) <= 12:
        return category, "vision4_photo_without_business_text"
    if category == "sixiang" and REPORT_WORDS.search(text) and not handles:
        return category, "vision4_with_report_text"
    return None


async def card_fields_conflict(conn, row):
    fields = decode(row["fields_json"], {})
    linked = await conn.fetch("""
        SELECT contact_username,nickname,district,price_amount,price_currency FROM listings
        WHERE status IN ('published','pending','duplicate_pending')
          AND (id=$1 OR source_msg_id=$2 OR profile_collection_job_id IN
               (SELECT job_id FROM profile_collection_results WHERE id=$3))
        """, row["listing_id"], row["raw_id"], row["profile_result_id"])
    for card in linked:
        parsed = structured_ocr_candidate(
            fields, decode(row["evidence_json"], {}), ocr_id=row["id"],
            engine_version=row["engine_version"], confidence=float(row["confidence"] or 0),
            text_identity=card["contact_username"] or "",
        )
        if parsed["conflicts"] or any(
            card[key] not in (None, "") and not equivalent_field(key, card[key], parsed["fields"][key])
            for key in ("nickname", "district", "price_amount", "price_currency") if key in parsed["fields"]
        ):
            return True
    return len({h.casefold() for h in HANDLE.findall(row["raw_text"] or "") if not h.lower().endswith("bot")}) > 1


async def apply_ocr(pool, root, asset_id, relative):
    sha = await asyncio.to_thread(fingerprint, root, relative)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("""
            SELECT * FROM listing_media_ocr WHERE id=$1 AND status='done' AND review_status='pending'
            AND human_classification IS NULL AND auto_checked_at IS NULL FOR UPDATE
            """, asset_id)
        if row is None or row["media_path"] != relative:
            return False
        manual = await conn.fetch("""
            SELECT min(label.category_code) AS category, count(DISTINCT label.category_code) AS distinct_categories
            FROM listing_media_ocr sibling JOIN ocr_classification_labels label ON label.ocr_id=sibling.id
            JOIN ocr_classification_categories c ON c.code=label.category_code AND c.is_active
            WHERE sibling.asset_sha256=$1 AND sibling.id<>$2 AND label.label_source='manual'
              AND label.superseded_at IS NULL
            """, sha, asset_id)
        await conn.execute("UPDATE listing_media_ocr SET asset_sha256=$2,auto_checked_at=now() WHERE id=$1", asset_id, sha)
        if not sha or manual[0]["distinct_categories"] > 1 or await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM nexus_auto_ocr_decisions WHERE ocr_id=$1)", asset_id,
        ):
            return False
        decision = ocr_decision(row, manual[0]["category"])
        if not decision or (decision[0] == "info_card" and await card_fields_conflict(conn, row)):
            return False
        category, reason = decision
        status = "confirmed" if category == "info_card" else "not_card"
        await conn.execute("UPDATE listing_media_ocr SET classification=$2,review_status=$3,updated_at=now() WHERE id=$1",
                           asset_id, category, status)
        await conn.execute("""
            INSERT INTO nexus_auto_ocr_decisions(ocr_id,category,previous_category,reason,model_sha,score,asset_sha256)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """, asset_id, category, row["classification"], reason, row["predicted_model_sha"], row["predicted_confidence"], sha)
    return True


async def record_learning(conn, asset_id, sha):
    if not sha:
        return
    await conn.execute("UPDATE listing_media_ocr SET asset_sha256=$2 WHERE id=$1", asset_id, sha)
    await conn.execute("""
        UPDATE listing_media_ocr SET auto_checked_at=NULL
        WHERE asset_sha256=$1 AND id<>$2 AND review_status='pending' AND human_classification IS NULL
        """, sha, asset_id)


async def run(pool, media_dir, stop):
    root = Path(media_dir).resolve()
    while not stop.is_set():
        try:
            if await pool.fetchval("SELECT COALESCE((SELECT value FROM app_settings WHERE key='review_auto_group'),'1')='1'"):
                await group_pending(pool)
            if await pool.fetchval("SELECT COALESCE((SELECT value FROM app_settings WHERE key='review_auto_ocr'),'1')='1'"):
                labels = await pool.fetch("""
                    SELECT id,media_path FROM listing_media_ocr WHERE human_classification IS NOT NULL
                    AND asset_sha256 IS NULL AND auto_checked_at IS NULL ORDER BY id LIMIT 24
                    """)
                for label in labels:
                    sha = await asyncio.to_thread(fingerprint, root, label["media_path"])
                    async with pool.acquire() as conn, conn.transaction():
                        await conn.execute("UPDATE listing_media_ocr SET auto_checked_at=now() WHERE id=$1", label["id"])
                        await record_learning(conn, label["id"], sha)
                pending = await pool.fetch("""
                    SELECT id,media_path FROM listing_media_ocr WHERE status='done' AND review_status='pending'
                    AND auto_checked_at IS NULL AND human_classification IS NULL ORDER BY id LIMIT 40
                    """)
                for asset in pending:
                    await apply_ocr(pool, root, asset["id"], asset["media_path"])
        except Exception:
            logger.exception("Automatic review cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass
