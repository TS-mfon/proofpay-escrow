# { "Depends": "py-genlayer:test" }

import hashlib
import json
import re
from dataclasses import dataclass

from genlayer import *

ERROR_EXPECTED = "[EXPECTED]"
ERROR_TRANSIENT = "[TRANSIENT]"

ALLOWED_REASON_CODES = {
    "MISSING_DELIVERABLE",
    "MISSING_EVIDENCE",
    "PROMPT_MISMATCH",
    "UNVERIFIABLE_CLAIMS",
    "PASS_MINIMUM_ACCEPTANCE",
}


def canonical_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def keywords(value: str) -> set[str]:
    normalized = re.sub(r"[^a-zA-Z0-9 ]+", " ", value or "").lower()
    return {token for token in normalized.split() if len(token) > 3}


def canonical_codes(reason_codes: list[str]) -> list[str]:
    return sorted({code for code in reason_codes if code in ALLOWED_REASON_CODES})


def digest_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@allow_storage
@dataclass
class ProofPayVerdict:
    job_id: str
    submission_id: str
    accepted: bool
    summary: str
    evidence_digest: str
    verdict_digest: str
    reason_codes_json: str


class ProofPayJudge(gl.Contract):
    rubric_version: str
    verdicts: TreeMap[str, ProofPayVerdict]

    def __init__(self, rubric_version: str):
        self.rubric_version = rubric_version

    @gl.public.view
    def get_rubric_version(self) -> str:
        return self.rubric_version

    @gl.public.view
    def get_verdict(self, job_id: str) -> dict:
        verdict = self.verdicts[job_id]
        return {
            "jobId": verdict.job_id,
            "submissionId": verdict.submission_id,
            "accepted": verdict.accepted,
            "summary": verdict.summary,
            "evidenceDigest": verdict.evidence_digest,
            "verdictDigest": verdict.verdict_digest,
            "reasonCodes": json.loads(verdict.reason_codes_json),
        }

    @gl.public.write
    def evaluate_delivery(
        self,
        job_id: str,
        submission_id: str,
        job_requirements: str,
        deliverable_text: str,
        deliverable_url: str,
        evidence_urls_json: str,
        rubric_version: str,
    ) -> dict:
        if not job_id or not submission_id or not job_requirements:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Missing required job fields")
        if rubric_version != self.rubric_version:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Unsupported rubric version")

        def leader_fn():
            return self._evaluate_once(
                job_id,
                submission_id,
                job_requirements,
                deliverable_text,
                deliverable_url,
                evidence_urls_json,
            )

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return self._handle_leader_error(leaders_res, leader_fn)
            leader_result = leaders_res.calldata
            validator_result = leader_fn()
            return (
                validator_result["accepted"] == leader_result["accepted"]
                and canonical_codes(validator_result["reasonCodes"])
                == canonical_codes(leader_result["reasonCodes"])
            )

        verdict = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        self.verdicts[job_id] = ProofPayVerdict(
            job_id=job_id,
            submission_id=submission_id,
            accepted=verdict["accepted"],
            summary=verdict["summary"],
            evidence_digest=verdict["evidenceDigest"],
            verdict_digest=verdict["verdictDigest"],
            reason_codes_json=json.dumps(canonical_codes(verdict["reasonCodes"])),
        )
        return verdict

    def _evaluate_once(
        self,
        job_id: str,
        submission_id: str,
        job_requirements: str,
        deliverable_text: str,
        deliverable_url: str,
        evidence_urls_json: str,
    ) -> dict:
        evidence_urls = self._parse_json_array(evidence_urls_json)
        delivery_body = deliverable_text
        reason_codes: list[str] = []

        if not canonical_text(delivery_body) and not canonical_text(deliverable_url):
            reason_codes.append("MISSING_DELIVERABLE")
        if not evidence_urls:
            reason_codes.append("MISSING_EVIDENCE")
        if not delivery_body and deliverable_url:
            delivery_body = self._fetch_url_text(deliverable_url)

        requirement_terms = keywords(job_requirements)
        delivery_terms = keywords(delivery_body)
        if len(requirement_terms & delivery_terms) < 2:
            reason_codes.append("PROMPT_MISMATCH")

        evidence_matches = 0
        for evidence_url in evidence_urls:
            evidence_text = self._fetch_url_text(evidence_url)
            if len(keywords(evidence_text) & delivery_terms) > 0:
                evidence_matches += 1
        if evidence_urls and evidence_matches == 0:
            reason_codes.append("UNVERIFIABLE_CLAIMS")

        reason_codes = canonical_codes(reason_codes)
        accepted = len(reason_codes) == 0
        if accepted:
            reason_codes = ["PASS_MINIMUM_ACCEPTANCE"]
            summary = "Deliverable satisfies the ProofPay acceptance rubric."
        else:
            summary = "Deliverable does not satisfy the ProofPay acceptance rubric."

        evidence_digest = digest_payload(
            {"jobId": job_id, "deliverable": canonical_text(delivery_body), "evidenceUrls": sorted(evidence_urls)}
        )
        verdict_digest = digest_payload(
            {"submissionId": submission_id, "accepted": accepted, "reasonCodes": reason_codes}
        )
        return {
            "jobId": job_id,
            "submissionId": submission_id,
            "accepted": accepted,
            "summary": summary,
            "reasonCodes": reason_codes,
            "evidenceDigest": evidence_digest,
            "verdictDigest": verdict_digest,
        }

    def _parse_json_array(self, raw_json: str) -> list[str]:
        try:
            data = json.loads(raw_json or "[]")
        except Exception as exc:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Invalid JSON array") from exc
        if not isinstance(data, list):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Invalid JSON array")
        return [str(item) for item in data]

    def _fetch_url_text(self, url: str) -> str:
        try:
            return gl.get_webpage(url, mode="text")
        except Exception as exc:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} Unable to fetch webpage") from exc

    def _handle_leader_error(self, leaders_res: gl.vm.Result, leader_fn) -> bool:
        leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
        try:
            leader_fn()
            return False
        except gl.vm.UserError as exc:
            validator_msg = exc.message if hasattr(exc, "message") else str(exc)
            if validator_msg.startswith(ERROR_EXPECTED):
                return validator_msg == leader_msg
            return validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT)
        except Exception:
            return False
