import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from runner import presence


class Channel:
    id = 12345
    access_hash = 67890


class PresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_user_entity_without_status_is_saved_as_unknown(self):
        stop = asyncio.Event()
        entity = Channel()

        class Client:
            async def get_entity(self, _username):
                stop.set()
                return entity

        class Pool:
            def __init__(self):
                self.upserts = []

            async def fetch(self, query, *args):
                if query == presence.SQL_TARGETS:
                    return [{"contact_username": "sample", "contact_peer_id": None}]
                if query == presence.SQL_UPSERT:
                    self.upserts.append(args)
                return []

        pool = Pool()
        with patch.dict(os.environ, {
            "NEXUS_BRIDGE_SIM": "0",
            "NEXUS_BRIDGE_API_ID": "1",
            "NEXUS_BRIDGE_API_HASH": "test",
            "NEXUS_BRIDGE_PRESENCE_INTERVAL": "0.01",
        }), patch("runner.presence._shared_client", new=AsyncMock(return_value=Client())):
            await presence.run_presence(pool, stop)

        self.assertEqual(pool.upserts, [(entity.id, "sample", "unknown")])


if __name__ == "__main__":
    unittest.main()
