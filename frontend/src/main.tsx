import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { createPublicClient, createWalletClient, custom, http, isAddress, keccak256, parseEther, toHex } from "viem";
import "./styles.css";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

const API_URL = import.meta.env.VITE_API_URL || "https://proofpay-escrow-relay.onrender.com";
const ARC_CHAIN_ID = 5042002;
const ARC_RPC_URL = "https://rpc.testnet.arc.network";
const ARC_ESCROW_CONTRACT = "0xE9e9d7A274528D2B055aDe9c5f4b7f9DF639e2f7";
const GENLAYER_JUDGE_CONTRACT = "0xd1066738fB575067e65a3975aF6Ef9945fE1CB33";
const GENLAYER_NETWORK = "studionet";

const arcTestnet = {
  id: ARC_CHAIN_ID,
  name: "Arc Testnet",
  nativeCurrency: { name: "Arc Testnet Token", symbol: "ARC", decimals: 18 },
  rpcUrls: { default: { http: [ARC_RPC_URL] } },
} as const;

const escrowAbi = [
  { name: "nextJobId", type: "function", stateMutability: "view", inputs: [], outputs: [{ type: "uint256" }] },
  {
    name: "createJob",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "provider", type: "address" },
      { name: "amount", type: "uint256" },
      { name: "titleHash", type: "bytes32" },
      { name: "rubricHash", type: "bytes32" },
      { name: "deadline", type: "uint64" },
    ],
    outputs: [{ type: "uint256" }],
  },
  {
    name: "fundJob",
    type: "function",
    stateMutability: "payable",
    inputs: [{ name: "jobId", type: "uint256" }],
    outputs: [],
  },
  {
    name: "submitWork",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "jobId", type: "uint256" },
      { name: "submissionHash", type: "bytes32" },
    ],
    outputs: [],
  },
] as const;

type Role = "creator" | "jobber";
type Page = "home" | "creator" | "jobber" | "jobs" | "tutorial" | "status";
type Health = { arcEscrowContract: string; genlayerJudgeContract: string; genlayerNetwork: string; arcChainId: number };
type Job = {
  id: string;
  buyer: string;
  provider: string;
  title: string;
  requirements: string;
  amount: string;
  deadline: number;
  status: string;
  onchainJobId?: string;
  fundingTxHash?: string;
};

const nowPlusDay = () => new Date(Date.now() + 86400 * 1000).toISOString().slice(0, 16);
const hash32 = (value: string) => keccak256(toHex(value));
const short = (value: string) => (value ? `${value.slice(0, 6)}...${value.slice(-4)}` : "");
const splitUrls = (value: string) => value.split(",").map((url) => url.trim()).filter(Boolean);
const toTimestamp = (value: string) => Math.floor(new Date(value).getTime() / 1000);

