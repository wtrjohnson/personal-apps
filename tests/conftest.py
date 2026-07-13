"""Shared DB fixtures for route tests.

Route tests require a throwaway Postgres. Point JOS_TEST_DATABASE_URL at one to enable
them; without it they skip (e.g. in CI with no database). The local dev DB is spun up in
/tmp during development.
"""
import os

import pytest

TEST_DB_URL = os.environ.get("JOS_TEST_DATABASE_URL", "")


@pytest.fixture(scope="session")
def app_db():
    if not TEST_DB_URL:
        pytest.skip("JOS_TEST_DATABASE_URL not set; route tests need a Postgres")
    import app
    app.DATABASE_URL = TEST_DB_URL
    app.init_db()
    return app


@pytest.fixture()
def db(app_db):
    """Truncate all app tables before each test for isolation."""
    app = app_db
    with app.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT tablename FROM pg_tables WHERE schemaname='public'
            """)
            tables = [r["tablename"] for r in cur.fetchall()]
            if tables:
                cur.execute(
                    "TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"
                )
    return app


@pytest.fixture()
def client(db):
    c = db.app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
    return c
