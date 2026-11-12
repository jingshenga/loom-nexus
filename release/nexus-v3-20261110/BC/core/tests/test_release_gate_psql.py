import os
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlparse


DATABASE_URL = os.environ.get("NEXUS_TEST_DATABASE_URL", "")
PSQL = shutil.which("psql")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy-core-release.sh"


@unittest.skipUnless(DATABASE_URL and PSQL, "requires isolated nexus_test PostgreSQL and psql")
class ReleaseGatePsqlTests(unittest.TestCase):
    def test_quiet_gate_query_returns_only_missing_objects_and_propagates_sql_errors(self):
        if urlparse(DATABASE_URL).path.rsplit("/", 1)[-1] != "nexus_test":
            self.fail("NEXUS_TEST_DATABASE_URL must target disposable database nexus_test")
        script = SCRIPT.read_text()
        self.assertEqual(script.count("psql -XAtq -v ON_ERROR_STOP=1"), 2)
        self.assertIn("constraint:router_mode_previous_active", script)
        self.assertIn("constraint:router_mode_new_active", script)
        self.assertIn("table:rule_active_ocr_rechecks", script)
        self.assertIn("safety:legacy_raw_requeue_trigger_absent", script)
        self.assertIn("trigger:active_ocr_review_retry", script)
        self.assertIn("ROLLBACK_OK image_id=", script)

        good = subprocess.run(
            [PSQL, "-XAtq", "-v", "ON_ERROR_STOP=1", DATABASE_URL],
            input="BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; "
                  "SELECT 'missing' WHERE FALSE; ROLLBACK;",
            text=True, capture_output=True,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(good.stdout, "")

        bad = subprocess.run(
            [PSQL, "-XAtq", "-v", "ON_ERROR_STOP=1", DATABASE_URL],
            input="SELECT * FROM nexus_release_gate_missing_relation;",
            text=True, capture_output=True,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("does not exist", bad.stderr)


if __name__ == "__main__":
    unittest.main()
