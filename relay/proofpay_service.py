import hashlib
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DB_PATH = os.environ.get("PROOFPAY_DB_PATH", "proofpay.db")
PORT = int(os.environ.get("PORT", "8895"))
ARC_ESCROW_CONTRACT = os.environ.get("ARC_ESCROW_CONTRACT", "0xE9e9d7A274528D2B055aDe9c5f4b7f9DF639e2f7")
GENLAYER_JUDGE_CONTRACT = os.environ.get("GENLAYER_JUDGE_CONTRACT", "0xd1066738fB575067e65a3975aF6Ef9945fE1CB33")
ARC_RPC_URL = os.environ.get("ARC_RPC_URL", "https://rpc.testnet.arc.network")
ARC_CHAIN_ID = int(os.environ.get("ARC_CHAIN_ID", "5042002"))
GENLAYER_NETWORK = os.environ.get("GENLAYER_NETWORK", "studionet")
BLOCKED_EVIDENCE_HOSTS = ("x.com", "twitter.com", "instagram.com", "tiktok.com", "facebook.com")
RELAY_VERSION = "proofpay-ui-v2"


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
                created_at INTEGER NOT NULL,
                onchain_job_id TEXT DEFAULT '',
                funding_tx_hash TEXT DEFAULT ''
            )
            """
        )
        add_column(db, "jobs", "onchain_job_id", "TEXT DEFAULT ''")
        add_column(db, "jobs", "funding_tx_hash", "TEXT DEFAULT ''")
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


def add_column(db, table, column, definition):
    columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def validate_optional_urls(urls):
    if not urls:
        return []
    problems = []
    for raw_url in urls:
        url = str(raw_url).strip()
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not hostname:
            problems.append({"url": url, "reason": "Use a full http(s) URL."})
            continue
        if hostname in BLOCKED_EVIDENCE_HOSTS or hostname.endswith(tuple(f".{host}" for host in BLOCKED_EVIDENCE_HOSTS)):
            problems.append({"url": url, "reason": "GenLayer may not be able to access gated/social links. Add a public mirror or raw evidence link."})
            continue
        try:
            request = Request(url, method="HEAD", headers={"User-Agent": "ProofPay-Link-Check/1.0"})
            with urlopen(request, timeout=6) as response:
                if response.status >= 400:
                    problems.append({"url": url, "reason": f"URL returned HTTP {response.status}."})
        except Exception as exc:
            problems.append({"url": url, "reason": f"GenLayer cannot access this link from the relay: {exc}"})
    return problems


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
                    "relayVersion": RELAY_VERSION,
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
                    "onchainJobId": row[9] if len(row) > 9 else "",
                    "fundingTxHash": row[10] if len(row) > 10 else "",
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
            status = "funded" if payload.get("fundingTxHash") else "created"
            with sqlite3.connect(DB_PATH) as db:
                db.execute(
                    "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        payload["buyer"],
                        payload["provider"],
                        payload["title"],
                        payload["requirements"],
                        str(payload["amount"]),
                        int(payload["deadline"]),
                        status,
                        now,
                        str(payload.get("onchainJobId", "")),
                        str(payload.get("fundingTxHash", "")),
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
            url_problems = validate_optional_urls(evidence_urls)
            if url_problems:
                json_response(self, 400, {"error": "One or more evidence links cannot be accessed by GenLayer.", "urlProblems": url_problems})
                return
            verdict = {
                "accepted": True,
                "summary": "Relay preview accepted the submission shape. Final judgement should be written through GenLayer Studionet before payout/refund.",
                "reasonCodes": ["PASS_MINIMUM_ACCEPTANCE"] if evidence_urls else ["NO_OPTIONAL_EVIDENCE_PROVIDED"],
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
