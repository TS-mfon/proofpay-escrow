import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from web3 import Web3

DB_PATH = os.environ.get("PROOFPAY_DB_PATH", "proofpay.db")
PORT = int(os.environ.get("PORT", "8895"))
ARC_ESCROW_CONTRACT = os.environ.get("ARC_ESCROW_CONTRACT", "0xE9e9d7A274528D2B055aDe9c5f4b7f9DF639e2f7")
GENLAYER_JUDGE_CONTRACT = os.environ.get("GENLAYER_JUDGE_CONTRACT", "0xd1066738fB575067e65a3975aF6Ef9945fE1CB33")
ARC_RPC_URL = os.environ.get("ARC_RPC_URL", "https://rpc.testnet.arc.network")
ARC_CHAIN_ID = int(os.environ.get("ARC_CHAIN_ID", "5042002"))
GENLAYER_NETWORK = os.environ.get("GENLAYER_NETWORK", "studionet")
BLOCKED_EVIDENCE_HOSTS = ("x.com", "twitter.com", "instagram.com", "tiktok.com", "facebook.com")
RELAY_VERSION = "proofpay-ui-v9"
GENLAYER_CLI = os.environ.get("GENLAYER_CLI", "genlayer")
GENLAYER_PASSWORD = os.environ.get("GENLAYER_PASSWORD", "")
RELAY_PRIVATE_KEY = os.environ.get("RELAY_PRIVATE_KEY", os.environ.get("PRIVATE_KEY", ""))
RUBRIC_VERSION = os.environ.get("RUBRIC_VERSION", "v1")

