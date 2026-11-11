"""Database regressions for the admin Lifecycle review transaction."""
import asyncio
import os
import unittest
from urllib.parse import urlparse
from unittest.mock import patch

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None

from app.api import rules


DATABASE_URL = os.environ.get("NEXUS_TEST_DATABASE_URL", "")


@unittest.skipUnless(
    asyncpg is not None and DATABASE_URL,
    "requires an isolated nexus_test PostgreSQL and asyncpg",
)
class LifecycleReview2DTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_is_single_winner_and_archives_only_selected_card(self):
        if urlparse(DATABASE_URL).path.rsplit("/", 1)[-1] != "nexus_test":
            self.fail("NEXUS_TEST_DATABASE_URL must target nexus_test")
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        source_id = raw_id = event_id = None
        listing_ids = []
        try:
            suffix = str(os.getpid())
            source_id = await pool.fetchval(
                "INSERT INTO ingest_sources(peer_id,title,username) VALUES($1,'Lifecycle fixture',$2) RETURNING id",
                -970000000 - os.getpid(), f"lifecycle{suffix}",
            )
            raw_id = await pool.fetchval(
                "INSERT INTO raw_messages(source_id,platform_msg_id,raw_text,media_json) "
                "VALUES($1,$2,'已售 @lifecycle_target','[]'::jsonb) RETURNING id",
                source_id, abs(hash(suffix)) % 1000000000,
            )
            for username in ("lifecycle_target", "lifecycle_target", "different_target"):
                listing_ids.append(await pool.fetchval(
                    "INSERT INTO listings(contact_username,nickname,status) VALUES($1,'Fixture','published') RETURNING id",
                    username,
                ))
            event_id = await pool.fetchval(
                "SELECT extract_lifecycle_review_enqueue($1,NULL,123,'已售','lifecycle_target','explicit_tg',"
                "'{\"basis\":\"explicit_username\"}'::jsonb)", raw_id,
            )

            with patch.object(rules, "_pool", return_value=pool):
                queue = await rules.list_lifecycle_review_events(
                    status="pending", source_id=source_id, limit=10, offset=0,
                )
            self.assertEqual(len(queue["items"]), 1)
            self.assertEqual(queue["items"][0]["id"], event_id)
            self.assertEqual(queue["items"][0]["source_title"], "Lifecycle fixture")
            self.assertEqual(queue["items"][0]["identity_evidence"]["basis"], "explicit_username")
            self.assertEqual(len(queue["items"][0]["candidates"]), 2)

            with self.assertRaises(asyncpg.PostgresError):
                await pool.fetchval(
                    "SELECT lifecycle_review_resolve($1,'approve',$2,'admin-a','wrong target')",
                    event_id, listing_ids[2],
                )
            self.assertEqual(await pool.fetchval(
                "SELECT status FROM lifecycle_review_events WHERE id=$1", event_id,
            ), "pending")

            outcomes = await asyncio.gather(*[
                pool.fetchval(
                    "SELECT lifecycle_review_resolve($1,'approve',$2,$3,'reviewed')",
                    event_id, listing_ids[0], f"admin-{index}",
                ) for index in range(2)
            ])
            self.assertEqual(sorted(outcomes), [False, True])
            statuses = await pool.fetch(
                "SELECT id,status FROM listings WHERE id=ANY($1::bigint[]) ORDER BY id", listing_ids,
            )
            self.assertEqual([row["status"] for row in statuses], ["archived", "published", "published"])
            event = await pool.fetchrow(
                "SELECT status,resolved_listing_id,reviewed_by,reviewed_at FROM lifecycle_review_events WHERE id=$1",
                event_id,
            )
            self.assertEqual(event["status"], "approved")
            self.assertEqual(event["resolved_listing_id"], listing_ids[0])
            self.assertIsNotNone(event["reviewed_by"])
            self.assertIsNotNone(event["reviewed_at"])
        finally:
            if event_id:
                await pool.execute("DELETE FROM lifecycle_review_events WHERE id=$1", event_id)
            if listing_ids:
                await pool.execute("DELETE FROM listings WHERE id=ANY($1::bigint[])", listing_ids)
            if raw_id:
                await pool.execute("DELETE FROM raw_messages WHERE id=$1", raw_id)
            if source_id:
                await pool.execute("DELETE FROM ingest_sources WHERE id=$1", source_id)
            await pool.close()


if __name__ == "__main__":
    unittest.main()
