import os
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlparse


DATABASE_URL = os.environ.get("NEXUS_TEST_DATABASE_URL", "")
PSQL = shutil.which("psql")
MIGRATION = Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "2026-11-11-active-router-mode-audit.sql"


@unittest.skipUnless(DATABASE_URL and PSQL, "requires isolated nexus_test PostgreSQL and psql")
class ActiveModeAuditMigrationTests(unittest.TestCase):
    def test_migration_is_idempotent_and_allows_active_audit_transitions(self):
        if urlparse(DATABASE_URL).path.rsplit("/", 1)[-1] != "nexus_test":
            self.fail("NEXUS_TEST_DATABASE_URL must target disposable database nexus_test")
        schema = "active_mode_audit_" + uuid.uuid4().hex[:12]
        setup = (
            f'CREATE SCHEMA "{schema}"; SET search_path TO "{schema}"; '
            "CREATE TABLE rule_router_mode_events ("
            "previous_mode VARCHAR(12) NOT NULL CHECK (previous_mode IN ('legacy','shadow')), "
            "new_mode VARCHAR(12) NOT NULL CHECK (new_mode IN ('legacy','shadow')));"
        )
        self.run_sql(setup)
        try:
            migration = MIGRATION.read_text()
            self.run_sql(f'SET search_path TO "{schema}";\n' + migration + migration)
            self.run_sql(
                f'SET search_path TO "{schema}"; '
                "INSERT INTO rule_router_mode_events VALUES ('legacy','active'),('active','legacy');"
            )
            result = self.run_sql(
                f'SET search_path TO "{schema}"; '
                "SELECT count(*) FROM rule_router_mode_events WHERE previous_mode='active' OR new_mode='active';"
            )
            self.assertEqual(result.strip(), "2")
        finally:
            self.run_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE;')

    @classmethod
    def run_sql(cls, sql):
        result = subprocess.run(
            [PSQL, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", DATABASE_URL],
            input=sql, text=True, capture_output=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout


if __name__ == "__main__":
    unittest.main()
