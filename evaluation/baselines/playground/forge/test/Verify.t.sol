// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Verification-only tests for forge 1.9.0 features not yet checked:
//   --symbolic (check_/prove_), fuzz --debug, vm.breakpoint.
import {Test} from "forge-std/Test.sol";
import {VaultHarness} from "./Vault.t.sol";

contract VerifyTest is Test {
    VaultHarness v;

    function setUp() public {
        v = new VaultHarness();
    }

    function _seed(address who, uint256 bal) internal {
        vm.store(address(v), keccak256(abi.encode(who, uint256(0))), bytes32(bal));
        vm.store(address(v), bytes32(uint256(1)), bytes32(bal));
    }

    // [symbolic] true property: fee never increases the amount
    function check_afterFee_le(uint256 amt) public view {
        assertLe(v.exposed_afterFee(amt), amt);
    }

    // [symbolic] false property: should yield a counterexample (amt >= 334)
    function check_afterFee_eq(uint256 amt) public view {
        assertEq(v.exposed_afterFee(amt), amt);
    }

    // [symbolic] through public entry with seeded storage
    function prove_withdraw(uint256 bal, uint256 amt) public {
        vm.assume(amt <= bal);
        _seed(address(this), bal);
        uint256 out = v.withdraw(amt);
        assertLe(out, amt);
        assertEq(v.shares(address(this)), bal - amt);
    }

    // [fuzz --debug] fails for amt >= 334 -> debugger opens on the failing case
    function testFuzz_afterFee_eq(uint256 amt) public view {
        amt = bound(amt, 0, 1e18);
        assertEq(v.exposed_afterFee(amt), amt);
    }

    // [vm.breakpoint] cheatcode breakpoint
    function test_breakpoint() public {
        _seed(address(this), 1000);
        vm.breakpoint("a");
        v.withdraw(100);
    }
}
