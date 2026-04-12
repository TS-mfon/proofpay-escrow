// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {ProofPayEscrow} from "../src/ProofPayEscrow.sol";

contract ProofPayEscrowTest is Test {
    ProofPayEscrow escrow;
    address owner = address(0xA11CE);
    address relay = address(0xBEEF);
    address buyer = address(0xB0B);
    address provider = address(0xCAFE);

    function setUp() public {
        escrow = new ProofPayEscrow(owner, relay);
        vm.deal(buyer, 1000 ether);
    }

    function _fundedSubmittedJob() internal returns (uint256 jobId) {
        vm.prank(buyer);
        jobId = escrow.createJob(provider, 10 ether, keccak256("title"), keccak256("rubric"), uint64(block.timestamp + 1 days));
        vm.prank(buyer);
        escrow.fundJob{value: 10 ether}(jobId);
        vm.prank(provider);
        escrow.submitWork(jobId, keccak256("submission"));
    }

    function testAcceptedJobPaysProvider() public {
        uint256 jobId = _fundedSubmittedJob();
        uint256 beforeBalance = provider.balance;
        vm.prank(relay);
        escrow.recordVerdict(jobId, true, keccak256("verdict"));
        escrow.releasePayout(jobId);
        assertEq(provider.balance - beforeBalance, 10 ether);
    }

    function testRejectedJobRefundsBuyer() public {
        uint256 jobId = _fundedSubmittedJob();
        uint256 beforeBalance = buyer.balance;
        vm.prank(relay);
        escrow.recordVerdict(jobId, false, keccak256("verdict"));
        vm.prank(buyer);
        escrow.refundBuyer(jobId);
        assertEq(buyer.balance - beforeBalance, 10 ether);
    }

    function testWrongRelayCannotRecordVerdict() public {
        uint256 jobId = _fundedSubmittedJob();
        vm.expectRevert(ProofPayEscrow.NotRelay.selector);
        escrow.recordVerdict(jobId, true, keccak256("verdict"));
    }

    function testCannotFundWrongAmount() public {
        vm.prank(buyer);
        uint256 jobId = escrow.createJob(provider, 10 ether, keccak256("title"), keccak256("rubric"), uint64(block.timestamp + 1 days));
        vm.expectRevert(ProofPayEscrow.InvalidAmount.selector);
        vm.prank(buyer);
        escrow.fundJob{value: 9 ether}(jobId);
    }
}
