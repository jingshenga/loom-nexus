"""GET /api/admin/sources/forward-origins：聚合输出形状与空数据降级。"""
import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import db
from app.api import sources


class FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping

    def __getitem__(self, key):
        return self._mapping[key]


class FakePool:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [FakeRow(r) for r in self.rows]


FORWARD_ROWS = [{
    "from_id": "424242",
    "from_name": "某资料母频",
    "from_type": "channel",
    "forward_count": 12,
    "source_count": 3,
    "latest_forwarded_at": None,
    "is_already_source": True,
    "known_title": "已绑定的标题",
}]


class ForwardOriginsApiTests(unittest.TestCase):
    def run_endpoint(self, pool, **kwargs):
        with patch.object(db, "get_pool", lambda: pool):
            return asyncio.run(sources.forward_origins(**kwargs))

    def test_aggregated_rows_are_shaped_for_the_frontend(self):
        pool = FakePool(FORWARD_ROWS)
        data = self.run_endpoint(pool, limit=50)
        item = data["items"][0]
        self.assertEqual(item["from_id"], "424242")
        self.assertEqual(item["from_name"], "某资料母频")
        self.assertEqual(item["forward_count"], 12)
        self.assertEqual(item["source_count"], 3)
        self.assertIs(item["is_already_source"], True)
        self.assertIsNone(item["latest_forwarded_at"])
        # 聚合语句走 sqlres 常量，limit 以参数绑定下发
        sql, args = pool.calls[0]
        self.assertIn("forward_count", sql)
        self.assertEqual(args, (50,))

    def test_limit_is_bound_as_a_parameter(self):
        pool = FakePool([])
        self.run_endpoint(pool, limit=200)
        self.assertEqual(pool.calls[0][1], (200,))

    def test_empty_data_degrades_to_empty_items(self):
        data = self.run_endpoint(FakePool([]), limit=50)
        self.assertEqual(data["items"], [])

    def test_database_unavailable_returns_503(self):
        with patch.object(db, "get_pool", lambda: None):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sources.forward_origins(limit=50))
        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
