"""Acceptance scenario 1 against the running Compose stack.

Drives the containerized API over real HTTP, then reads the rows it wrote
straight from the stack's PostgreSQL database. Nothing is reset or dropped.

    docker compose up --build -d
    RELAY_API_URL=http://127.0.0.1:8000 uv run pytest -q test_compose_integration.py
"""

from __future__ import annotations

import os

import httpx
import pytest
from sqlalchemy import create_engine, text

from test_agent_relay import exchange_task

API_URL = os.getenv("RELAY_API_URL")
STACK_DATABASE_URL = os.getenv(
    "RELAY_STACK_DATABASE_URL", "postgresql+psycopg://relay:relay@localhost:5432/agent_relay"
)

pytestmark = pytest.mark.skipif(not API_URL, reason="set RELAY_API_URL to run against a live stack")


def test_scenario_1_against_compose_stack_is_stored_in_postgres():
    with httpx.Client(base_url=API_URL, timeout=10) as client:
        assert client.get("/ready").json() == {"status": "ready"}
        task_id = exchange_task(client)

    engine = create_engine(STACK_DATABASE_URL)
    try:
        with engine.connect() as conn:
            assert conn.dialect.name == "postgresql"
            row = conn.execute(
                text("SELECT status, output, attempt_count FROM tasks WHERE id = :id"), {"id": task_id}
            ).one()
            assert tuple(row) == ("completed", "HELLO RELAY", 1)
            outcomes = conn.execute(
                text("SELECT outcome FROM attempts WHERE task_id = :id"), {"id": task_id}
            ).scalars().all()
            assert outcomes == ["completed"]
    finally:
        engine.dispose()
