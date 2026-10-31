import unittest

from runner.source_routing import is_flood_wait_error, select_source_routes


class SourceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {"source_id": 10, "account_id": 1, "priority": 0, "listen_source": True},
            {"source_id": 10, "account_id": 2, "priority": 1, "listen_source": True},
            {"source_id": 10, "account_id": 3, "priority": 2, "listen_source": True},
        ]

    def test_primary_account_is_preferred(self):
        routes = select_source_routes(self.candidates, {1, 2, 3})
        self.assertEqual(routes[10]["account_id"], 1)

    def test_unavailable_primary_fails_over_in_priority_order(self):
        routes = select_source_routes(self.candidates, {2, 3})
        self.assertEqual(routes[10]["account_id"], 2)

    def test_flood_wait_stops_source_without_trying_backup(self):
        routes = select_source_routes(self.candidates, {2, 3}, {1})
        self.assertNotIn(10, routes)

    def test_flood_wait_on_first_backup_stops_before_next_backup(self):
        routes = select_source_routes(self.candidates, {3}, {2})
        self.assertNotIn(10, routes)

    def test_flood_wait_detection(self):
        error = type("FloodWaitError", (Exception,), {"seconds": 30})()
        self.assertTrue(is_flood_wait_error(error))


if __name__ == "__main__":
    unittest.main()
