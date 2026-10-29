// 실행: npx hardhat run scripts/logged.js --network sim
// console.log 를 심은 버전(VaultLogged)을 돌려 루프 iteration 값을 본다.
// ★ 관찰 비용의 핵심: 값을 하나 더 보려면 **소스를 고치고 다시 컴파일·배포·실행** 해야 한다.
import { network } from "hardhat";

const { ethers, provider } = await network.create();

const [me] = await ethers.getSigners();
const v = await ethers.deployContract("VaultLogged");
await v.waitForDeployment();
const addr = await v.getAddress();

const coder = ethers.AbiCoder.defaultAbiCoder();
const u256 = (n) => ethers.toBeHex(n, 32);
const slot = ethers.keccak256(coder.encode(["address", "uint256"], [me.address, 0]));
await provider.request({ method: "hardhat_setStorageAt", params: [addr, slot, u256(1000)] });
await provider.request({ method: "hardhat_setStorageAt", params: [addr, u256(1), u256(1000)] });

for (const amt of [100, 10000 /* revert 시 */]) {
  console.log(`withdraw(${amt})`);
  try {
    await (await v.withdraw(amt)).wait();
  } catch (e) {
    console.log("  reverted:", e.shortMessage ?? e.message);
  }
}
