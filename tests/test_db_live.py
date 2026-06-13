"""Live smoke tests for MySQL connectivity. Requires SQL_* env vars."""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.live

_SQL_VARS = ("SQL_HOST", "SQL_USER", "SQL_PASSWORD", "SQL_DATABASE")


@pytest.fixture(scope="module")
def sql_configured():
    missing = [var for var in _SQL_VARS if not os.getenv(var)]
    if missing:
        pytest.fail(f"Missing SQL env vars: {', '.join(missing)}")
    return {var: os.getenv(var) for var in _SQL_VARS}


def test_mysql_connection(sql_configured):
    import pymysql

    conn = pymysql.connect(
        host=sql_configured["SQL_HOST"],
        user=sql_configured["SQL_USER"],
        password=sql_configured["SQL_PASSWORD"],
        database=sql_configured["SQL_DATABASE"],
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1
    finally:
        conn.close()


def test_firstlist_id_readable(sql_configured):
    from utils.db import db_ops

    df = db_ops.get_table_data("firstlist_id")
    assert "user_id" in df.columns
    assert "timesent" in df.columns
