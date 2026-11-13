# 실행 안내: W1 · W2 (Foundry) · W3b (Hardhat 3)

작성 2026-11-09. 모든 명령은 이 PC(Windows, PowerShell)에서 실제로 돌려 출력까지 확인했다.
용도: Background 서술과 그림 캡처. 실험 계획은 `../EXPERIMENT_PLAN.md`.
`[캡처]` 표시는 그림으로 쓰기 좋은 지점이다.

---

## 0. 공통

### 0.1 버전과 위치

| 항목 | 값 | 위치 |
|---|---|---|
| Foundry | 1.9.0 (commit 6056c58), 디버거 = 내장 SolDB 0.4.5 | `evaluation\baselines\forge\bin\` (`forge.exe`, `cast.exe`, `anvil.exe`). **PATH 에 없음** |
| forge-std | v1.16.2 | `playground\forge\lib\forge-std` (설치 완료) |
| solc | 0.8.37 (Foundry·Hardhat 동일) | 자동 다운로드 |
| Hardhat | 3.21.1 + hardhat-ethers | `playground\hardhat\node_modules` (`npm install` 완료) |
| Node | v22.21.1 | |

모든 PowerShell 창에서 먼저 한 줄:

```powershell
$F = "C:\Users\isjeon\PycharmProjects\pythonProject\IQDebug\evaluation\baselines\forge\bin"
```

### 0.2 공통 시나리오

세 워크플로 모두 같은 일을 한다.

> **상태** `shares[호출자] = 1000`, `totalShares = 1000` 에서 **`withdraw(amt)`** 를 호출하고,
> **25행**에서 반복마다 `r`, `i` 의 값을 본다. 그다음 `amt` 를 바꿔 다시 본다.

그림용으로는 `amt = 1000` 을 권한다. `amt = 100` 이면 `100 * 30 / 10000 = 0` 이라 `r` 이 변하지 않는다.
`amt = 1000` 이면 `r` 이 1000 → 997 → 995 로 바뀌고 반환값은 993 이다.

### 0.3 대상 소스 `playground\forge\src\Vault.sol` (Hardhat 쪽 `contracts\Vault.sol` 과 동일)

```solidity
 9  contract Vault {
10      mapping(address => uint256) public shares; // slot 0
11      uint256 public totalShares;                // slot 1
12      uint256 private feeBps = 30;               // slot 2 (private, getter 없음)
13
14      function withdraw(uint256 amt) external returns (uint256 out) {
15          uint256 bal = shares[msg.sender];
16          require(amt <= bal, "insufficient");
17          out = _afterFee(amt);
18          shares[msg.sender] = bal - amt;
19          totalShares -= amt;
20      }
21
22      function _afterFee(uint256 amt) internal view returns (uint256) {
23          uint256 r = amt;
24          for (uint256 i = 0; i < 3; i++) {
25              r = r - (r * feeBps) / 10000;
26          }
27          return r;
28      }
29  }
```

설정자(setter)가 없어서 상태를 storage 에 직접 넣어야 하고, 관찰 지점은 internal 함수 안이다.

### 0.4 디버거 명령 (W1·W2 공통, SolDB)

`--debug` 를 붙이면 forge/cast 가 실행을 기록한 뒤 `(soldb)` 프롬프트를 띄운다. 아래는 **forge 명령이 아니라 그 프롬프트에서 치는 SolDB 명령**이다.

| 명령 | 뜻 |
|---|---|
| `break src/Vault.sol:25` | 25행에 breakpoint |
| `continue` / `reverse-continue` | 다음 / 이전 breakpoint 까지 |
| `next` / `step` / `finish` | 다음 줄 / 함수 안으로 / 함수 끝까지 (앞에 `reverse-` 를 붙이면 역방향) |
| `vars` | 현재 지점의 지역변수와 상태변수 |
| `print <식>` | 값 하나 (`print totalShares`) |
| `bt` | 호출 스택 |
| `tui` | 화면 모드 진입 (아래 키) |
| `quit` | 종료 |

TUI 키 (키보드 전용, 마우스 없음): `n`/`N` next/역, `s`/`S` step/역, `c`/`C` continue/역, `f`/`F` finish/역,
`b` 현재 줄 breakpoint, `m` source↔asm, `:` 명령 입력, `Tab` 패널 이동, `j`/`k` 스크롤, `?` 도움말, `q` 프롬프트로, `Q` 종료.
패널: Source, Variables, Stack, Memory, Backtrace, Opcodes, Console.

같은 명령을 파일로 넣어 재현할 수 있다: `playground\forge\dbg\line25.txt` (25행 breakpoint → 세 바퀴 `vars` → `bt`).

---

## 1. 준비물 파일 목록

| 파일 | 워크플로 | 역할 |
|---|---|---|
| `forge\src\Vault.sol` | W1·W2 | 대상 (수정하지 않음) |
| `forge\test\VaultDebug.t.sol` | W1 | 테스트 + 하네스. **W1 에서 개발 대상과 별도로 써야 하는 파일** |
| `forge\test\Vault.t.sol` | W1 (참고) | 같은 일을 `vm.store`(슬롯 직접 지정)로 하는 예전 예제 |
| `forge\w2_setup.ps1` | W2 | 배포 + 슬롯 계산 + 주입 문자열 생성 |
| `forge\dbg\line25.txt` | W1·W2 | 디버거 명령 재현용 |
| `hardhat\contracts\VaultLogged.sol` | W3b | `Vault.sol` 사본 + 25행 뒤 `console.log` 한 줄 |
| `hardhat\scripts\w3b.js` | W3b | 배포 + 슬롯 주입 + 호출 |
| `hardhat\hardhat.config.js` | W3b | solc 0.8.37, 네트워크 `sim`(내장 시뮬레이터) |

---

## 2. W1 — forge 테스트 + `forge test --debug`

### 2.1 무엇을 하는가

forge 는 **테스트 함수를 실행하는 도구**다. 대상 함수를 디버깅하려면 그 함수를 부르는 테스트를 먼저 쓴다.
구체 실행의 재료(배포, 상태, 호출자, 입력)는 모두 테스트 파일 안에 Solidity 로 적는다.

### 2.2 테스트 파일 `forge\test\VaultDebug.t.sol` (이미 만들어 둠)

```solidity
contract VaultDebugHarness is Vault {                       // internal 함수 노출용 하네스
    function exposed_afterFee(uint256 amt) external view returns (uint256) { return _afterFee(amt); }
}

