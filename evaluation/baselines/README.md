# 기존 도구 베이스라인 (baselines)

ISSTA 2027 투고본의 **비교 대상 도구**를 손으로 돌려 보고, 평가 하네스의 출발점으로 쓰는 곳.
`IntentKeeper\evaluation\{forge,hardhat,playground}` 에서 2026-10-29 가져와 **현재 버전으로 다시 세웠다**.

- 도구별 작동 방식·기능·인용 정보는 **`RELATED_TOOLS.md`** 에 있다(논문 Background/Related Work 작성용).
- 이 문서는 **설치와 실행 방법**, 그리고 실제로 관측한 출력이다.

바이너리·패키지·빌드 산출물은 커밋하지 않는다(`../.gitignore`). 재현은 **버전을 고정한 설치 절차**로 한다.

| 구성 | 고정 버전 | 설치 방법 |
|---|---|---|
| Foundry (forge/cast/anvil/chisel) | **v1.8.6** (2026-10-21 릴리스) | `forge/install.ps1` |
| forge-std (forge 플레이그라운드) | **v1.16.2** | 아래 `git clone` 한 줄 |
| Hardhat | **3.21.1** (`npm` latest, 2026-10-29) | `npm install` |
| forge-std (Hardhat Solidity 테스트) | **v1.16.2** | `package.json` 의 npm 의존성 |
| solc | forge = 0.8.37 자동 / Hardhat = 0.8.28 (config 지정) | 각 도구가 자동 다운로드 |

---

## 설치

```powershell
# ① Foundry 바이너리 (약 257MB → baselines\forge\bin\)
.\forge\install.ps1

# ② forge 플레이그라운드의 forge-std
#    `forge install` 은 IQDebug 저장소에 git submodule 을 추가하므로 쓰지 않는다.
cd playground\forge
git clone --depth 1 --branch v1.16.2 https://github.com/foundry-rs/forge-std lib\forge-std
Remove-Item -Recurse -Force lib\forge-std\.git

# ③ Hardhat 3
cd ..\hardhat
npm install
```

---

## 공유 예제 `Vault.sol`

두 플레이그라운드가 같은 컨트랙트를 쓴다. 평가 케이스가 가진 "귀찮은 점"을 일부러 다 넣었다.

| 성질 | 왜 넣었나 |
|---|---|
| 상태변수에 setter 가 없다 (`shares`, `totalShares`, `private feeBps`) | 값을 심으려면 **storage 슬롯을 직접** 써야 한다 (슬롯 번호 + 매핑 해시 규칙을 사용자가 알아야 함) |
| 타깃 로직이 `internal _afterFee` 다 | 테스트에서 바로 못 부른다 → **상속 harness** 필요. `private` 이면 그마저 불가 |
| 루프가 있다 (`for i < 3`) | 구체 디버거는 iteration 을 하나씩 밟는다. IQDebug 는 fixpoint 로 **접는다** |
| `require` 로 막힌 경로가 있다 | 값을 잘못 심으면 revert 로 끝난다 |
| 바닥 나눗셈 정밀도 손실 | `withdraw(100)` → `100*30/10000 = 0` → 수수료가 **한 번도** 안 떼인다 (`out = 100`) |

---

## Foundry (forge)

```powershell
cd playground\forge
$F = "..\..\forge\bin\forge.exe"

& $F build                                      # 컴파일 (out/)
& $F test -vv                                   # 전체 테스트 + console.log
& $F test --match-test 'test_withdraw\(\)' -vvvv  # 호출 trace (누가 누구를 불렀나, storage 읽기/쓰기)
& $F inspect Vault storageLayout                # 상태변수 → 슬롯 번호 (상태 주입에 필요)

# ★ 인터랙티브 스텝 디버거 (TUI). q 로 종료
& $F test --match-test 'test_withdraw\(\)' --debug

# ★★ 같은 디버거를 비대화형으로: 스텝 전체를 JSON 으로 덤프 (평가 하네스에 쓸 수 있다)
& $F test --match-test 'test_withdraw\(\)' --debug --dump steps.json
```

