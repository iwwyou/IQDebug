// 실행: npx hardhat run scripts/run.js --network sim
// forge 테스트와 같은 일을 "스크립트 + JSON-RPC" 로 한다. 평가 하네스가 Python 에서 할 일이 바로 이것이다.
//
// Hardhat 3 로 포팅(2026-10-29). HH2 와 달라진 부분만:
//   · `const hre = require("hardhat")` → `import { network } from "hardhat"` + `await network.create()`
//   · `hre.network.provider.send(m, p)` → `provider.request({ method, params })` (send 는 호환용으로 남아 있다)
//   · ESM + top-level await (package.json 의 "type": "module")
import { network } from "hardhat";

const { ethers, provider } = await network.create();

const [me] = await ethers.getSigners();

// ① 컴파일된 artifact 로 배포 (hardhat 이 run 전에 자동 compile)
const v = await ethers.deployContract("Vault");
await v.waitForDeployment();
const addr = await v.getAddress();
console.log("deployed at", addr);

// ② 상태 주입: setter 가 없으니 storage 슬롯에 직접 쓴다 (JSON-RPC hardhat_setStorageAt)
//    mapping(address=>uint) at slot 0 → 값 위치 = keccak256(abi.encode(key, 0))
//    ★ 이 세 줄이 "디버깅을 시작하기 위해 사용자가 먼저 알아야 하는 것" 이다:
//      슬롯 번호, 매핑 해시 규칙, 32바이트 패딩.
const coder = ethers.AbiCoder.defaultAbiCoder();
const slotShares = ethers.keccak256(coder.encode(["address", "uint256"], [me.address, 0]));
const u256 = (n) => ethers.toBeHex(n, 32);
await provider.request({ method: "hardhat_setStorageAt", params: [addr, slotShares, u256(1000)] });
await provider.request({ method: "hardhat_setStorageAt", params: [addr, u256(1), u256(1000)] }); // totalShares
console.log("before: shares =", (await v.shares(me.address)).toString(),
            " total =", (await v.totalShares()).toString());

// ③ 전역 변수 조작 예: 다음 블록 timestamp 지정 (@GlobalVar block.timestamp 에 해당)
await provider.request({ method: "evm_setNextBlockTimestamp", params: ["0x77359400"] }); // 2_000_000_000

// ④ 트랜잭션 실행
const tx = await v.withdraw(100);
const rc = await tx.wait();
console.log("after : shares =", (await v.shares(me.address)).toString(),
            " total =", (await v.totalShares()).toString(), " gas =", rc.gasUsed.toString());

// ⑤ 실행 추적: opcode 단위 trace. Hardhat 3 에도 스텝 디버거는 없고(`hardhat --help` 의 task 목록에
//    debug 가 없다), 소스 수준 관찰 수단은 console.log 뿐이다. 이게 가장 낮은 수준의 관찰 수단이다.
const trace = await provider.request({ method: "debug_traceTransaction", params: [tx.hash] });
console.log("opcode steps =", trace.structLogs.length,
            " first ops =", trace.structLogs.slice(0, 5).map((s) => s.op).join(" "));
