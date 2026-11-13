// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// W1 실습용 테스트 (WALKTHROUGH.md §2). 목표: withdraw(amt) 실행 중 25행의 r, i 관찰.
//   상태 주입은 stdstore(변수 경로) 로, 입력은 환경변수 AMT 로 받는다(재컴파일 없이 입력 변경).
import {Test} from "forge-std/Test.sol";
import {stdStorage, StdStorage} from "forge-std/StdStorage.sol";
import {Vault} from "../src/Vault.sol";

/// internal 함수 _afterFee 를 밖에서 부르기 위한 하네스 (상속으로 노출).
contract VaultDebugHarness is Vault {
    function exposed_afterFee(uint256 amt) external view returns (uint256) {
        return _afterFee(amt);
    }
}

contract VaultDebugTest is Test {
    using stdStorage for StdStorage;

    VaultDebugHarness v;

    // ① 배포: 생성자가 실행되므로 feeBps = 30 이 자동으로 들어간다.
    function setUp() public {
        v = new VaultDebugHarness();
    }

    // ② 상태 주입: 슬롯 대신 getter 이름 + 키로 지정한다.
    function _seed(uint256 bal, uint256 total) internal {
        stdstore.target(address(v)).sig("shares(address)").with_key(address(this)).checked_write(bal);
        stdstore.target(address(v)).sig("totalShares()").checked_write(total);
    }

    /// W1-a: public 함수 withdraw 를 그대로 디버깅.
    function test_withdraw() public {
        uint256 amt = vm.envOr("AMT", uint256(100)); // ③ 입력. 기본 100, $env:AMT 로 변경
        _seed(1000, 1000);
        uint256 out = v.withdraw(amt);                // ④ 타깃 호출
        assertEq(v.shares(address(this)), 1000 - amt);
        assertLe(out, amt);
    }

    /// W1-b: internal 함수 _afterFee 를 하네스로 직접 디버깅.
    function test_afterFee() public view {
        uint256 amt = vm.envOr("AMT", uint256(100));
        v.exposed_afterFee(amt);
    }
}