`--match-test` 패턴은 **시그니처 전체**(`test_withdraw()`)에 매칭된다 → `^test_withdraw$` 는 안 맞고
`test_withdraw\(\)` 로 써야 한다.

### 관측 결과 (2026-10-29, forge 1.8.6)

```
Ran 4 tests for test/Vault.t.sol:VaultTest
[PASS] testFuzz_withdraw(uint256,uint256) (runs: 256, μ: 46316, ~: 47034)
[PASS] test_afterFee_internal() (gas: 14095)   Logs: _afterFee(10000) = 9912
[PASS] test_withdraw() (gas: 96055)            Logs: out = 100
[PASS] test_withdraw_unseeded_reverts() (gas: 33044)
finished in 23.42ms
```

`--dump` 산출물(8.7MB JSON)의 구조 — **평가에 바로 쓸 수 있는 형태**다:

```
{ "contracts": { "identified_contracts": {addr → name},
                 "sources": {source_id → {file_id → {source, source_map, ...}}} },
  "debug_arena": [ frame, ... ] }                       # 18 frames
frame = { address, kind: CALL|STATICCALL, calldata, returndata, gas_limit,
          trace_node_idx, step_offset, decoded, contract_name, steps: [...] }
step  = { pc, op, stack, push_stack, memory, returndata,
          gas_remaining, gas_used, gas_cost, storage_change, status,
          immediate_bytes, decoded }
```

| 측정 | 값 |
|---|---|
| `test_withdraw()` 한 번의 **총 디버거 스텝** | **4,601** |
| 콜 프레임 수 | **18** (cheatcode·getter 호출 포함) |
| 그중 타깃 호출 `VaultHarness.withdraw(100)` 프레임 | **1,014 스텝** |
| 스텝 레코드에 소스 줄 필드가 있는가 | **없다** — `pc` 뿐. 소스는 별도 `sources.source_map` 으로 `pc → 소스 범위` 조회 |

**교차 검증**: 같은 호출을 Hardhat 3 의 `debug_traceTransaction` 으로 받으면 `structLogs` 가
**정확히 1,014개** — 두 도구가 같은 EVM 의미를 보고 있음을 확인했다.

### 직접 바꿔볼 것

| 해볼 일 | 어디를 | 관찰 포인트 |
|---|---|---|
| 값 바꾸기 | `test_withdraw` 의 `_seed(..., 1000, 1000)` 과 `withdraw(100)` | `out` 이 왜 100 그대로인가? ← 정밀도 손실 |
| 버그를 fuzz 로 잡기 | `testFuzz_withdraw` 의 `assertLe` → `assertLt` | fuzz 가 반례를 몇 번째에 찾는지, 매번 같은지 |
| 소스 한 줄 고치고 다시 | `src/Vault.sol` 의 `feeBps` → `300` | **재컴파일 → 재배포 → 재실행** 시간 (= 증분 재측정 비용). 디버거는 처음부터 다시 연다 |
| `private` 로 만들기 | `_afterFee` → `private` | harness 가 컴파일 안 된다 → 접근 수술의 한계 |
| 루프 안으로 들어가 보기 | `--debug` 에서 `_afterFee` 의 `for` | `r` 이 iteration 마다 바뀌는 걸 따라간다 → IQDebug 는 같은 루프를 **fixpoint 한 번**으로 접는다 |

---

## Hardhat 3

**HH2 → HH3 로 올렸다**(2.29.1 → 3.21.1). API 가 바뀌어 config·스크립트를 다시 썼다.

| | Hardhat 2 | Hardhat 3 |
|---|---|---|
| config | `hardhat.config.js`, `require(plugin)` 자동 로드 | `defineConfig`, **`plugins: [...]` 명시**, ESM |
| 전역 HRE | `const hre = require("hardhat")` | 없음 — **`await network.create()`** 로 연결 생성 |
| 네트워크 | 암묵적 | **`type: "edr-simulated" | "http"`** 필수 |
| RPC 호출 | `hre.network.provider.send(m, p)` | `provider.request({ method, params })` (`send` 는 호환용) |
| Solidity 테스트 | **없음** | **있다 — forge-std 호환** (`npx hardhat test solidity`) |

