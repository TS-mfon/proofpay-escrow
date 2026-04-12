// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract ProofPayEscrow {
    enum JobStatus {
        None,
        Created,
        Funded,
        Submitted,
        Accepted,
        Rejected,
        PaidOut,
        Refunded
    }

    struct Job {
        address buyer;
        address provider;
        uint256 amount;
        bytes32 titleHash;
        bytes32 rubricHash;
        bytes32 submissionHash;
        bytes32 verdictDigest;
        bool accepted;
        uint64 deadline;
        JobStatus status;
    }

    address public owner;
    address public relaySigner;
    uint256 public nextJobId = 1;
    mapping(uint256 => Job) public jobs;

    event JobCreated(uint256 indexed jobId, address indexed buyer, address indexed provider, uint256 amount);
    event JobFunded(uint256 indexed jobId, uint256 amount);
    event WorkSubmitted(uint256 indexed jobId, bytes32 submissionHash);
    event VerdictRecorded(uint256 indexed jobId, bool accepted, bytes32 verdictDigest);
    event PayoutReleased(uint256 indexed jobId, address indexed provider, uint256 amount);
    event BuyerRefunded(uint256 indexed jobId, address indexed buyer, uint256 amount);
    event RelaySignerUpdated(address indexed relaySigner);

    error NotOwner();
    error NotRelay();
    error NotBuyer();
    error NotProvider();
    error InvalidAddress();
    error InvalidAmount();
    error InvalidStatus();
    error InvalidDeadline();
    error TransferFailed();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyRelay() {
        if (msg.sender != relaySigner) revert NotRelay();
        _;
    }

    constructor(address owner_, address relaySigner_) {
        if (owner_ == address(0) || relaySigner_ == address(0)) revert InvalidAddress();
        owner = owner_;
        relaySigner = relaySigner_;
    }

    function setRelaySigner(address relaySigner_) external onlyOwner {
        if (relaySigner_ == address(0)) revert InvalidAddress();
        relaySigner = relaySigner_;
        emit RelaySignerUpdated(relaySigner_);
    }

    function createJob(address provider, uint256 amount, bytes32 titleHash, bytes32 rubricHash, uint64 deadline)
        external
        returns (uint256 jobId)
    {
        if (provider == address(0)) revert InvalidAddress();
        if (amount == 0) revert InvalidAmount();
        if (deadline <= block.timestamp) revert InvalidDeadline();

        jobId = nextJobId++;
        jobs[jobId] = Job({
            buyer: msg.sender,
            provider: provider,
            amount: amount,
            titleHash: titleHash,
            rubricHash: rubricHash,
            submissionHash: bytes32(0),
            verdictDigest: bytes32(0),
            accepted: false,
            deadline: deadline,
            status: JobStatus.Created
        });
        emit JobCreated(jobId, msg.sender, provider, amount);
    }

    function fundJob(uint256 jobId) external payable {
        Job storage job = _job(jobId);
        if (msg.sender != job.buyer) revert NotBuyer();
        if (job.status != JobStatus.Created) revert InvalidStatus();
        if (msg.value != job.amount) revert InvalidAmount();
        job.status = JobStatus.Funded;
        emit JobFunded(jobId, msg.value);
    }

    function submitWork(uint256 jobId, bytes32 submissionHash) external {
        Job storage job = _job(jobId);
        if (msg.sender != job.provider) revert NotProvider();
        if (job.status != JobStatus.Funded && job.status != JobStatus.Rejected) revert InvalidStatus();
        if (submissionHash == bytes32(0)) revert InvalidAmount();
        job.submissionHash = submissionHash;
        job.status = JobStatus.Submitted;
        emit WorkSubmitted(jobId, submissionHash);
    }

    function recordVerdict(uint256 jobId, bool accepted, bytes32 verdictDigest) external onlyRelay {
        Job storage job = _job(jobId);
        if (job.status != JobStatus.Submitted) revert InvalidStatus();
        if (verdictDigest == bytes32(0)) revert InvalidAmount();
        job.accepted = accepted;
        job.verdictDigest = verdictDigest;
        job.status = accepted ? JobStatus.Accepted : JobStatus.Rejected;
        emit VerdictRecorded(jobId, accepted, verdictDigest);
    }

    function releasePayout(uint256 jobId) external {
        Job storage job = _job(jobId);
        if (job.status != JobStatus.Accepted) revert InvalidStatus();
        job.status = JobStatus.PaidOut;
        (bool ok,) = job.provider.call{value: job.amount}("");
        if (!ok) revert TransferFailed();
        emit PayoutReleased(jobId, job.provider, job.amount);
    }

    function refundBuyer(uint256 jobId) external {
        Job storage job = _job(jobId);
        if (msg.sender != job.buyer && msg.sender != owner) revert NotBuyer();
        if (job.status != JobStatus.Rejected && job.status != JobStatus.Funded && block.timestamp <= job.deadline) {
            revert InvalidStatus();
        }
        if (job.status == JobStatus.PaidOut || job.status == JobStatus.Refunded) revert InvalidStatus();
        job.status = JobStatus.Refunded;
        (bool ok,) = job.buyer.call{value: job.amount}("");
        if (!ok) revert TransferFailed();
        emit BuyerRefunded(jobId, job.buyer, job.amount);
    }

    function getJob(uint256 jobId) external view returns (Job memory) {
        return _job(jobId);
    }

    function _job(uint256 jobId) internal view returns (Job storage job) {
        job = jobs[jobId];
        if (job.status == JobStatus.None) revert InvalidStatus();
    }
}
