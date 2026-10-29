// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Hardhat 3 의 Solidity 테스트 (2026-10-29 확인). HH2 에는 없던 기능이고 **forge-std 와 호환**이다 —
// 즉 `vm.store` 류 cheatcode 상태 주입이 Hardhat 에서도 된다. 평가 서술에서
// "상태 주입은 forge cheatcode / Hardhat JSON-RPC" 로 갈라 쓰던 구분이 HH3 에서는 약해진다.
// 실행: npx hardhat test solidity
import {Test} from "forge-std/Test.sol";
import {Vault} from "./Vault.sol";

/// forge 쪽과 동일한 harness — internal 함수를 밖에서 부르려면 상속해서 노출해야 한다.
contract VaultHarness is Vault {
    function exposed_afterFee(uint256 amt) external view returns (uint256) {
        return _afterFee(amt);
    }
}

contract VaultTest is Test {
    VaultHarness v;

    function setUp() public {
        v = new VaultHarness();
    }

    function _seed(address who, uint256 bal, uint256 total) internal {
        bytes32 slotShares = keccak256(abi.encode(who, uint256(0)));
        vm.store(address(v), slotShares, bytes32(bal));
        vm.store(address(v), bytes32(uint256(1)), bytes32(total));
    }

    function test_withdraw() public {
        _seed(address(this), 1000, 1000);
        uint256 out = v.withdraw(100);
        // 100 * 30 / 10000 = 0 → 수수료가 한 번도 떼이지 않는다 (정밀도 손실)
        assertEq(out, 100);
        assertEq(v.shares(address(this)), 900);
    }

    function test_afterFee_internal() public view {
        // 10000 → 9970 → 9941 → 9912 (매 회전 바닥 나눗셈)
        assertEq(v.exposed_afterFee(10000), 9912);
    }

    function test_withdraw_unseeded_reverts() public {
        vm.expectRevert(bytes("insufficient"));
        v.withdraw(1);
    }

    function testFuzz_withdraw(uint256 bal, uint256 amt) public {
        bal = bound(bal, 1, 1e24);
        amt = bound(amt, 0, bal);
        _seed(address(this), bal, bal);
        uint256 out = v.withdraw(amt);
        assertLe(out, amt);
    }
}