```powershell
cd playground\hardhat
npx hardhat --help                              # task 목록 (debug task 가 없음을 확인)
npx hardhat compile
npx hardhat test solidity                       # ★ HH3 신규: forge-std cheatcode 로 돌아간다
npx hardhat run scripts/run.js --network sim    # 배포 → storage 주입 → 실행 → opcode trace
npx hardhat run scripts/logged.js --network sim # console.log 를 심은 버전으로 루프 값 보기
```

### 관측 결과 (2026-10-29, Hardhat 3.21.1)

`npx hardhat --help` 의 task 전체: `build` `clean` `compile` `console` `flatten` `node` `run`
`telemetry` `test` (+ 서브태스크 `test solidity`).
→ **`debug` task 가 없다. Hardhat 3 에도 인터랙티브 스텝 디버거는 없다.**

`npx hardhat test solidity`:

```
contracts/Vault.t.sol:VaultTest
  ✔ test_withdraw_unseeded_reverts()
  ✔ test_withdraw()
  ✔ test_afterFee_internal()
  ✔ testFuzz_withdraw(uint256,uint256) (runs: 256)
4 passing
```

→ `vm.store` · `vm.expectRevert` · `bound` · 256회 fuzz 가 **Hardhat 에서 그대로 동작한다.**
HH2 기준으로 썼던 "상태 주입은 forge=cheatcode / Hardhat=JSON-RPC" 라는 구분은 **HH3 에서 무효**다.

`npx hardhat run scripts/run.js --network sim`:

```
deployed at 0x5FbDB2315678afecb367f032d93F642f64180aa3
before: shares = 1000  total = 1000
after : shares = 900  total = 900  gas = 36812
opcode steps = 1014  first ops = PUSH1 PUSH1 MSTORE CALLVALUE DUP1
```

→ `hardhat_setStorageAt` · `evm_setNextBlockTimestamp` · `debug_traceTransaction` 모두 동작.
`run.js` 가 하는 일이 **평가 하네스를 Python 으로 쓸 때 할 일과 동일**하다(전부 JSON-RPC).

### 노드를 띄워서 밖에서 붙기 (Python 하네스의 실제 모양)

```powershell
# 터미널 A
npx hardhat node                 # 127.0.0.1:8545
# 터미널 B
npx hardhat run scripts/run.js --network localhost
```

Python 에서는 `requests.post("http://127.0.0.1:8545", json={...})` 로 똑같이 부른다.
Foundry 쪽은 `..\..\forge\bin\anvil.exe` 가 같은 역할(`anvil_setStorageAt`).

---

## 두 도구 비교 (2026-10-29 실측 기준으로 갱신)

| | Foundry 1.8.6 | Hardhat 3.21.1 |
|---|---|---|
| 테스트 언어 | Solidity (`*.t.sol`) | **Solidity + TS/JS 둘 다** |
| 상태 주입 | cheatcode `vm.store`/`vm.deal`/`vm.warp` | **cheatcode(Solidity 테스트)** 또는 JSON-RPC `hardhat_setStorageAt` |
| internal 호출 | 상속 harness | 상속 harness (동일) |
| 값 보기 | `console.log`, `-vvvv` trace, **`--debug` 스텝 디버거**, `--dump` | `console.log`, `debug_traceTransaction` — **스텝 디버거 없음** |
| 입력 구간 | fuzz (무작위 점 N개) | fuzz (Solidity 테스트) 또는 직접 루프 |
| 속도 | 4 테스트 23ms | 상대적으로 느림 (Node 기동) |
| 로컬 체인 | `anvil` | `hardhat node` (EDR, Rust) |
| 커버리지·가스 | `forge coverage`, `--gas-report` | `--coverage`, `--gas-stats` 전역 옵션 |

**IQDebug 와의 공통 전제**(= 평가에서 재야 할 것): 두 도구 모두 ① 컴파일되는 프로젝트, ② 상속 harness,
③ 슬롯 계산을 포함한 상태 주입, ④ **모든 입력에 구체값 하나**를 먼저 확정해야 첫 관측이 나온다.