ESCROW_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"internalType": "bool", "name": "accepted", "type": "bool"},
            {"internalType": "bytes32", "name": "verdictDigest", "type": "bytes32"},
        ],
        "name": "recordVerdict",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "jobId", "type": "uint256"}],
        "name": "releasePayout",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "jobId", "type": "uint256"}],
        "name": "refundBuyer",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


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
                created_at INTEGER NOT NULL,
                submission_tx_hash TEXT DEFAULT ''
            )
            """
        )
        add_column(db, "submissions", "submission_tx_hash", "TEXT DEFAULT ''")


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


def run_genlayer(args, timeout=180):
    if not shutil.which(GENLAYER_CLI):
        raise RuntimeError("GenLayer CLI is not installed on the relay.")
    stdin = f"{GENLAYER_PASSWORD}\n" if GENLAYER_PASSWORD else None
    result = subprocess.run([GENLAYER_CLI, *args], input=stdin, text=True, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "GenLayer command failed").strip())
    return result.stdout.strip()


def extract_json_candidates(raw):
    candidates = []
    stack = []
    start = None
    for index, char in enumerate(raw):
        if char == "{":
            if not stack:
                start = index
            stack.append(char)
        elif char == "}" and stack:
            stack.pop()
            if not stack and start is not None:
                candidates.append(raw[start : index + 1])
                start = None
    return candidates


def extract_json_object(raw):
    for candidate in reversed(extract_json_candidates(raw)):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise RuntimeError(f"Could not parse GenLayer verdict output: {raw}")


def maybe_extract_verdict(raw):
    try:
        data = extract_json_object(raw)
    except Exception:
        data = None
    if isinstance(data, dict) and {"accepted", "verdictDigest"}.issubset(data.keys()):
        return data
    return extract_readable_verdict(raw)


def extract_readable_verdict(raw):
    readable_matches = re.findall(r"readable:\s*'([^']+)'", raw, flags=re.S)
    for readable in reversed(readable_matches):
        if '"accepted":' not in readable or '"verdictDigest":' not in readable:
            continue
        accepted_match = re.search(r'"accepted":(true|false)', readable)
        verdict_digest_match = re.search(r'"verdictDigest":"([^"]+)"', readable)
        if not accepted_match or not verdict_digest_match:
            continue
        reason_codes_match = re.search(r'"reasonCodes":\[(.*?)\]', readable)
        reason_codes = re.findall(r'"([^"]+)"', reason_codes_match.group(1)) if reason_codes_match else []
        verdict = {
            "accepted": accepted_match.group(1) == "true",
            "summary": extract_readable_field(readable, "summary") or "GenLayer returned a verdict.",
            "reasonCodes": reason_codes,
            "evidenceDigest": extract_readable_field(readable, "evidenceDigest") or "",
            "verdictDigest": verdict_digest_match.group(1),
        }
        submission_id = extract_readable_field(readable, "submissionId")
        job_id = extract_readable_field(readable, "jobId")
        if submission_id:
            verdict["submissionId"] = submission_id
        if job_id:
            verdict["jobId"] = job_id
        return verdict
    return None


def extract_readable_field(readable, field):
    match = re.search(rf'"{re.escape(field)}":"([^"]*)"', readable)
    return match.group(1) if match else None


def evaluate_with_genlayer(job_id, submission_id, requirements, deliverable, deliverable_url, evidence_urls):
    write_output = run_genlayer(
        [
            "write",
            GENLAYER_JUDGE_CONTRACT,
            "evaluate_delivery",
            "--args",
            job_id,
            submission_id,
            requirements,
            deliverable,
            deliverable_url or "",
            json.dumps(evidence_urls),
            RUBRIC_VERSION,
        ],
        timeout=300,
    )
    tx_hash_match = re.search(r"0x[a-fA-F0-9]{64}", write_output)
    genlayer_tx_hash = tx_hash_match.group(0) if tx_hash_match else ""
    write_verdict = maybe_extract_verdict(write_output)
    if write_verdict:
        write_verdict["genlayerTxHash"] = genlayer_tx_hash
        return write_verdict
    if tx_hash_match:
        try:
            receipt_output = run_genlayer(["receipt", genlayer_tx_hash, "--status", "FINALIZED", "--retries", "60", "--interval", "3000"], timeout=240)
            receipt_verdict = maybe_extract_verdict(receipt_output)
            if receipt_verdict:
                receipt_verdict["genlayerTxHash"] = genlayer_tx_hash
                return receipt_verdict
        except Exception as exc:
            last_receipt_error = exc
        else:
            last_receipt_error = None
    else:
        last_receipt_error = "GenLayer write output did not include a transaction hash."
    last_error = None
    for _ in range(18):
        try:
            verdict_raw = run_genlayer(["call", GENLAYER_JUDGE_CONTRACT, "get_verdict", "--args", job_id], timeout=120)
            verdict = extract_json_object(verdict_raw)
            verdict["genlayerTxHash"] = genlayer_tx_hash
            return verdict
        except Exception as exc:
            last_error = exc
            time.sleep(10)
    raise RuntimeError(f"GenLayer verdict was not available after evaluation. receipt={last_receipt_error}; read={last_error}; writeOutput={write_output[:1200]}")


def bytes32_from_digest(value):
    clean = str(value or "").removeprefix("0x")
    if len(clean) == 64 and re.fullmatch(r"[0-9a-fA-F]{64}", clean):
        return "0x" + clean
    return Web3.keccak(text=str(value)).hex()


def send_arc_tx(function_name, *args):
    if not RELAY_PRIVATE_KEY:
        raise RuntimeError("Relay private key is not configured; cannot write Arc verdict or payout/refund.")
    web3 = Web3(Web3.HTTPProvider(ARC_RPC_URL))
    if not web3.is_connected():
        raise RuntimeError("Arc RPC is unavailable.")
    account = web3.eth.account.from_key(RELAY_PRIVATE_KEY)
    contract = web3.eth.contract(address=Web3.to_checksum_address(ARC_ESCROW_CONTRACT), abi=ESCROW_ABI)
    tx = getattr(contract.functions, function_name)(*args).build_transaction(
        {
            "from": account.address,
            "nonce": web3.eth.get_transaction_count(account.address),
            "chainId": ARC_CHAIN_ID,
            "gasPrice": web3.eth.gas_price,
        }
    )
    tx.setdefault("gas", web3.eth.estimate_gas(tx))
    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"Arc transaction {function_name} failed: {tx_hash.hex()}")
    return Web3.to_hex(tx_hash)


def settle_submission_async(submission_id, job_id, onchain_job_id, requirements, deliverable, deliverable_url, evidence_urls):
    try:
        verdict = evaluate_with_genlayer(job_id, submission_id, requirements, deliverable, deliverable_url, evidence_urls)
        record_tx_hash = send_arc_tx("recordVerdict", int(onchain_job_id), bool(verdict["accepted"]), bytes32_from_digest(verdict["verdictDigest"]))
        settlement_tx_hash = send_arc_tx("releasePayout" if verdict["accepted"] else "refundBuyer", int(onchain_job_id))
        verdict["recordTxHash"] = record_tx_hash
        verdict["settlementTxHash"] = settlement_tx_hash
        verdict["settlementStatus"] = "payout_released" if verdict["accepted"] else "buyer_refunded"
        status = "paid" if verdict["accepted"] else "refunded"
        accepted = 1 if verdict["accepted"] else 0
    except Exception as exc:
        verdict = {"accepted": False, "summary": f"GenLayer/Arc settlement failed: {exc}", "reasonCodes": ["SETTLEMENT_FAILED"], "verdictDigest": digest({"submissionId": submission_id, "error": str(exc)})}
        status = "settlement_failed"
        accepted = 0
    with sqlite3.connect(DB_PATH) as db:
        db.execute("UPDATE submissions SET accepted = ?, verdict = ? WHERE id = ?", (accepted, json.dumps(verdict), submission_id))
        db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


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
        if path == "/submissions":
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT * FROM submissions ORDER BY created_at DESC").fetchall()
            submissions = [
                {
                    "id": row[0],
                    "jobId": row[1],
                    "deliverable": row[2],
                    "deliverableUrl": row[3],
                    "evidenceUrls": json.loads(row[4]),
                    "accepted": row[5],
                    "verdict": json.loads(row[6]) if row[6] else None,
                    "createdAt": row[7],
                    "submissionTxHash": row[8] if len(row) > 8 else "",
                }
                for row in rows
            ]
            json_response(self, 200, {"submissions": submissions})
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
            with sqlite3.connect(DB_PATH) as db:
                job = db.execute("SELECT * FROM jobs WHERE id = ?", (payload["jobId"],)).fetchone()
            if not job:
                json_response(self, 404, {"error": "Job not found."})
                return
            onchain_job_id = payload.get("onchainJobId") or (job[9] if len(job) > 9 else "")
            if not onchain_job_id:
                json_response(self, 400, {"error": "Missing on-chain job ID; create and fund the job before submitting work."})
                return
            verdict = {
                "accepted": None,
                "summary": "GenLayer evaluation started. The relay will record the verdict on Arc and release or refund after the judge completes.",
                "reasonCodes": ["EVALUATION_STARTED"],
                "verdictDigest": digest({"submissionId": submission_id, "status": "started"}),
            }
            with sqlite3.connect(DB_PATH) as db:
                db.execute(
                    """
                    INSERT INTO submissions (
                        id, job_id, deliverable, deliverable_url, evidence_urls,
                        accepted, verdict, created_at, submission_tx_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission_id,
                        payload["jobId"],
                        payload["deliverable"],
                        payload.get("deliverableUrl", ""),
                        json.dumps(evidence_urls),
                        None,
                        json.dumps(verdict),
                        now,
                        str(payload.get("submissionTxHash", "")),
                    ),
                )
                db.execute("UPDATE jobs SET status = ? WHERE id = ?", ("evaluating", payload["jobId"]))
            threading.Thread(
                target=settle_submission_async,
                args=(submission_id, payload["jobId"], onchain_job_id, job[4], payload["deliverable"], payload.get("deliverableUrl", ""), evidence_urls),
                daemon=True,
            ).start()
            json_response(self, 202, {"submissionId": submission_id, "verdict": verdict, "settlementStatus": "started"})
            return
        json_response(self, 404, {"error": "not_found"})


if __name__ == "__main__":
    init_db()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
