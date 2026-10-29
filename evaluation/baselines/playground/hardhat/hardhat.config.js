// Hardhat 3 설정 (ESM). HH2 와 달라진 점:
//   · `defineConfig` + `plugins: [...]` 배열로 플러그인을 명시한다(자동 로드 없음).
//   · `require("hardhat")` 전역 HRE 가 없다 → 스크립트가 `network.create()` 로 연결을 만든다.
//   · 네트워크에 `type` 이 필요하다: "edr-simulated"(로컬 시뮬레이션) | "http".
import hardhatEthers from "@nomicfoundation/hardhat-ethers";
import { defineConfig } from "hardhat/config";

export default defineConfig({
  plugins: [hardhatEthers],
  // 0.8.28 — forge-std v1.16.2 가 요구하는 하한을 넘기기 위해. Vault 의 pragma 는 ^0.8.20.
  solidity: { version: "0.8.28" },
  networks: {
    sim: { type: "edr-simulated", chainType: "l1" },
  },
});