function App() {
  const [page, setPage] = useState<Page>("home");
  const [wallets, setWallets] = useState<Record<Role, string>>({ creator: "", jobber: "" });
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [message, setMessage] = useState("Connect a role wallet to start.");
  const [busy, setBusy] = useState(false);
  const [jobForm, setJobForm] = useState({
    provider: "",
    title: "Landing page copy review",
    requirements: "Deliver concise landing page copy with pricing, CTA, and implementation notes.",
    amount: "0.001",
    deadline: nowPlusDay(),
  });
  const [submissionForm, setSubmissionForm] = useState({ jobId: "", onchainJobId: "", deliverable: "", deliverableUrl: "", evidenceUrls: "" });

  const selectedJob = useMemo(() => jobs.find((job) => job.id === submissionForm.jobId), [jobs, submissionForm.jobId]);

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    try {
      const [healthRes, jobsRes] = await Promise.all([fetch(`${API_URL}/health`), fetch(`${API_URL}/jobs`)]);
      if (!healthRes.ok || !jobsRes.ok) throw new Error("Relay returned an unavailable response.");
      setHealth(await healthRes.json());
      setJobs((await jobsRes.json()).jobs || []);
      setMessage("Relay online. Arc escrow and GenLayer judge are configured.");
    } catch (error) {
      setHealth({ arcEscrowContract: ARC_ESCROW_CONTRACT, genlayerJudgeContract: GENLAYER_JUDGE_CONTRACT, arcChainId: ARC_CHAIN_ID, genlayerNetwork: GENLAYER_NETWORK });
      setMessage(`Relay is starting or temporarily unavailable: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  }

  async function connectWallet(role: Role) {
    if (!window.ethereum) {
      setMessage("Install a browser wallet that supports Arc Testnet.");
      return;
    }
    try {
      await ensureArcNetwork();
      const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
      setWallets((current) => ({ ...current, [role]: accounts[0] || "" }));
      setMessage(`${role === "creator" ? "Creator" : "Jobber"} wallet connected: ${short(accounts[0] || "")}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Wallet connection failed.");
    }
  }

  async function ensureArcNetwork() {
    if (!window.ethereum) throw new Error("No wallet provider found.");
    const chainId = `0x${ARC_CHAIN_ID.toString(16)}`;
    try {
      await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] });
    } catch {
      await window.ethereum.request({
        method: "wallet_addEthereumChain",
        params: [{ chainId, chainName: "Arc Testnet", nativeCurrency: arcTestnet.nativeCurrency, rpcUrls: [ARC_RPC_URL] }],
      });
    }
  }

  async function createAndFundJob() {
    if (!wallets.creator || !isAddress(wallets.creator)) return setMessage("Connect the creator wallet first.");
    if (!isAddress(jobForm.provider)) return setMessage("Provider wallet must be a valid EVM address.");
    if (!jobForm.title.trim() || !jobForm.requirements.trim()) return setMessage("Add a title and acceptance requirements.");
    const deadline = toTimestamp(jobForm.deadline);
    if (!deadline || deadline <= Math.floor(Date.now() / 1000)) return setMessage("Deadline must be in the future.");

    setBusy(true);
    try {
      await ensureArcNetwork();
      const publicClient = createPublicClient({ chain: arcTestnet, transport: http(ARC_RPC_URL) });
      const walletClient = createWalletClient({ chain: arcTestnet, transport: custom(window.ethereum!) });
      const onchainJobId = (await publicClient.readContract({ address: ARC_ESCROW_CONTRACT, abi: escrowAbi, functionName: "nextJobId" })) as bigint;
      const value = parseEther(jobForm.amount);
      setMessage("Creating the Arc escrow job...");
      const createHash = await walletClient.writeContract({
        account: wallets.creator as `0x${string}`,
        address: ARC_ESCROW_CONTRACT,
        abi: escrowAbi,
        functionName: "createJob",
        args: [jobForm.provider as `0x${string}`, value, hash32(jobForm.title), hash32(jobForm.requirements), BigInt(deadline)],
      });
      await publicClient.waitForTransactionReceipt({ hash: createHash });
      setMessage("Funding the treasury escrow from the creator wallet...");
      const fundingTxHash = await walletClient.writeContract({
        account: wallets.creator as `0x${string}`,
        address: ARC_ESCROW_CONTRACT,
        abi: escrowAbi,
        functionName: "fundJob",
        args: [onchainJobId],
        value,
      });
      await publicClient.waitForTransactionReceipt({ hash: fundingTxHash });

      const res = await fetch(`${API_URL}/jobs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ buyer: wallets.creator, ...jobForm, deadline, onchainJobId: onchainJobId.toString(), fundingTxHash }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not save the funded job.");
      setSubmissionForm((form) => ({ ...form, jobId: data.jobId, onchainJobId: onchainJobId.toString() }));
      setMessage(`Job funded and listed: ${data.jobId}. Treasury tx: ${short(fundingTxHash)}`);
      setPage("jobs");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Create and fund flow failed.");
    } finally {
      setBusy(false);
    }
  }

  async function submitWork() {
    const onchainJobId = submissionForm.onchainJobId || selectedJob?.onchainJobId || "";
    if (!wallets.jobber || !isAddress(wallets.jobber)) return setMessage("Connect the jobber wallet first.");
    if (!submissionForm.jobId || !submissionForm.deliverable.trim()) return setMessage("Choose a job and add deliverable text.");
    setBusy(true);
    try {
      await ensureArcNetwork();
      if (onchainJobId) {
        const publicClient = createPublicClient({ chain: arcTestnet, transport: http(ARC_RPC_URL) });
        const walletClient = createWalletClient({ chain: arcTestnet, transport: custom(window.ethereum!) });
        setMessage("Submitting the work hash to the Arc escrow contract...");
        const txHash = await walletClient.writeContract({
          account: wallets.jobber as `0x${string}`,
          address: ARC_ESCROW_CONTRACT,
          abi: escrowAbi,
          functionName: "submitWork",
          args: [BigInt(onchainJobId), hash32(JSON.stringify(submissionForm))],
        });
        await publicClient.waitForTransactionReceipt({ hash: txHash });
      }
      const res = await fetch(`${API_URL}/submissions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...submissionForm, onchainJobId, evidenceUrls: splitUrls(submissionForm.evidenceUrls) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.urlProblems?.map((item: { url: string; reason: string }) => `${item.url}: ${item.reason}`).join(" | ") || data.error || "Could not submit work.");
      setMessage(`Submission ${data.submissionId}: ${data.verdict.summary}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Submit work failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <header className="topbar">
        <button className="brand" onClick={() => setPage("home")}><span className="logo">◆</span> ProofPay</button>
        <nav>
          {(["jobs", "creator", "jobber", "tutorial", "status"] as Page[]).map((item) => <button className={page === item ? "active" : ""} onClick={() => setPage(item)} key={item}>{item}</button>)}
        </nav>
      </header>

      {page === "home" && <Landing setPage={setPage} connectWallet={connectWallet} wallets={wallets} />}
      {page === "creator" && <CreatorPortal form={jobForm} setForm={setJobForm} wallet={wallets.creator} connect={() => connectWallet("creator")} submit={createAndFundJob} busy={busy} />}
      {page === "jobber" && <JobberPortal jobs={jobs} form={submissionForm} setForm={setSubmissionForm} wallet={wallets.jobber} connect={() => connectWallet("jobber")} submit={submitWork} busy={busy} />}
      {page === "jobs" && <FindJobs jobs={jobs} setPage={setPage} setSubmissionForm={setSubmissionForm} />}
      {page === "tutorial" && <Tutorial />}
      {page === "status" && <Status health={health} message={message} refresh={refresh} />}

      <aside className="toast">{message}</aside>
    </main>
  );
}

function Landing({ setPage }: { setPage: (page: Page) => void; connectWallet: (role: Role) => void; wallets: Record<Role, string> }) {
  return <section className="hero page-rise">
    <div className="hero-copy">
      <p className="eyebrow">Verified work escrow for Arc builders</p>
      <h1>Pay for completed work, not promises.</h1>
      <p>ProofPay lets a creator lock an Arc escrow reward, publish a clear job, and let a jobber submit work for GenLayer-assisted review. Funds stay in the deployed escrow contract until the work path is ready for verdict-based release or refund.</p>
      <div className="actions">
        <button className="primary" onClick={() => setPage("creator")}>Create a funded job</button>
        <button className="secondary" onClick={() => setPage("jobs")}>Find open jobs</button>
      </div>
    </div>
    <div className="hero-panel">
      <span>01 Lock reward</span><span>02 Submit work</span><span>03 Check evidence</span><span>04 Release or refund</span>
    </div>
  </section>;
}

function CreatorPortal({ form, setForm, wallet, connect, submit, busy }: { form: typeof initialJobForm; setForm: (form: typeof initialJobForm) => void; wallet: string; connect: () => void; submit: () => void; busy: boolean }) {
  return <section className="panel page-rise">
    <div className="section-head"><p className="eyebrow">Creator portal</p><h2>Create and fund a job</h2><p>The reward is deducted from the connected creator wallet and locked in the deployed Arc escrow contract.</p></div>
    <div className="form-grid">
      <label>Provider wallet<span>The jobber address allowed to submit work and receive payout after acceptance.</span><input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} placeholder="0x..." /></label>
      <label>Reward amount<span>Amount sent to the escrow contract. The current deployed contract accepts Arc native testnet value.</span><input value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
      <label>Job title<span>Short public title shown in the job board.</span><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
      <label>Deadline<span>Used for refund timing if work is not accepted.</span><input type="datetime-local" value={form.deadline} onChange={(e) => setForm({ ...form, deadline: e.target.value })} /></label>
      <label className="wide">Acceptance requirements<span>Write the exact rubric GenLayer should evaluate against.</span><textarea value={form.requirements} onChange={(e) => setForm({ ...form, requirements: e.target.value })} /></label>
    </div>
    <div className="actions"><button className="secondary" onClick={connect}>{wallet ? `Creator ${short(wallet)}` : "Connect creator wallet"}</button><button className="primary" disabled={busy} onClick={submit}>{busy ? "Processing..." : "Create, fund, and list job"}</button></div>
  </section>;
}

const initialJobForm = { provider: "", title: "", requirements: "", amount: "", deadline: "" };

function JobberPortal({ jobs, form, setForm, wallet, connect, submit, busy }: { jobs: Job[]; form: typeof initialSubmissionForm; setForm: (form: typeof initialSubmissionForm) => void; wallet: string; connect: () => void; submit: () => void; busy: boolean }) {
  return <section className="panel page-rise">
    <div className="section-head"><p className="eyebrow">Jobber portal</p><h2>Submit work for a funded job</h2><p>Evidence links are optional. If you add links, they must be publicly accessible; gated X, Instagram, and similar links will be rejected with a clear message.</p></div>
    <div className="form-grid">
      <label>Job ID<span>Select a relay job or paste the job ID.</span><select value={form.jobId} onChange={(e) => {
        const job = jobs.find((item) => item.id === e.target.value);
        setForm({ ...form, jobId: e.target.value, onchainJobId: job?.onchainJobId || "" });
      }}><option value="">Choose a job</option>{jobs.map((job) => <option key={job.id} value={job.id}>{job.title} - {job.id}</option>)}</select></label>
      <label>On-chain job ID<span>Auto-filled for funded jobs; required for Arc contract submission.</span><input value={form.onchainJobId} onChange={(e) => setForm({ ...form, onchainJobId: e.target.value })} /></label>
      <label className="wide">Deliverable<span>Describe the completed work clearly enough for review.</span><textarea value={form.deliverable} onChange={(e) => setForm({ ...form, deliverable: e.target.value })} placeholder="Paste the work or a precise summary." /></label>
      <label>Deliverable URL<span>Optional public link to the work.</span><input value={form.deliverableUrl} onChange={(e) => setForm({ ...form, deliverableUrl: e.target.value })} placeholder="https://..." /></label>
      <label>Evidence URLs<span>Optional comma-separated public links. Avoid gated social URLs.</span><input value={form.evidenceUrls} onChange={(e) => setForm({ ...form, evidenceUrls: e.target.value })} placeholder="https://example.com/proof" /></label>
    </div>
    <div className="actions"><button className="secondary" onClick={connect}>{wallet ? `Jobber ${short(wallet)}` : "Connect jobber wallet"}</button><button className="primary" disabled={busy} onClick={submit}>{busy ? "Submitting..." : "Submit work"}</button></div>
  </section>;
}

const initialSubmissionForm = { jobId: "", onchainJobId: "", deliverable: "", deliverableUrl: "", evidenceUrls: "" };

function FindJobs({ jobs, setPage, setSubmissionForm }: { jobs: Job[]; setPage: (page: Page) => void; setSubmissionForm: React.Dispatch<React.SetStateAction<typeof initialSubmissionForm>> }) {
  return <section className="panel page-rise">
    <div className="section-head"><p className="eyebrow">Job board</p><h2>Find funded work</h2><p>Browse jobs created through the relay and backed by the Arc escrow transaction recorded at creation time.</p></div>
    <div className="cards">{jobs.length === 0 ? <p className="empty">No jobs listed yet.</p> : jobs.map((job) => <article className="job-card" key={job.id}>
      <div><span className="pill">{job.status}</span><span className="pill">On-chain #{job.onchainJobId || "pending"}</span></div>
      <h3>{job.title}</h3><p>{job.requirements}</p>
      <dl><div><dt>Reward</dt><dd>{job.amount}</dd></div><div><dt>Provider</dt><dd>{short(job.provider)}</dd></div></dl>
      <button className="primary" onClick={() => { setSubmissionForm((form) => ({ ...form, jobId: job.id, onchainJobId: job.onchainJobId || "" })); setPage("jobber"); }}>Submit work</button>
    </article>)}</div>
  </section>;
}

function Tutorial() {
  return <section className="panel tutorial page-rise"><p className="eyebrow">Tutorial</p><h2>How ProofPay works</h2>
    <ol><li>Creator connects their creator wallet and enters the provider wallet, reward, deadline, and acceptance rubric.</li><li>The app creates the Arc escrow job and funds it from the creator wallet in two wallet transactions.</li><li>Jobber connects their own wallet, selects a job, and submits the work hash to Arc plus a review package to the relay.</li><li>Optional evidence links are checked before being accepted. If GenLayer cannot access a link, the relay tells the user before submission.</li><li>The current deployment supports funded escrow plus verdict preview. On-chain release/refund requires the relay signer to record the GenLayer verdict.</li></ol>
  </section>;
}

function Status({ health, message, refresh }: { health: Health | null; message: string; refresh: () => void }) {
  return <section className="panel status page-rise"><p className="eyebrow">Network status</p><h2>Live configuration</h2>
    <p>Arc escrow: <code>{health?.arcEscrowContract || ARC_ESCROW_CONTRACT}</code></p><p>GenLayer judge: <code>{health?.genlayerJudgeContract || GENLAYER_JUDGE_CONTRACT}</code></p><p>Network: <code>{health?.genlayerNetwork || GENLAYER_NETWORK}</code></p><p>Relay API: <code>{API_URL}</code></p><p>{message}</p><button className="secondary" onClick={refresh}>Refresh status</button>
  </section>;
}

createRoot(document.getElementById("root")!).render(<App />);
