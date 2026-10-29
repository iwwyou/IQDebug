// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "hardhat/console.sol";

/// hardhat 식 "디버깅" = 소스에 console.log 를 넣고 다시 컴파일·배포·실행.
/// 루프 안 값을 보고 싶으면 이렇게 소스를 고쳐야 한다 (원본 변형 + 재컴파일 비용).
contract VaultLogged {
    mapping(address => uint256) public shares;
    uint256 public totalShares;
    uint256 private feeBps = 30;

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
            console.log("  loop i=%s r=%s", i, r);
        }
        return r;
    }
}
