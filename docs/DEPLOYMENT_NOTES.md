# Deployment Notes

## Arc Testnet

- Contract: `ProofPayEscrow`
- Address: `0xE9e9d7A274528D2B055aDe9c5f4b7f9DF639e2f7`
- RPC: `https://rpc.testnet.arc.network`
- Chain ID: `5042002`
- Owner: `0xEd9EDd8586b20524CafA4F568413C504C9B03172`
- Relay signer: `0xEd9EDd8586b20524CafA4F568413C504C9B03172`
- Deployment command used: Foundry `forge script script/Deploy.s.sol:Deploy --broadcast`

## GenLayer Studionet

- Contract: `ProofPayJudge`
- Address: `0xd1066738fB575067e65a3975aF6Ef9945fE1CB33`
- Transaction hash: `0x5cfbca390eb30395213c66c75a2035bc6431dc402b372ca70004ab95ae220609`
- Rubric version: `v1`
- Verification: `genlayer call 0xd1066738fB575067e65a3975aF6Ef9945fE1CB33 get_rubric_version` returned `v1`

## Hosted Apps

- Relay backend: `https://proofpay-escrow-relay.onrender.com`
- Frontend: `https://proofpay-escrow.vercel.app`
