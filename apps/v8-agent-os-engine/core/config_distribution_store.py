"""Domain job journal in the existing Engine database, including command deduplication."""
from __future__ import annotations

import json
from copy import deepcopy

from fastapi import HTTPException
from core.database import db
from core.time_truth import utc_now_iso


class DistributionStore:
    def __init__(self, database=None):
        self.db = database or db
        self.initialized = False

    def initialize(self):
        if self.initialized:
            return
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS config_distribution_jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS config_distribution_receipts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS config_distribution_commands (owner TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, job_id TEXT NOT NULL, PRIMARY KEY(owner,id))")
            conn.commit()
        self.initialized = True

    def get(self, job_id, owner=None):
        self.initialize()
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT owner,payload FROM config_distribution_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or (owner is not None and row["owner"] != owner):
            raise HTTPException(404, "distribution_job_not_found")
        return json.loads(row["payload"])

    def jobs(self, owner=None):
        self.initialize()
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT payload FROM config_distribution_jobs" + (" WHERE owner=?" if owner is not None else ""),
                                (owner,) if owner is not None else ()).fetchall()
        return sorted([json.loads(row[0]) for row in rows], key=lambda item: item["createdAt"], reverse=True)

    def page(self, owner, offset=0, limit=20):
        self.initialize()
        terminal = "('completed','cancelled','withdrawn','withdrawal_conflict')"
        pending = f"json_extract(payload,'$.state') NOT IN {terminal}"
        with self.db.get_connection() as conn:
            count = conn.execute(f"SELECT count(*),sum(CASE WHEN {pending} THEN 1 ELSE 0 END) FROM config_distribution_jobs WHERE owner=?", (owner,)).fetchone()
            rows = conn.execute(f"SELECT payload FROM config_distribution_jobs WHERE owner=? ORDER BY CASE WHEN {pending} THEN 0 ELSE 1 END, json_extract(payload,'$.createdAt') DESC,id LIMIT ? OFFSET ?", (owner, limit, offset)).fetchall()
        items = []
        for row in rows:
            job = json.loads(row[0])
            items.append({**{key: job[key] for key in ("jobId", "revision", "planDigest", "intent", "state", "templateId", "createdAt", "updatedAt")},
                          "summary": True, "targetCount": len(job["targets"]), "targetNames": [target["displayName"] for target in job["targets"][:3]], "targets": []})
        return {"items": items, "pendingCount": count[1] or 0, "total": count[0], "nextCursor": str(offset + limit) if offset + limit < count[0] else None}

    def mutate(self, job_id, change):
        self.initialize()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM config_distribution_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "distribution_job_not_found")
            value = json.loads(row[0])
            change(value)
            value["updatedAt"] = utc_now_iso()
            conn.execute("UPDATE config_distribution_jobs SET payload=? WHERE id=?", (json.dumps(value), job_id))
            conn.commit()
        return value

    def command(self, owner, command_id, digest, job_id, change=None, create=None):
        self.initialize()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT digest,job_id FROM config_distribution_commands WHERE owner=? AND id=?", (owner, command_id)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise HTTPException(409, "distribution_command_reused")
                row = conn.execute("SELECT payload FROM config_distribution_jobs WHERE id=?", (old["job_id"],)).fetchone()
                return json.loads(row[0])
            if create is not None:
                value = deepcopy(create)
                conn.execute("INSERT INTO config_distribution_jobs VALUES (?,?,?)", (job_id, owner, json.dumps(value)))
            else:
                row = conn.execute("SELECT payload FROM config_distribution_jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
                if not row:
                    raise HTTPException(404, "distribution_job_not_found")
                value = json.loads(row[0])
                change(value)
                value["updatedAt"] = utc_now_iso()
                conn.execute("UPDATE config_distribution_jobs SET payload=? WHERE id=?", (json.dumps(value), job_id))
            conn.execute("INSERT INTO config_distribution_commands VALUES (?,?,?,?)", (owner, command_id, digest, job_id))
            conn.commit()
        return value

    def receipt(self, key):
        self.initialize()
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT payload FROM config_distribution_receipts WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_receipt(self, key, value):
        self.initialize()
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO config_distribution_receipts VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (key, json.dumps(value)))
            conn.commit()
