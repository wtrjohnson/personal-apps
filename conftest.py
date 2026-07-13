"""Pytest bootstrap: make `import app` safe and DB-free during tests.

The app module runs init_db() at import time when DATABASE_URL is set; JOS_SKIP_DB_INIT
short-circuits that so the pure-function suite never touches a database.
"""
import os
import sys

os.environ.setdefault("JOS_SKIP_DB_INIT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
