"""Provision one disposable local test user and exercise the existing authenticated API.

Uses the local Docker PostgreSQL socket without reading .env or displaying credentials.
Disables only the account created by this invocation when the test ends.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ONCALL_DISABLE_ENV_FILE"] = "1"

from load_test_agent import LoadTest  # noqa: E402

from app.auth.contracts import NewUser  # noqa: E402
from app.auth.password import hash_password  # noqa: E402


def database_sql(statement: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", "oncall-postgres", "sh", "-c",
         'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'],
        input=statement, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        # PostgreSQL errors can echo the INSERT, including the password hash.
        raise RuntimeError("local_test_account_database_operation_failed")
    return result.stdout.strip()


class AuthenticatedLoadTest(LoadTest):
    def __init__(self, args, username: str, password: str) -> None:
        super().__init__(args)
        self.username = username
        self.password = password

    async def login(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/api/auth/token", data={"username": self.username, "password": self.password}
        )
        if response.status_code != 200:
            raise RuntimeError(f"test_account_login_http_{response.status_code}")
        self.metadata["token_expires_in_s"] = response.json()["expires_in"]
        return response.json()["access_token"]

    async def batch(self, client, batch_id, scenarios, sample_offset=0) -> bool:
        # Refresh outside measured load; no credential files or token logging.
        client.headers["Authorization"] = "Bearer " + await self.login(client)
        return await super().batch(client, batch_id, scenarios, sample_offset)


async def monitor(test: LoadTest, done: asyncio.Event) -> None:
    samples = []
    try:
        pids = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{urlparse(test.args.base_url).port}", "-sTCP:LISTEN", "-t"], text=True
        ).split()
    except subprocess.CalledProcessError:
        pids = []
    while not done.is_set():
        if pids:
            proc = await asyncio.create_subprocess_exec(
                "ps", "-p", ",".join(pids), "-o", "pid=,%cpu=,rss=",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            for line in stdout.decode().splitlines():
                fields = line.split()
                if len(fields) == 3:
                    samples.append({"elapsed_s": round(time.perf_counter() - test.started, 2),
                                    "pid": int(fields[0]), "cpu_percent": float(fields[1]),
                                    "rss_kib": int(fields[2]), "client_inflight": test.active})
        try:
            await asyncio.wait_for(done.wait(), timeout=2)
        except TimeoutError:
            pass
    test.metadata["resource_observation"] = {
        "method": "macOS ps sampled every ~2s; CPU is ps average, not instant utilization",
        "listener_pids": pids,
        "samples": len(samples),
        "peak_rss_kib": max((s["rss_kib"] for s in samples), default=None),
        "max_observed_ps_cpu_percent": max((s["cpu_percent"] for s in samples), default=None),
    }
    test.args.output.mkdir(parents=True, exist_ok=True)
    (test.args.output / "resources.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")


async def execute(args) -> None:
    username = "loadtest_" + uuid4().hex[:12]
    user_id = uuid4()
    password = secrets.token_urlsafe(36)
    user = NewUser(username=username, password_hash=hash_password(password), role="user")
    # All interpolated values are generated locally (UUID, lowercase hex, Argon2 output).
    # The password never enters SQL or a shell command; input is piped directly to psql.
    statement = (
        "INSERT INTO users (user_id, username, password_hash, role, is_active) "
        f"VALUES ('{user_id}', '{user.username}', '{user.password_hash}', 'user', TRUE) "
        "RETURNING user_id;"
    )
    database_sql(statement)
    test = AuthenticatedLoadTest(args, username, password)
    test.metadata.update(test_username=username, test_user_id=str(user_id),
                         account_role="user", account_creation="local PostgreSQL + project Argon2",
                         account_disabled_after_test=False)
    print("Created dedicated ordinary-role test account: " + username, flush=True)
    done = asyncio.Event()
    resource_task = asyncio.create_task(monitor(test, done))
    try:
        async with httpx.AsyncClient(base_url=args.base_url, trust_env=False, timeout=10) as client:
            os.environ["ONCALL_LOAD_TEST_TOKEN"] = await test.login(client)
        await test.execute()
    except asyncio.CancelledError:
        test.stop_reason = "interrupted_by_user"
        raise
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
        test.stop_reason = "execution_error_" + type(exc).__name__
        print("Stopped: " + test.stop_reason, flush=True)
    finally:
        os.environ.pop("ONCALL_LOAD_TEST_TOKEN", None)
        done.set()
        await resource_task
        database_sql(
            "UPDATE users SET is_active=FALSE, updated_at=CURRENT_TIMESTAMP "
            f"WHERE user_id='{user_id}' AND username='{user.username}' RETURNING user_id;"
        )
        test.metadata["account_disabled_after_test"] = True
        test.save()
        print("Test account disabled; stop_reason=" + test.stop_reason, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:9900")
    parser.add_argument("--levels", type=int, nargs="+", default=[10, 30, 50, 100])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--window", type=float, default=3)
    parser.add_argument("--pause", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-requests", type=int, default=579)
    parser.add_argument("--max-duration", type=float, default=7200)
    args = parser.parse_args()
    args.token_file = None
    if args.base_url not in {"http://127.0.0.1:9900", "http://127.0.0.1:9901"}:
        parser.error("Only the local application/test instance is allowed")
    if args.output.exists():
        parser.error("Use a new output directory to preserve previous test evidence")
    if (any(n not in (10, 30, 50, 100) for n in args.levels)
            or not 1 <= args.repeats <= 3 or args.window < 0 or args.pause < 0
            or args.timeout <= 0 or args.max_requests < 9 or args.max_duration <= 0):
        parser.error("Invalid load/budget parameters")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
