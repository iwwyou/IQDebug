// W3b 실습 스크립트 (WALKTHROUGH.md §4).
// 실행: npx hardhat run scripts/w3b.js --network sim        입력 변경: $env:AMT=200 후 다시 실행
// 관찰 대상(25행의 i, r)은 contracts/VaultLogged.sol 에 console.log 로 미리 심어 둔 것만 출력된다.
import { network } from "hardhat";

const { ethers, provider } = await network.create();
const amt = BigInt(process.env.AMT ?? "100");

// ① 배포 (run 직전에 Hardhat 이 자동 컴파일)
const [me] = await ethers.getSigners();
const v = await ethers.deployContract("VaultLogged");
await v.waitForDeployment();
const addr = await v.getAddress();

// ② 상태 주입: 슬롯을 직접 계산해 storage 에 쓴다
const coder = ethers.AbiCoder.defaultAbiCoder();
const u256 = (n) => ethers.toBeHex(n, 32);
const slotShares = ethers.keccak256(coder.encode(["address", "uint256"], [me.address, 0]));
await provider.request({ method: "hardhat_setStorageAt", params: [addr, slotShares, u256(1000)] });
await provider.request({ method: "hardhat_setStorageAt", params: [addr, u256(1), u256(1000)] });
console.log(`before: shares=${await v.shares(me.address)} total=${await v.totalShares()}`);

// ③ 타깃 호출 → 컨트랙트 안의 console.log 가 이 터미널에 찍힌다
console.log(`withdraw(${amt})`);
try {
  const rc = await (await v.withdraw(amt)).wait();
  console.log(`after : shares=${await v.shares(me.address)} total=${await v.totalShares()} gas=${rc.gasUsed}`);
} catch (e) {
  console.log("reverted:", e.shortMessage ?? e.message);
}
