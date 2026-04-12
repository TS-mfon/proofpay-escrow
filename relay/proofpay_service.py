import hashlib
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DB_PATH = os.environ.get("PROOFPAY_DB_PATH", "proofpay.db")
PORT = int(os.environ.get("PORT", "8895"))
ARC_ESCROW_CONTRACT = os.environ.get("ARC_ESCROW_CONTRACT", "0xE9e9d7A274528D2B055aDe9c5f4b7f9DF639e2f7")
GENLAYER_JUDGE_CONTRACT = os.environ.get("GENLAYER_JUDGE_CONTRACT", "0xd1066738fB575067e65a3975aF6Ef9945fE1CB33")
ARC_RPC_URL = os.environ.get("ARC_RPC_URL", "https://rpc.testnet.arc.network")
ARC_CHAIN_ID = int(os.environ.get("ARC_CHAIN_ID", "5042002"))
GENLAYER_NETWORK = os.environ.get("GENLAYER_NETWORK", "studionet")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                buyer TEXT NOT NULL,
                provider TEXT NOT NULL,
                title TEXT NOT NULL,
                requirements TEXT NOT NULL,
                amount TEXT NOT NULL,
                deadline INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                deliverable TEXT NOT NULL,
                deliverable_url TEXT NOT NULL,
                evidence_urls TEXT NOT NULL,
                accepted INTEGER,
                verdict TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )


def json_response(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "content-type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("content-length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        json_response(self, 200, {"ok": True})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            json_response(
                self,
                200,
                {
                    "ok": True,
                    "arcRpcUrl": ARC_RPC_URL,
                    "arcChainId": ARC_CHAIN_ID,
                    "arcEscrowContract": ARC_ESCROW_CONTRACT,
                    "genlayerNetwork": GENLAYER_NETWORK,
                    "genlayerJudgeContract": GENLAYER_JUDGE_CONTRACT,
                },
            )
            return
        if path == "/jobs":
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
            jobs = [
                {
                    "id": row[0],
                    "buyer": row[1],
                    "provider": row[2],
                    "title": row[3],
                    "requirements": row[4],
                    "amount": row[5],
                    "deadline": row[6],
                    "status": row[7],
                    "createdAt": row[8],
                }
                for row in rows
            ]
            json_response(self, 200, {"jobs": jobs})
            return
        json_response(self, 404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path
        payload = read_json(self)
        now = int(time.time())
        if path == "/jobs":
            required = ["buyer", "provider", "title", "requirements", "amount", "deadline"]
            missing = [key for key in required if not payload.get(key)]
            if missing:
                json_response(self, 400, {"error": f"Missing fields: {', '.join(missing)}"})
                return
            job_id = "job_" + digest({**payload, "createdAt": now})[:16]
            with sqlite3.connect(DB_PATH) as db:
                db.execute(
                    "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        payload["buyer"],
                        payload["provider"],
                        payload["title"],
                        payload["requirements"],
                        str(payload["amount"]),
                        int(payload["deadline"]),
                        "created",
                        now,
                    ),
                )
            json_response(self, 201, {"jobId": job_id, "titleHash": digest(payload["title"]), "rubricHash": digest(payload["requirements"])})
            return
        if path == "/submissions":
            required = ["jobId", "deliverable"]
            missing = [key for key in required if not payload.get(key)]
            if missing:
                json_response(self, 400, {"error": f"Missing fields: {', '.join(missing)}"})
                return
            submission_id = "sub_" + digest({**payload, "createdAt": now})[:16]
            evidence_urls = payload.get("evidenceUrls", [])
            verdict = {
                "accepted": bool(payload.get("evidenceUrls")),
                "summary": "Relay preview only. Final judgement should be written through GenLayer Studionet.",
                "reasonCodes": ["PASS_MINIMUM_ACCEPTANCE"] if evidence_urls else ["MISSING_EVIDENCE"],
                "verdictDigest": digest(payload),
            }
            with sqlite3.connect(DB_PATH) as db:
                db.execute(
                    "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        submission_id,
                        payload["jobId"],
                        payload["deliverable"],
                        payload.get("deliverableUrl", ""),
                        json.dumps(evidence_urls),
                        1 if verdict["accepted"] else 0,
                        json.dumps(verdict),
                        now,
                    ),
                )
            json_response(self, 201, {"submissionId": submission_id, "verdict": verdict})
            return
        json_response(self, 404, {"error": "not_found"})


if __name__ == "__main__":
    init_db()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
