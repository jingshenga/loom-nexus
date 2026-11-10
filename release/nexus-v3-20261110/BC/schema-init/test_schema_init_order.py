from pathlib import Path


def test_chat_session_username_column_precedes_cleanup_update():
    schema = Path(__file__).resolve().parents[2] / "scripts" / "schema.sql"
    sql = schema.read_text()
    add_column = "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS contact_username VARCHAR(128);"
    cleanup = "UPDATE chat_sessions SET contact_username = ltrim(contact_username, '@')"

    assert sql.index(add_column) < sql.index(cleanup)