contract VaultDebugTest is Test {
    using stdStorage for StdStorage;
    VaultDebugHarness v;

    function setUp() public { v = new VaultDebugHarness(); }  // ① 배포 (feeBps=30 자동)

    function _seed(uint256 bal, uint256 total) internal {      // ② 상태: 변수 이름 + 키로 지정
        stdstore.target(address(v)).sig("shares(address)").with_key(address(this)).checked_write(bal);
        stdstore.target(address(v)).sig("totalShares()").checked_write(total);
    }

    function test_withdraw() public {                          // W1-a: public 함수
        uint256 amt = vm.envOr("AMT", uint256(100));           // ③ 입력 (환경변수, 기본 100)
        _seed(1000, 1000);
        uint256 out = v.withdraw(amt);                         // ④ 타깃 호출 (호출자 = 테스트 컨트랙트)
        assertEq(v.shares(address(this)), 1000 - amt);
        assertLe(out, amt);
    }

    function test_afterFee() public view {                     // W1-b: internal 함수를 하네스로
        v.exposed_afterFee(vm.envOr("AMT", uint256(100)));
    }
}
```

`setUp`, `test_*` 는 forge 의 이름 규칙이다. `vm.*`, `stdstore` 는 forge 의 EVM 이 가로채는 cheatcode 와 forge-std 라이브러리다.

### 2.3 실행

```powershell
cd C:\Users\isjeon\PycharmProjects\pythonProject\IQDebug\evaluation\baselines\playground\forge

# (확인) 테스트만 돌리기
& "$F\forge.exe" test --match-path test/VaultDebug.t.sol -vv

