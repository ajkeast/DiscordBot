"""Live smoke tests for Postgres connectivity. Requires SQL_* env vars."""

import os

import pytest
from dotenv import load_dotenv

from tests.reporting import SECTION_LIVE_DB

load_dotenv()

pytestmark = pytest.mark.live

_SQL_VARS = ("SQL_HOST", "SQL_USER", "SQL_PASSWORD", "SQL_DATABASE")


@pytest.fixture(scope="module")
def sql_configured():
    missing = [var for var in _SQL_VARS if not os.getenv(var)]
    if missing:
        pytest.fail(f"Missing SQL env vars: {', '.join(missing)}")
    return {var: os.getenv(var) for var in _SQL_VARS}


def test_postgres_connection(report, sql_configured):
    import psycopg

    host = sql_configured["SQL_HOST"]
    port = 5432
    if ":" in host:
        host, port_str = host.rsplit(":", 1)
        port = int(port_str)

    with psycopg.connect(
        host=host,
        port=port,
        user=sql_configured["SQL_USER"],
        password=sql_configured["SQL_PASSWORD"],
        dbname=sql_configured["SQL_DATABASE"],
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            value = cursor.fetchone()[0]

    report.record("host", sql_configured["SQL_HOST"], sql_configured["SQL_HOST"], section=SECTION_LIVE_DB)
    report.record("database", sql_configured["SQL_DATABASE"], sql_configured["SQL_DATABASE"], section=SECTION_LIVE_DB)
    report.record("SELECT 1", 1, value, section=SECTION_LIVE_DB)
    assert value == 1


def test_firstlist_id_readable(report, sql_configured):
    from utils.db import db_ops

    df = db_ops.get_table_data("firstlist_id")
    columns = list(df.columns)

    report.record("table", "firstlist_id", "firstlist_id", section=SECTION_LIVE_DB)
    report.record("columns", "user_id, timesent", columns, section=SECTION_LIVE_DB)
    report.record("row count", ">= 0", len(df), section=SECTION_LIVE_DB)

    assert "user_id" in df.columns
    assert "timesent" in df.columns
