from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from mashup.extension.assets import Assets
from mashup.extension.models import Approval, Composition, canonical_hash, now
from mashup.extension.templates import approve, propose, validate

MAX_PENDING = 5
MAX_RENDER_SECONDS = 180
IDENTIFIER = re.compile(r"[a-f0-9-]{36}\Z")


class Jobs:
    def __init__(self, state: Path):
        self.state = state.expanduser().resolve()
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.state / "extension.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, edit TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL, edit TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'queued','running','succeeded','failed','cancelled'
                    )),
                    created TEXT NOT NULL, started REAL, finished TEXT, worker_pid INTEGER,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT, result TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_render
                    ON jobs(state) WHERE state='running';
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def plan(self, composition: Composition, assets: Assets) -> dict:
        plan_id = str(uuid.uuid4())
        edit = propose(composition, assets, plan_id)
        with self.connection() as db:
            db.execute("INSERT INTO plans VALUES (?,?)", (plan_id, json.dumps(edit)))
        return {"planId": plan_id, "planHash": edit["template"]["planHash"], "edit": edit}

    def get_plan(self, plan_id: str) -> dict:
        if not IDENTIFIER.fullmatch(plan_id):
            raise ValueError("Invalid plan ID")
        with self.connection() as db:
            row = db.execute("SELECT edit FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("Plan not found")
        return json.loads(row["edit"])

    def validate_plan(self, plan_id: str, assets: Assets) -> dict:
        edit = self.get_plan(plan_id)
        composition = validate(edit, assets)
        return {
            "valid": True,
            "planId": plan_id,
            "planHash": edit["template"]["planHash"],
            "composition": composition.model_dump(),
            "approval": edit["approval"],
        }

    def start(
        self, plan_id: str, approval: Approval, key: str, assets: Assets, *, launch=True
    ) -> dict:
        if not 1 <= len(key) <= 160 or not key.strip():
            raise ValueError("An idempotency key of 1 to 160 characters is required")
        approved = approve(self.get_plan(plan_id), approval, assets)
        request_hash = canonical_hash({"planId": plan_id, "approval": approval.model_dump()})
        self.reconcile()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ValueError("Idempotency key already identifies a different request")
                job_id = previous["id"]
            else:
                pending = db.execute(
                    "SELECT count(*) FROM jobs WHERE state IN ('queued','running')"
                ).fetchone()[0]
                if pending >= MAX_PENDING:
                    raise ValueError("Local render queue is full (one active and four waiting)")
                job_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO jobs(id,idempotency_key,request_hash,edit,state,created) "
                    "VALUES(?,?,?,?,?,?)",
                    (job_id, key, request_hash, json.dumps(approved), "queued", now()),
                )
        if launch:
            self.launch()
        return self.get(job_id)

    def launch(self) -> None:
        self.reconcile()
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM jobs WHERE state='queued'").fetchone():
                return
        subprocess.Popen(
            [sys.executable, "-m", "mashup.extension.worker", "--state-dir", str(self.state)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env={
                "PATH": os.environ.get("PATH", os.defpath),
                "LANG": "en_US.UTF-8",
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "PYTHONNOUSERSITE": "1",
            },
        )

    def reconcile(self) -> None:
        with self.connection() as db:
            for row in db.execute(
                "SELECT id,worker_pid,started FROM jobs WHERE state='running'"
            ).fetchall():
                alive = False
                if row["worker_pid"]:
                    try:
                        os.kill(row["worker_pid"], 0)
                        alive = True
                    except (ProcessLookupError, PermissionError):
                        pass
                if not alive or time.time() - row["started"] > MAX_RENDER_SECONDS + 30:
                    db.execute(
                        "UPDATE jobs SET state=CASE WHEN cancel_requested=1 "
                        "THEN 'cancelled' ELSE 'failed' END, "
                        "finished=?,error=? WHERE id=? AND state='running'",
                        (
                            now(),
                            "Render worker stopped before committing a verified artifact",
                            row["id"],
                        ),
                    )

    def claim(self) -> dict | None:
        self.reconcile()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE jobs SET state='running',started=?,worker_pid=? WHERE id=?",
                (time.time(), os.getpid(), row["id"]),
            )
            return {
                "id": row["id"],
                "edit": json.loads(row["edit"]),
                "requestHash": row["request_hash"],
            }

    def get(self, job_id: str) -> dict:
        if not IDENTIFIER.fullmatch(job_id):
            raise ValueError("Invalid job ID")
        self.reconcile()
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("Job not found")
        return {
            "jobId": job_id,
            "state": row["state"],
            "createdAt": row["created"],
            "finishedAt": row["finished"],
            "cancelRequested": bool(row["cancel_requested"]),
            "error": row["error"],
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    def cancel(self, job_id: str) -> dict:
        self.get(job_id)
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET cancel_requested=1,state=CASE WHEN state='queued' "
                "THEN 'cancelled' ELSE state END, "
                "finished=CASE WHEN state='queued' THEN ? ELSE finished END "
                "WHERE id=? AND state IN ('queued','running')",
                (now(), job_id),
            )
        return self.get(job_id)

    def cancelled(self, job_id: str) -> bool:
        with self.connection() as db:
            row = db.execute(
                "SELECT state,cancel_requested FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        return not row or bool(row["cancel_requested"]) or row["state"] != "running"

    def finish(self, job_id: str, *, result: dict | None = None, error: str | None = None) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,cancel_requested FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row["state"] != "running":
                return
            state = "cancelled" if row["cancel_requested"] else "failed" if error else "succeeded"
            db.execute(
                "UPDATE jobs SET state=?,finished=?,error=?,result=? WHERE id=?",
                (
                    state,
                    now(),
                    error if state == "failed" else None,
                    json.dumps(result) if state == "succeeded" else None,
                    job_id,
                ),
            )

    def result(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["state"] != "succeeded":
            raise ValueError(f"Job has no published result ({job['state']})")
        return job["result"]
