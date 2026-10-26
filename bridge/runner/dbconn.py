"""Shared database connection parameters (environment-driven)."""
import os


def connect_kwargs() -> dict:
    """Connection parameters passed as typed kwargs (never a URL string)."""
    return {
        "host": os.getenv("NEXUS_DB_HOST", "nexus-db"),
        "port": int(os.getenv("NEXUS_DB_PORT", "5432")),
        "user": os.getenv("NEXUS_DB_USER", "nexus"),
        "password": os.getenv("NEXUS_DB_PASSWORD", "nexus"),
        "database": os.getenv("NEXUS_DB_NAME", "nexus"),
    }
