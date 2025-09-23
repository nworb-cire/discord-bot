from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Generator
from contextlib import suppress
from urllib.parse import quote_plus

import psycopg
from psycopg import sql
import pytest
import requests
from docker.errors import DockerException
from testcontainers.postgres import PostgresContainer

POSTGRES_IMAGE = os.environ.get("TESTCONTAINERS_POSTGRES_IMAGE", "postgres:16-alpine")
API_PORT = int(os.environ.get("E2E_TEST_API_PORT", "8081"))
HEALTH_TIMEOUT = float(os.environ.get("E2E_TEST_HEALTH_TIMEOUT", "30"))

_REQUIRED_ENV = {
    "DISCORD_BOT_TOKEN": "test-token",
    "BOOKCLUB_CHANNEL_ID": "1",
    "BOOKCLUB_NOM_CHANNEL_ID": "2",
    "BOOKCLUB_RESULTS_CHANNEL_ID": "3",
    "PREDICTIONS_CHANNEL_ID": "4",
    "BOOKCLUB_ROLE_ID": "5",
    "VOTE_WEIGHT_INNER": "10",
    "VOTE_WEIGHT_OUTER": "5",
    "BALLOT_SIZE": "3",
    "REDIS_URL": "redis://localhost:6379/0",
    "OPENAI_API_KEY": "test-openai",
}


@pytest.fixture(scope="session")
def base_test_environment() -> Generator[dict[str, str], None, None]:
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in _REQUIRED_ENV}
    os.environ.update(_REQUIRED_ENV)
    try:
        yield _REQUIRED_ENV
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    try:
        container = PostgresContainer(POSTGRES_IMAGE)
        container.start()
    except DockerException as exc:
        pytest.skip(f"Docker unavailable for e2e tests: {exc}")
    except Exception as exc:
        pytest.skip(f"Unable to start Postgres container: {exc}")

    try:
        yield container
    finally:
        with suppress(Exception):
            container.stop()


@pytest.fixture(scope="session")
def worker_id(request: pytest.FixtureRequest) -> str:
    worker_input = getattr(request.config, "workerinput", None)
    if worker_input is None:
        return "master"
    return worker_input.get("workerid", "master")


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer, worker_id: str) -> Generator[str, None, None]:
    params = _connection_parameters(postgres_container)
    db_name = f"test_{worker_id}_{uuid.uuid4().hex[:8]}"
    admin_url = _build_sync_url(params, params["dbname"])
    _create_database(admin_url, db_name)
    async_url = _build_async_url(params, db_name)
    try:
        yield async_url
    finally:
        _drop_database(admin_url, db_name)


@pytest.fixture(scope="session")
def sync_database_url(database_url: str) -> str:
    if "+" in database_url:
        driver, rest = database_url.split("+", 1)
        return f"{driver}://{rest.split('://', 1)[1]}"
    return database_url


@pytest.fixture(scope="session")
def migrated_database(database_url: str, base_test_environment: dict[str, str]) -> Generator[str, None, None]:
    env = {**os.environ, **base_test_environment, "DATABASE_URL": database_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)
    yield database_url


@pytest.fixture(scope="session")
def api_base_url(migrated_database: str, base_test_environment: dict[str, str]) -> Generator[str, None, None]:
    env = {**os.environ, **base_test_environment, "DATABASE_URL": migrated_database, "PORT": str(API_PORT)}
    process = subprocess.Popen(
        [sys.executable, "-m", "bot.api"],
        env=env,
    )
    base_url = f"http://127.0.0.1:{API_PORT}"
    try:
        _wait_for_health(f"{base_url}/health")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture(autouse=True)
def truncate_database(sync_database_url: str) -> Generator[None, None, None]:
    yield
    _truncate_all_tables(sync_database_url)


@pytest.fixture
def db_connection(sync_database_url: str) -> Generator[psycopg.Connection, None, None]:
    with psycopg.connect(sync_database_url, autocommit=True) as conn:
        yield conn


def _connection_parameters(container: PostgresContainer) -> dict[str, str]:
    url = container.get_connection_url()
    connection = psycopg.conninfo.conninfo_to_dict(url)
    if connection.get("dbname") is None:
        connection["dbname"] = container.dbname
    return {
        "user": connection["user"],
        "password": connection["password"],
        "host": connection["host"],
        "port": str(connection["port"]),
        "dbname": connection["dbname"],
    }


def _build_sync_url(params: dict[str, str], db_name: str) -> str:
    return psycopg.conninfo.make_conninfo(
        user=params["user"],
        password=params["password"],
        host=params["host"],
        port=params["port"],
        dbname=db_name,
    )


def _build_async_url(params: dict[str, str], db_name: str) -> str:
    user = quote_plus(params["user"])
    password = quote_plus(params["password"])
    return (
        "postgresql+asyncpg://"
        f"{user}:{password}@{params['host']}:{params['port']}/{db_name}"
    )


def _create_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def _drop_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def _truncate_all_tables(sync_url: str) -> None:
    with psycopg.connect(sync_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public' AND tablename <> 'alembic_version'
                """
            )
            tables = [row[0] for row in cur.fetchall()]
            if not tables:
                return
            cur.execute(
                sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier(table) for table in tables)
                )
            )


def _wait_for_health(url: str) -> None:
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("API did not become healthy before timeout")
