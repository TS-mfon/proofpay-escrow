# ProofPay Escrow

ProofPay Escrow is a scratch Arc + GenLayer dapp for paying for work only after evidence is checked.

A buyer creates an Arc Testnet escrow job and funds it with native Arc USDC. The freelancer, vendor, or AI agent submits a deliverable plus evidence URLs. A GenLayer Studionet intelligent contract evaluates the deliverable against the job requirements and stores an acceptance verdict. The Arc escrow contract can then release funds to the provider or refund the buyer depending on the verdict.

## Deployed Network Details

| Layer | Network | Address |
| --- | --- | --- |
| Arc escrow | Arc Testnet, chain `5042002` | `0xE9e9d7A274528D2B055aDe9c5f4b7f9DF639e2f7` |
| GenLayer judge | Studionet, chain `61999` | `0xd1066738fB575067e65a3975aF6Ef9945fE1CB33` |
| Owner / relay signer | Arc + GenLayer | `0xEd9EDd8586b20524CafA4F568413C504C9B03172` |

Arc RPC: `https://rpc.testnet.arc.network`

GenLayer RPC: `https://studio.genlayer.com/api`

## How It Works

1. Buyer enters provider wallet, title, requirements, amount, and deadline.
2. Frontend sends job metadata to the relay API and can use the returned hashes for on-chain escrow creation.
3. Provider submits deliverable text, an optional deliverable URL, and evidence URLs.
4. GenLayer `ProofPayJudge` evaluates the evidence and stores a verdict.
5. The Arc escrow accepts the relay signer verdict digest and releases or refunds native Arc USDC.

## Local Development

Run the relay:

```bash
cd relay
python3 proofpay_service.py
```

Run the frontend:

```bash
cd frontend
npm install
VITE_API_URL=http://localhost:8895 npm run dev
```

Run contract checks:

```bash
cd arc
forge test
genvm-lint check ../genlayer/contracts/proofpay_judge.py
```

## Vercel

Deploy from `frontend/` and set:

```bash
VITE_API_URL=<your-render-proofpay-relay-url>
```
