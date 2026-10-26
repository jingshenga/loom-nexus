"""Static SQL resource loader (same pattern as nexus-bridge).

Statements live as .sql files under app/sql/ and are read at import
time; runtime values are always passed as bound parameters ($N,
asyncpg native). No SQL text is built inside Python.
"""
from importlib import resources


def load(name: str) -> str:
    return (resources.files("app.sql") / name).read_text(encoding="utf-8")
