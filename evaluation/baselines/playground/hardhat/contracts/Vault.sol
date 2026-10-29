// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// 실습용 컨트랙트. 평가 케이스들이 가진 "귀찮은 점"을 일부러 다 넣었다.
///  - 상태변수에 setter 가 없다        → 값을 심으려면 storage 를 직접 써야 한다
///  - 타겟 로직이 internal 함수다       → 테스트에서 바로 못 부른다 (harness 필요)
///  - 루프가 있다                       → concrete 디버거는 iteration 을 하나씩 밟는다
///  - require 로 막힌 경로가 있다       → 값을 잘못 심으면 revert 로 끝난다
contract Vault {
    mapping(address => uint256) public shares; // slot 0  (매핑 자체는 빈 슬롯, 값은 keccak 위치)
    uint256 public totalShares;                // slot 1
    uint256 private feeBps = 30;               // slot 2  (private — getter 도 없다)

    function withdraw(uint256 amt) external returns (uint256 out) {
        uint256 bal = shares[msg.sender];
        require(amt <= bal, "insufficient");
        out = _afterFee(amt);
        shares[msg.sender] = bal - amt;
        totalShares -= amt;
    }

    function _afterFee(uint256 amt) internal view returns (uint256) {
        uint256 r = amt;
        for (uint256 i = 0; i < 3; i++) {
            r = r - (r * feeBps) / 10000;
        }
        return r;
    }
}
