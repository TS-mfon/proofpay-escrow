import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { isAddress } from "viem";
import "./styles.css";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8895";
const ARC_CHAIN_ID = 5042002;

type Health = {
  ok: boolean;
  arcEscrowContract: string;
  genlayerJudgeContract: string;
  arcChainId: number;
  genlayerNetwork: string;
};

type Job = {
  id: string;
  buyer: string;
  provider: string;
  title: string;
  amount: string;
  deadline: number;
  status: string;
};

function App() {
  const [wallet, setWallet] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [message, setMessage] = useState("");
  const [jobForm, setJobForm] = useState({
    provider: "",
    title: "Landing page copy review",
    requirements: "Deliver concise landing page copy with pricing, CTA, and implementation notes.",
    amount: "1",
    deadline: String(Math.floor(Date.now() / 1000) + 86400),
  });
  const [submissionForm, setSubmissionForm] = useState({
    jobId: "",
    deliverable: "Completed copy with hero, pricing, CTA, and deployment notes.",
    deliverableUrl: "",
    evidenceUrls: "https://example.com",
  });

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    try {
      const [healthRes, jobsRes] = await Promise.all([fetch(`${API_URL}/health`), fetch(`${API_URL}/jobs`)]);
      setHealth(await healthRes.json());
      const data = await jobsRes.json();
      setJobs(data.jobs || []);
    } catch (error) {
      setMessage(`Backend unavailable: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  }

  async function connectWallet() {
    if (!window.ethereum) {
      setMessage("Install a wallet that supports Arc Testnet.");
      return;
    }
    const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
    setWallet(accounts[0] || "");
    await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: `0x${ARC_CHAIN_ID.toString(16)}` }] });
  }

  async function createJob() {
    if (!wallet || !isAddress(wallet)) {
      setMessage("Connect a valid buyer wallet before creating a job.");
      return;
    }
    if (!isAddress(jobForm.provider)) {
      setMessage("Provider wallet must be a valid EVM address.");
      return;
    }
    const res = await fetch(`${API_URL}/jobs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ buyer: wallet, ...jobForm }),
    });
    const data = await res.json();
    if (!res.ok) {
      setMessage(data.error || "Could not create job.");
      return;
    }
    setMessage(`Job created: ${data.jobId}`);
    setSubmissionForm((form) => ({ ...form, jobId: data.jobId }));
    await refresh();
  }

  async function submitWork() {
    if (!submissionForm.jobId || !submissionForm.deliverable) {
      setMessage("Choose a job ID and add deliverable text before submitting work.");
      return;
    }
    const evidenceUrls = submissionForm.evidenceUrls.split(",").map((url) => url.trim()).filter(Boolean);
    const res = await fetch(`${API_URL}/submissions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...submissionForm, evidenceUrls }),
    });
    const data = await res.json();
    if (!res.ok) {
      setMessage(data.error || "Could not submit work.");
      return;
    }
    setMessage(`Submission ${data.submissionId}: ${data.verdict.summary}`);
  }

  return (
    <main>
      <section className="hero">
        <p className="eyebrow">Arc + GenLayer</p>
        <h1>ProofPay Escrow</h1>
        <p>Fund Arc USDC-native work escrows, then use a GenLayer Studionet judge to check deliverables before release.</p>
        <button onClick={connectWallet}>{wallet ? `${wallet.slice(0, 6)}...${wallet.slice(-4)}` : "Connect Wallet"}</button>
      </section>

      <section className="grid">
        <div className="card">
          <h2>Create Job</h2>
          <label>Provider Wallet<span>The freelancer, vendor, or AI agent address that can receive payout.</span><input value={jobForm.provider} onChange={(e) => setJobForm({ ...jobForm, provider: e.target.value })} /></label>
          <label>Job Title<span>Short public name for the escrow job.</span><input value={jobForm.title} onChange={(e) => setJobForm({ ...jobForm, title: e.target.value })} /></label>
          <label>Requirements<span>The acceptance criteria GenLayer should check against the submission.</span><textarea value={jobForm.requirements} onChange={(e) => setJobForm({ ...jobForm, requirements: e.target.value })} /></label>
          <label>Arc Amount<span>Native Arc Testnet USDC amount to escrow on-chain.</span><input value={jobForm.amount} onChange={(e) => setJobForm({ ...jobForm, amount: e.target.value })} /></label>
          <label>Deadline<span>Unix timestamp after which rejected/unfunded jobs can refund.</span><input value={jobForm.deadline} onChange={(e) => setJobForm({ ...jobForm, deadline: e.target.value })} /></label>
          <button onClick={createJob}>Create Job</button>
        </div>

        <div className="card">
          <h2>Submit Work</h2>
          <label>Job ID<span>Use the ID returned after job creation.</span><input value={submissionForm.jobId} onChange={(e) => setSubmissionForm({ ...submissionForm, jobId: e.target.value })} /></label>
          <label>Deliverable Text<span>The work result to evaluate.</span><textarea value={submissionForm.deliverable} onChange={(e) => setSubmissionForm({ ...submissionForm, deliverable: e.target.value })} /></label>
          <label>Deliverable URL<span>Optional URL if the deliverable lives off-app.</span><input value={submissionForm.deliverableUrl} onChange={(e) => setSubmissionForm({ ...submissionForm, deliverableUrl: e.target.value })} /></label>
          <label>Evidence URLs<span>Comma-separated proof links GenLayer can inspect.</span><input value={submissionForm.evidenceUrls} onChange={(e) => setSubmissionForm({ ...submissionForm, evidenceUrls: e.target.value })} /></label>
          <button onClick={submitWork}>Generate Verdict Preview</button>
        </div>
      </section>

      <section className="card">
        <h2>Network</h2>
        <p>Arc escrow: {health?.arcEscrowContract || "not configured yet"}</p>
        <p>GenLayer judge: {health?.genlayerJudgeContract || "not configured yet"} on {health?.genlayerNetwork || "studionet"}</p>
        <p>{message}</p>
      </section>

      <section className="card">
        <h2>Jobs</h2>
        {jobs.map((job) => <p key={job.id}>{job.id} - {job.title} - {job.status} - {job.amount} Arc USDC</p>)}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
