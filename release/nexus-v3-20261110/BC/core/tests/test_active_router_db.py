"""PostgreSQL integration tests for the additive Active Router review queue."""
import os
import ast
import shutil
import subprocess
import unittest
import uuid
from urllib.parse import urlparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("NEXUS_TEST_DATABASE_URL", "")
PARSED = urlparse(DATABASE_URL)
PSQL = shutil.which("psql")


@unittest.skipUnless(DATABASE_URL and PSQL, "requires isolated nexus_test PostgreSQL and psql")
class ActiveRouterDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PARSED.path.rsplit("/", 1)[-1] != "nexus_test":
            raise AssertionError("NEXUS_TEST_DATABASE_URL must target disposable database nexus_test")
        cls.schema = "active_router_test_" + uuid.uuid4().hex[:12]
        cls.run_sql(f"CREATE SCHEMA {cls.schema};")
        cls.run_sql(f"SET search_path TO {cls.schema};"
                    "CREATE TABLE ingest_sources(id BIGINT PRIMARY KEY,title TEXT NOT NULL,router_mode TEXT);"
                    "CREATE TABLE site_users(id BIGINT PRIMARY KEY);"
                    "CREATE TABLE raw_messages(id BIGINT PRIMARY KEY,source_id BIGINT NOT NULL "
                    "REFERENCES ingest_sources(id),extract_state TEXT,posted_at TIMESTAMPTZ);"
                    "CREATE TABLE rule_shadow_comparisons(id BIGSERIAL PRIMARY KEY,source_id BIGINT,"
                    "observed_at TIMESTAMPTZ,raw_id BIGINT,actual_json JSONB,predicted_json JSONB,changed BOOLEAN);"
                    "CREATE TABLE rule_shadow_error_counts(source_id BIGINT,error_class TEXT,occurrences INT,"
                    "last_occurred_at TIMESTAMPTZ);"
                    "CREATE TABLE listings(id BIGSERIAL PRIMARY KEY,status TEXT,source_msg_id BIGINT,"
                    "contact_username TEXT,nickname TEXT,district TEXT,price_amount NUMERIC,price_currency TEXT,"
                    "description TEXT,media_json JSONB DEFAULT '[]',manual_fields JSONB DEFAULT '{}',"
                    "evidence_json JSONB DEFAULT '{}',custom_fields JSONB DEFAULT '{}',confidence NUMERIC,"
                    "published_at TIMESTAMPTZ);"
                    "CREATE TABLE raw_message_revisions(id BIGINT,raw_id BIGINT,revision INT);"
                    "CREATE TABLE rule_parse_runs(raw_revision_id BIGINT,run_reason TEXT,created_at TIMESTAMPTZ);"
                    "CREATE TABLE source_rule_bindings(source_id BIGINT,rule_id BIGINT,priority INT,rule_version_id BIGINT);"
                    "CREATE TABLE ingest_rules(id BIGINT,rule_type TEXT,is_active BOOLEAN);"
                    "CREATE TABLE rule_instance_versions(id BIGINT,config_json JSONB,config_hash TEXT);"
                    "CREATE TABLE source_rule_binding_policies(source_id BIGINT,rule_id BIGINT,purpose TEXT,"
                    "match_group TEXT,match_mode TEXT,fallback_order INT,enabled BOOLEAN);"
                    "CREATE FUNCTION rule_shadow_bindings_for_source(BIGINT) RETURNS TABLE(rule_id BIGINT,"
                    "rule_type VARCHAR(24),is_active BOOLEAN,is_default BOOLEAN,priority INT,"
                    "rule_version_id BIGINT,config_json JSONB,purpose VARCHAR(64),match_group VARCHAR(64),"
                    "mode VARCHAR(32),fallback_order INT,config_hash VARCHAR(64)) LANGUAGE SQL AS "
                    "$$ SELECT NULL::BIGINT,NULL::VARCHAR(24),NULL::BOOLEAN,NULL::BOOLEAN,NULL::INT,"
                    "NULL::BIGINT,NULL::JSONB,NULL::VARCHAR(64),NULL::VARCHAR(64),NULL::VARCHAR(32),"
                    "NULL::INT,NULL::VARCHAR(64) WHERE FALSE $$;")
        migration = (ROOT / "scripts/migrations/2026-11-10-active-router-review-queue.sql").read_text()
        cls.run_sql(f"SET search_path TO {cls.schema};" + migration + migration)

    @classmethod
    def tearDownClass(cls):
        cls.run_sql(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE;")

    @classmethod
    def run_sql(cls, sql):
        result = subprocess.run(
            [PSQL, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", DATABASE_URL],
            input=sql, text=True, capture_output=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout

    def test_review_queue_insert_is_idempotent_and_keeps_resolved_state(self):
        base = f"SET search_path TO {self.schema};"
        self.run_sql(base + "TRUNCATE rule_active_review_events,rule_active_review_queue,"
                     "rule_shadow_comparisons,rule_shadow_error_counts,raw_messages,ingest_sources,"
                     "listings RESTART IDENTITY CASCADE;")
        self.run_sql(base + "INSERT INTO ingest_sources(id,title,router_mode) VALUES(2,'synthetic','legacy');"
                     "INSERT INTO raw_messages(id,source_id) VALUES(101,2);")
        insert = (ROOT / "core/app/sql/rule_active_review_upsert.sql").read_text()
        insert = insert.replace("$1", "2").replace("$2", "101").replace("$3", "'hash-a'")
        insert = insert.replace("$4", "'identity_unresolved'").replace("$5", "ARRAY[3,4]")
        insert = insert.replace("$6::jsonb", "'{\"reason\":\"identity_unresolved\"}'::jsonb")
        insert = insert.replace("$7::jsonb", "'{\"fields\":{}}'::jsonb")
        self.run_sql(base + insert + insert)
        count = self.run_sql(base + "SELECT count(*) FROM rule_active_review_queue;").strip()
        self.assertEqual(count, "1")
        self.run_sql(base + "UPDATE rule_active_review_queue SET status='resolved' WHERE raw_id=101;")
        self.run_sql(base + insert)
        row = self.run_sql(base + "SELECT status || ':' || reason FROM rule_active_review_queue WHERE raw_id=101;").strip()
        self.assertEqual(row, "resolved:identity_unresolved")

    def test_review_decision_creates_only_pending_listing_and_audits_actor(self):
        base = f"SET search_path TO {self.schema};"
        self.run_sql(base + "TRUNCATE rule_active_review_events,rule_active_review_queue,"
                     "rule_shadow_comparisons,rule_shadow_error_counts,raw_messages,ingest_sources,"
                     "listings RESTART IDENTITY CASCADE;")
        self.run_sql(base + "INSERT INTO site_users(id) VALUES(91) ON CONFLICT DO NOTHING;"
                     "INSERT INTO ingest_sources(id,title,router_mode) VALUES(2,'synthetic','active') "
                     "ON CONFLICT(id) DO NOTHING;"
                     "INSERT INTO raw_messages(id,source_id,posted_at) VALUES(102,2,NOW());"
                     "INSERT INTO listings(id,status,contact_username) VALUES(800,'published','confirmeduser');"
                     "INSERT INTO rule_active_review_queue(source_id,raw_id,content_hash,reason,candidate_json) "
                     "VALUES(2,102,'hash-b','identity_unresolved',"
                     "'{\"fields\":{\"nickname\":\"Candidate\"}}');")
        self.run_sql(base + "BEGIN;"
                     "SELECT id FROM rule_active_review_queue WHERE raw_id=102 FOR UPDATE;"
                     "INSERT INTO listings(source_msg_id,contact_username,nickname,status,manual_fields,"
                     "published_at) SELECT 102,'confirmeduser','Candidate',CASE WHEN EXISTS("
                     "SELECT 1 FROM listings WHERE lower(contact_username)=lower('confirmeduser') "
                     "AND status IN ('published','pending','duplicate_pending','archived')) "
                     "THEN 'duplicate_pending' ELSE 'pending' END,'{}',NULL;"
                     "INSERT INTO rule_active_review_events(queue_id,actor_user_id,action,decision_json) "
                     "SELECT id,91,'create_pending_listing','{\"confirmed_fields\":{\"username\":\"confirmeduser\"}}' "
                     "FROM rule_active_review_queue WHERE raw_id=102;"
                     "UPDATE rule_active_review_queue SET status='resolved',reviewed_by=91,reviewed_at=NOW(),"
                     "last_action='create_pending_listing' WHERE raw_id=102; COMMIT;")
        result = self.run_sql(base + "SELECT l.status || ':' || COALESCE(l.published_at::text,'null') || ':' || "
                              "(SELECT status FROM listings WHERE id=800) || ':' || "
                              "(SELECT count(*) FROM rule_active_review_events WHERE queue_id=q.id) "
                              "FROM rule_active_review_queue q JOIN listings l ON l.source_msg_id=q.raw_id "
                              "WHERE q.raw_id=102;").strip()
        self.assertEqual(result, "duplicate_pending:null:published:1")

    def test_shadow_guard_metrics_sql_runs_against_postgresql_and_keeps_source_scope(self):
        base = f"SET search_path TO {self.schema};"
        self.run_sql(base + "TRUNCATE rule_active_review_events,rule_active_review_queue,"
                     "rule_shadow_comparisons,rule_shadow_error_counts,raw_messages,ingest_sources,"
                     "listings RESTART IDENTITY CASCADE;")
        self.run_sql(base + "INSERT INTO ingest_sources VALUES(1,'other','legacy'),(2,'source2','shadow') "
                     "ON CONFLICT(id) DO UPDATE SET title=EXCLUDED.title,router_mode=EXCLUDED.router_mode;"
                     "INSERT INTO raw_messages VALUES(1,2,'done',NOW()) ON CONFLICT(id) DO NOTHING;"
                     "INSERT INTO rule_shadow_comparisons(source_id,observed_at,raw_id,actual_json,predicted_json,changed) "
                     "VALUES(2,NOW(),1,'{}','{\"predictions\":[]}',FALSE);")
        source = (ROOT / "scripts/shadow_guard_sidecar.py").read_text()
        module = ast.parse(source)
        metrics_sql = next(ast.literal_eval(node.value) for node in module.body
                           if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == "METRICS_SQL"
                                   for target in node.targets))
        metrics_sql = metrics_sql.replace("$1::timestamptz", "'2000-01-01 00:00:00+00'::timestamptz")
        metrics_sql = metrics_sql.replace("$2", "0")
        output = self.run_sql(base + metrics_sql + ";")
        self.assertIn('"comparisons" : 1', output)
        self.assertIn('"source2_listing_status" : {}', output)


if __name__ == "__main__":
    unittest.main()
