// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test, console} from "forge-std/Test.sol";
import {Vault} from "../src/Vault.sol";

/// internal 함수를 밖에서 부르기 위한 harness. 원본을 수정하지 않고 "상속"으로 노출한다.
/// (private 이었다면 이 방법도 안 된다 — 그래서 평가에서 private 5건은 internal 패치가 필요하다)
contract VaultHarness is Vault {
    function exposed_afterFee(uint256 amt) external view returns (uint256) {
        return _afterFee(amt);
    }
}

contract VaultTest is Test {
    VaultHarness v;

    // forge 는 test_* 함수마다 setUp() 을 새로 실행한다 (= 매번 깨끗한 체인 상태)
    function setUp() public {
        v = new VaultHarness();
    }

    /// ① 상태 주입: setter 가 없으니 storage 슬롯에 직접 쓴다 (cheatcode vm.store)
    function _seed(address who, uint256 bal, uint256 total) internal {
        // mapping(address=>uint) at slot 0  → 값 위치 = keccak256(abi.encode(key, 0))
        bytes32 slotShares = keccak256(abi.encode(who, uint256(0)));
        vm.store(address(v), slotShares, bytes32(bal));
        vm.store(address(v), bytes32(uint256(1)), bytes32(total)); // totalShares = slot 1
    }

    /// ② 구체값 1개로 실행하고 값을 본다 — "디버깅" 의 가장 흔한 형태
    function test_withdraw() public {
        _seed(address(this), 1000, 1000);
        console.log("before: shares =", v.shares(address(this)), " total =", v.totalShares());

        uint256 out = v.withdraw(100);

        console.log("out =", out);
        console.log("after : shares =", v.shares(address(this)), " total =", v.totalShares());
        assertEq(v.shares(address(this)), 900);
    }

    /// ③ internal 함수를 harness 로 직접 호출
    function test_afterFee_internal() public view {
        console.log("_afterFee(10000) =", v.exposed_afterFee(10000));
    }

    /// ④ 상태를 안 심으면? → require 에 걸려 revert. concrete 실행은 "그 값"에서만 본다
    function test_withdraw_unseeded_reverts() public {
        vm.expectRevert(bytes("insufficient"));
        v.withdraw(1);
    }

    /// ⑤ fuzz: 입력을 무작위로 256번 넣어 본다. IQDebug 의 "구간" 과 가장 가까운 동적 기법.
    ///    단 256개 '점' 을 찍을 뿐, 구간 전체를 덮지는 않는다.
    function testFuzz_withdraw(uint256 bal, uint256 amt) public {
        bal = bound(bal, 1, 1e24);
        amt = bound(amt, 0, bal);
        _seed(address(this), bal, bal);
        uint256 out = v.withdraw(amt);
        assertLe(out, amt); // 수수료를 떼니 out <= amt 여야 한다
    }
}