# W1-a 디버깅 (입력 1000)
$env:AMT = "1000"
& "$F\forge.exe" test --match-path test/VaultDebug.t.sol --match-test "test_withdraw\(\)" --debug
```

`--debug` 는 테스트가 정확히 하나만 걸려야 한다. `--match-test` 는 정규식이므로 괄호를 `\(\)` 로 쓴다.
`(soldb)` 프롬프트에서:

```text
break src/Vault.sol:25
continue
vars
continue
vars
bt
tui
```

### 2.4 실제 출력 (amt = 100)

```text
Loaded trace with 67308 steps.
Sources loaded for: VaultDebugTest (0x7fa9…1496), VaultDebugHarness (0x5615…b72f)
test/VaultDebug.t.sol:17  (step 0/67307, pc 0, PUSH1, gas 1073407008)
   17 | contract VaultDebugTest is Test {
Breakpoint #1 hit at step 65848, src/Vault.sol:25
src/Vault.sol:25 in _afterFee  (step 65848/67307, pc 564, PUSH2, gas 1056335519)
   25 |             r = r - (r * feeBps) / 10000;
warning: local variables are inferred from the legacy source map and the stack layout of solc's legacy code generator, …
uint256 amt = 100 [stack+6]
uint256 ret0 = 0 [stack+7]
uint256 r = 100 [stack+8]
uint256 i = 0 [stack+9]
State:
mapping(address => uint256) shares = <mapping; index it with [key]> [slot 0x0]
uint256 totalShares = 1000 [slot 0x1]
uint256 feeBps = 30 [slot 0x2]
…
#0  _afterFee at src/Vault.sol:25
#1  withdraw at src/Vault.sol:17
#2  VaultDebugHarness at test/VaultDebug.t.sol:11
#3  test_withdraw at test/VaultDebug.t.sol:37
#4  VaultDebugTest at test/VaultDebug.t.sol:17
```

- **[캡처]** 첫 줄과 `step 0` 의 위치: 세션은 대상 함수가 아니라 **테스트 컨트랙트 배포부터** 시작한다.
- **[캡처]** `bt`: 대상 함수 위에 테스트 함수와 테스트 컨트랙트가 쌓여 있다.
- **[캡처]** `tui` 화면.
- 67,308 스텝 중 `withdraw` 는 약 1,000 스텝이다. 나머지 대부분은 `stdstore` 가 슬롯을 찾으려고 getter 를 반복 실행한 기록이다.
  같은 일을 `vm.store` 로 하는 `test\Vault.t.sol` 의 `test_withdraw` 는 4,776 스텝이다 (슬롯을 직접 써야 하는 대신 짧다).

### 2.5 W1-b: internal 함수 직접 디버깅

```powershell
$env:AMT = "10000"
& "$F\forge.exe" test --match-path test/VaultDebug.t.sol --match-test "test_afterFee\(\)" --debug
```

같은 명령으로 `r = 10000 → 9970 → 9941` 이 보인다. `bt` 에 `exposed_afterFee`(하네스)가 나타난다. **[캡처]**
internal 함수는 이렇게 하네스 컨트랙트를 거쳐야만 직접 부를 수 있다. private 이면 이 방법도 안 된다.

### 2.6 입력 변경

- `$env:AMT = "500"` 후 같은 명령 재실행. 재컴파일 없음 (`vm.envOr` 덕분).
- 환경변수를 쓰지 않는 보통의 테스트라면 파일의 숫자를 고치고 재컴파일된다.

### 2.7 정리

`환경변수 해제: Remove-Item Env:AMT`

---

## 3. W2 — Foundry 직접 호출 (`anvil` + `cast call --debug`)

### 3.1 무엇을 하는가

테스트 파일 없이, 로컬 노드에 실제로 배포한 컨트랙트를 **`cast call` 로 직접 호출**하고 그 실행을 같은 SolDB 로 디버깅한다.
상태는 호출할 때만 덮어쓴다(`--override-state-diff`, 체인에는 쓰지 않음). 대신 **슬롯 번호**를 알아야 한다.

`cast call --trace --debug` 의 디버거는 cast 안에 라이브러리로 들어 있는 SolDB 다 (Foundry PR #17429: "Replaces the built-in debugger
with SolDB in `forge test --debug`, `forge script --debug`, `cast run --debug`, and `cast call --trace --debug`"). 별도 설치가 필요 없다.

### 3.2 실행

**창 1** (켜 둔다):

```powershell
& "C:\Users\isjeon\PycharmProjects\pythonProject\IQDebug\evaluation\baselines\forge\bin\anvil.exe"
```

테스트 계정 10개와 `Listening on 127.0.0.1:8545` 가 뜬다. **[캡처]**

**창 2**:

```powershell
cd C:\Users\isjeon\PycharmProjects\pythonProject\IQDebug\evaluation\baselines\playground\forge
. .\w2_setup.ps1          # 앞의 점 + 공백 필수 (변수 $F $RPC $ME $V $SLOT $OV 를 이 창에 남긴다)
```

출력:

```text
Deployed to: 0x5FbDB2315678afecb367f032d93F642f64180aa3
V    = 0x5FbDB2315678afecb367f032d93F642f64180aa3
SLOT = 0x723077b8a1b173adc35e5f0e7e3662fd1208212cb629f9c128551ea7168da722
OV   = 0x5FbD…0aa3:0x7230…a722:1000,0x5FbD…0aa3:0x1:1000
```

스크립트가 하는 일은 세 줄이다. 손으로 해도 같다.

```powershell
& "$F\forge.exe" create src/Vault.sol:Vault --rpc-url $RPC --private-key $KEY --broadcast   # ① 배포
$SLOT = & "$F\cast.exe" index address $ME 0                                                  # ② shares[ME] 의 슬롯
$OV = "${V}:${SLOT}:1000,${V}:0x1:1000"                                                      # ③ 주입 문자열
```

이제 세 단계로 본다.

```powershell
# (1) 반환값만
& "$F\cast.exe" call $V "withdraw(uint256)(uint256)" 1000 --from $ME --rpc-url $RPC --override-state-diff $OV
#   → 993

# (2) 호출 trace + storage 변경
& "$F\cast.exe" call $V "withdraw(uint256)" 1000 --from $ME --rpc-url $RPC --trace -vvvvv --with-local-artifacts --override-state-diff $OV

# (3) 디버거
& "$F\cast.exe" call $V "withdraw(uint256)" 1000 --from $ME --rpc-url $RPC --trace --debug --with-local-artifacts --override-state-diff $OV
```

(2) 의 출력 (amt = 100 일 때):

```text
Traces:
  [15608] Vault::withdraw(100)
    ├─  storage changes:
    │   @ 0x7230…a722: 1000 → 900
    │   @ 1: 1000 → 900
    └─ ← [Return] 100
Transaction successfully executed.
Gas used: 36812
```

슬롯 번호로만 나오고 변수 이름은 나오지 않는다. **[캡처]**

(3) 은 §0.4 와 같은 `(soldb)` 프롬프트다. 실제 출력 (amt = 100):

```text
Loaded trace with 1014 steps.
Sources loaded for: Vault (0x5fbd…0aa3)
src/Vault.sol:9  (step 0/1013, pc 0, PUSH1, gas 29978796)
Breakpoint #1 hit at step 159, src/Vault.sol:25
src/Vault.sol:25 in _afterFee  (step 159/1013, pc 488, PUSH2, gas 29976139)
uint256 amt = 100 [stack+6]
uint256 ret0 = 0 [stack+7]
uint256 r = 100 [stack+8]
uint256 i = 0 [stack+9]
State:
mapping(address => uint256) shares = <mapping; index it with [key]> [slot 0x0]
uint256 totalShares = <unknown: slot 0x1 has not been read or written yet> [slot 0x1]
uint256 feeBps = <unknown: slot 0x2 has not been read or written yet> [slot 0x2]
…  (두 번째 continue 뒤 feeBps = 30)
#0  _afterFee at src/Vault.sol:25
#1  withdraw at src/Vault.sol:17
#2  Vault at src/Vault.sol:9
```

- **[캡처]** W1 과 같은 화면인데 세션이 **1,014 스텝, 대상 호출만**이다. `bt` 에 테스트 프레임이 없다.
- **[캡처]** `<unknown: … not been read>`: 실행이 아직 읽지 않은 슬롯은 값이 있어도 표시되지 않는다.

### 3.3 입력 변경

명령의 `1000` 만 바꾸면 된다. 재컴파일·재배포 없음. warm 실행은 관찰까지 약 0.06초(실측).

### 3.4 정리

창 1 에서 `Ctrl+C`. 노드를 다시 켜면 상태가 초기화되고, 같은 계정으로 첫 배포하면 주소도 같다.

### 3.5 자주 겪는 문제

- `--with-local-artifacts` 를 빼면 디버거가 opcode 단위로만 보인다 (소스·변수 없음).
- `$OV` 를 직접 쓸 때 PowerShell 에서는 `"${V}:${SLOT}:1000"` 처럼 중괄호가 필요하다 (`$V:` 는 드라이브 표기로 해석된다).
- `w2_setup.ps1` 을 점 없이 실행하면 변수가 남지 않는다.

---

## 4. W3b — Hardhat 3 + `console.log`

### 4.1 무엇을 하는가

Hardhat 3 에는 스텝 디버거가 없다. 소스 수준에서 값을 보는 방법은 **대상 소스에 `console.log` 를 넣고 다시 컴파일·실행**하는 것이다.
실행은 JS 스크립트가 맡는다 (배포, 슬롯 주입, 호출). 멈추거나 되감는 기능은 없고, 넣어 둔 값만 출력된다.

### 4.2 파일

`contracts\VaultLogged.sol` — `Vault.sol` 과의 차이 (실제 diff):

```diff
+import "hardhat/console.sol";
-contract Vault {
+contract VaultLogged {
         for (uint256 i = 0; i < 3; i++) {
             r = r - (r * feeBps) / 10000;
+            console.log("  loop i=%s r=%s", i, r);
         }
```

`console.log` 를 25행 **뒤**에 넣었으므로 출력은 각 반복의 대입 **후** 값이다 (W1·W2 의 breakpoint 는 대입 **전**).

`scripts\w3b.js` 의 핵심:

```js
const v = await ethers.deployContract("VaultLogged");                       // ① 배포
const slotShares = ethers.keccak256(coder.encode(["address","uint256"], [me.address, 0]));
await provider.request({ method: "hardhat_setStorageAt", params: [addr, slotShares, u256(1000)] }); // ② 슬롯 직접
await provider.request({ method: "hardhat_setStorageAt", params: [addr, u256(1), u256(1000)] });
await (await v.withdraw(amt)).wait();                                       // ③ 호출 (amt = $env:AMT, 기본 100)
```

### 4.3 실행

```powershell
cd C:\Users\isjeon\PycharmProjects\pythonProject\IQDebug\evaluation\baselines\playground\hardhat
npx hardhat run scripts/w3b.js --network sim
```

노드를 따로 띄울 필요 없다 (`sim` = 프로세스 안 시뮬레이터). 첫 실행은 컴파일 때문에 몇 초 걸린다. 실제 출력:

```text
before: shares=1000 total=1000
withdraw(100)
  loop i=0 r=100
  loop i=1 r=100
  loop i=2 r=100
after : shares=900 total=900 gas=42460
```

**[캡처]** 이것이 W3b 의 전부다. 화면·프롬프트가 없다.

### 4.4 입력 변경

```powershell
$env:AMT = "1000"; npx hardhat run scripts/w3b.js --network sim; Remove-Item Env:AMT
```

### 4.5 다른 값을 보려면

예를 들어 15행의 `bal` 도 보려면 `VaultLogged.sol` 에 `console.log("bal=%s", bal);` 을 추가하고 4.3 을 다시 실행한다 (재컴파일 포함).
관찰 대상이 바뀔 때마다 소스 수정이 필요하다. **[캡처: 수정 전후 diff]**

### 4.6 참고: 디버거가 없다는 것의 확인

`npx hardhat --help` 의 task 목록에 debug 가 없다. opcode trace(`debug_traceTransaction`)는 받을 수 있지만 소스 줄·변수 이름이 없다
(`scripts\run.js` 가 스텝 수와 첫 opcode 를 출력하는 예).

---

## 5. 세 워크플로 한눈에 (Background 서술용)

| 단계 | W1 forge 테스트 | W2 Foundry 직접 호출 | W3b Hardhat 3 |
|---|---|---|---|
| 별도 작성 파일 | 테스트 `.t.sol` (+ internal 이면 하네스) | 없음 (명령만) | 계측 사본 `.sol` + 실행 스크립트 `.js` |
| 배포 | `setUp` 의 `new` | `forge create` (노드 필요) | 스크립트의 `deployContract` |
| 상태 주입 | `stdstore` (변수 이름) 또는 `vm.store` (슬롯) | `--override-state-diff` (슬롯) | `hardhat_setStorageAt` (슬롯) |
| 호출자 | 테스트 컨트랙트 (`vm.prank` 로 변경) | `--from` | signer |
| 입력 | 테스트 코드 안 (또는 env) | 명령 인자 | 스크립트 (또는 env) |
| internal 함수 | 하네스로 가능 | 불가 (public 입구 경유) | 하네스로 가능 |
| 관찰 | 멈춤·스텝·역방향, 지역·상태변수 | W1 과 동일 | 심어 둔 값만, 실행 후 출력 |
| 관찰 대상 변경 | 디버거 명령만 | 디버거 명령만 | 소스 수정 + 재컴파일 + 재실행 |
| 입력 변경 | 재컴파일 (env 면 불필요) | 인자만 | 재실행 |
| 세션 범위 | 테스트 배포·setUp·cheatcode 포함 | 대상 호출만 | 해당 없음 |

공통점: 세 경로 모두 디버깅 전에 **배포된 코드, 상태, 호출자, 입력**이라는 구체 실행 하나를 완성해야 한다. 차이는 그것을 어디에(테스트 코드 / 명령 / 스크립트), 어떤 수준으로(변수 / 슬롯) 적느냐다.
