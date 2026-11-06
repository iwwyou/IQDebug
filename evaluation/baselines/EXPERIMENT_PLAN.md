# Baseline 실험 계획 (forge / Foundry 직접 호출 / Hardhat 3)

작성 2026-11-06, 같은 날 외부 검토 2회와 추가 실측을 반영해 개정. 범위: **비교 도구 쪽 측정만.**
IQDebug 측정과 논문 논리·RQ 번호는 다른 세션 소관이다. 여기서는 축을 `준비`, `재획득`, `범위`, `관찰` 로만 부른다.
짝 문서: `RELATED_TOOLS.md`, `FOUNDRY_RELEASE_NOTES.md`, `../DAppSCAN/DATASET_RECORD.md`, `../ROADMAP.md`(D4·D5 가 이 문서의 구현 항목).

**실험의 성격: 디버깅 기능 자체의 비교.** 도구가 무엇을 요구하고, 무엇을 내주며, 얼마나 걸리는지만 잰다.
사용성이나 작업 노력은 측정하지도 해석하지도 않는다. 준비 지표는 "디버깅 세션이 성립하는 데 요구되는 것"으로만 읽는다.

**공통 관찰 목표**: 모든 시간·성공 판정의 기준은 "케이스가 지정한 지점(`observe.points`)에서 지정한 변수의 값을 얻는 것"이다.
실행 완료나 trace 로드는 목표가 아니다. 구체값과 추상값은 같은 산출물이 아니므로 시간과 값의 종류를 항상 함께 보고한다.

---

## 1. 전제 확인: 실측 (2026-11-06, Foundry 1.9.0 / Hardhat 3.21.1, Windows, Vault 및 소형 예제)

### 1.1 테스트 없는 입구

| 입구 | 테스트·하네스 | 상태 주입 | 생성자 상태 | 소스·지역변수 | 결과 |
|---|---|---|---|---|---|
| `forge script src/Vault.sol:Vault --sig "withdraw(uint256)" 100 --debug` | 불필요 | **수단 없음** | 자동 | ✅ | 잔고 0 으로 16행 REVERT (248 스텝) |
| 위 + `--fork-url <anvil>` | 불필요 | 불가 (스크립트가 새로 배포) | 자동 | ✅ | 같은 REVERT |
| `cast call … --trace --debug --override-state-diff …` | 불필요 | ✅ | 자동 | ❌ **instruction 단위만** | 1,014 스텝 RETURN |
| 위 + **`--with-local-artifacts`** | 불필요 | ✅ | 자동 | ✅ | **1,014 스텝, `amt`·`r`·`i` 표시** |
| `anvil_setStorageAt` 후 `cast call … --with-local-artifacts` | 불필요 | ✅ | 자동 | ✅ | 같음 |
| `cast send` 후 `cast run <tx> --debug --with-local-artifacts` | 불필요 | ✅ | 자동 | ✅ | 같음 (트랜잭션 실제 전송) |
| standalone `soldb run` + legacy `combined.json` | 불필요 | `--storage` | **수동** (`--runtime` 필요) | ✅ | 1,014 스텝 |

**결론: Foundry 안에서 테스트·하네스 없이 입력과 상태를 주는 경로가 있다.** 요구되는 것은 anvil 실행, 배포, 슬롯 지식이다.
standalone SolDB 는 필수가 아니다.

공통 한계: internal 함수 직접 호출은 어느 입구로도 불가 (`forge script` 도 `not implemented in your script`).
`cast` 에는 `--dump` 가 없어서 스텝 전수 추출은 불가하다. 대신 stdin 명령으로 디버거를 구동한다.

### 1.2 실험 설계에 영향을 준 실측

| 항목 | 결과 | 설계 반영 |
|---|---|---|
| `cast call` 후 상태 | anvil 슬롯 불변 | post-state 는 tracer 로 얻는다 (§5) |
| anvil `debug_traceCall` + `prestateTracer` diffMode + `stateOverrides` | 슬롯별 pre/post JSON, 트랜잭션 불필요 | 구체 기준값 경로 |
| 위 결과에서 0 으로 삭제된 슬롯 | `post` 에 없고 `pre` 에만 있음 | 정규화 필요 (§5) |
| `cast call --trace -vvvvv` | 텍스트로 storage 변경 표시. **revert 된 하위 호출의 `0 → 7` 도 표시** | 정답으로 쓰지 않는다 |
| W1 1만 회, 복원 없음 | 45건만 성공 (정답 1,001) | 상태 복원 필수 (§4.4) |
| W1 1만 회, `vm.snapshotState`/`revertToState` | 통과. 복원 없는 테스트·stdstore 테스트 포함 스위트 전체 0.79초 | 스위트 값이므로 `T_enum` 으로 쓰지 않는다 |
| `stdstore` (`shares`, `totalShares`) | `with_key` + `checked_write` 로 슬롯 없이 주입 성공 | W1 최선 경로에 포함 (§4.1) |
| `stdstore` 구조별 (소형 예제 10건) | 중첩 mapping·struct 필드·packed(플래그 필수)·view 있는 private 성공. 빈 배열 원소·string·가공값만 내는 private 실패, `vm.store` 는 성공 | 상태 입력 수준 표 (§4.1) |
| W2 다중 주입 | 슬롯 3개(private 포함, 결과 73 = 손계산), 컨트랙트 2개, 플래그 반복 모두 작동 | 주입 단위는 슬롯 |
| W2 warm (단일 프로세스 7회 중앙값) | 일반 호출 0.044초, `break`→`continue`→`vars` 0.059초 | 재획득 비교의 기준 |
| 셸에서 Python 으로 시각을 찍은 측정 | 0.14–0.22초로 부풀려짐 | 타이머는 측정 프로세스 내부 (§8) |
| 입력당 비용 (anvil, 200건) | `eth_call` 0.76ms, `prestateTracer` 0.79ms, 최소 structLog 6.6ms·85KB, 전체 structLog 10.5ms·210KB | 정답 수집을 시간 측정과 분리 (§4.4) |
| `--override-code` + `--with-local-artifacts` | 값 편집(`i < 2`)에서 소스·지역변수 연결, 804 스텝 | 값 편집에 한해 검증됨 |
| `hardhat_setCode` | 값 편집에서 804 스텝 | 값 편집에 한해 검증됨 |
| immutable 컨트랙트에 artifact 코드 교체 | `owner()` = 0 주소, `isOwner()` true → false, **오류 없음** | 교체 전 검사 (§4.3) |
| Hardhat `debug_traceCall` + `stateOverrides` | **오류 없이 무시**, revert 응답 | 쓰지 않는다. 주입 확인 단계 (§6.6) |
| Hardhat `eth_call` 세 번째 인자 override | 작동 | 상태 무변경 일괄 실행 경로 |
| Hardhat `hardhat_setStorageAt` + `debug_traceCall` | 1,014 스텝, gas 36,812 (forge 와 일치) | W3a 경로 |
| SolDB `break` 25행 첫 도달 | `r=100, i=0` (실행 직전) | IQDebug `in_state` 와 대응. 한 사례이므로 S1 에서 재확인 |
| SolDB 미접근 슬롯 | `vars` 와 `print` 모두 `<unknown>` | `V_state` 정의 (§4.5) |
| revert 실행 | W1·W2 모두 디버거 열림 (W2 228 스텝) | revert 케이스 포함 가능 |

---

## 2. 비교 대상 워크플로

| ID | 정체 | 구성 | 역할 |
|---|---|---|---|
| **W1** | forge 테스트 | 생성한 `.t.sol` (+ 필요 시 하네스) + `forge test --debug` | 테스트 기반 경로 |
| **W2** | Foundry 직접 호출 | `anvil` + `forge create` + `cast call --trace --debug --with-local-artifacts --override-state-diff` | 테스트 없는 최선 경로 |
| **W3a** | Hardhat 3 RPC 실행 | `hardhat_setStorageAt` + `debug_traceCall` / `eth_call` | 실행·재획득 |
| **W3b** | Hardhat 3 소스 계측 | `console.log` 삽입 + 재컴파일 + 실행 | 지점 관찰 |
| W4 (보조) | standalone SolDB | `soldb run --runtime --storage` + legacy `combined.json` | 노드 없는 경로. **준비 축에만** 한 행 추가. `--json` 계측 도구 겸용 |

W3 은 Hardhat 전체가 아니라 이 두 경로만 대표한다. Hardhat Solidity 테스트(W3c)는 W1 과 준비가 거의 같으므로 S0·S1 에서 작동만 확인한다.
W3a 는 소스 수준 지역변수를 내주지 않는다. 이것은 성능 실패가 아니라 기능 지원 수준으로 기록한다.
Remix 는 정성 표로만 다룬다. W1·W2·W4 는 SolDB 탐색 계층을 공유하므로 **탐색 기능 지원 여부는 W1 에서 한 번만** 조사한다.

**최선-경로 원칙**: baseline 은 항상 도구가 제공하는 가장 빠른 자동화로 측정한다 (`stdstore`, 캐시 유지, env 파라미터, 스냅샷 재사용, code override 등).
느린 변형은 부가 행으로만 둔다. baseline 에 유리한 조건에서도 성립하는 결과만 주장한다.

---

## 3. 공통 입력: 케이스 명세

모든 도구(IQDebug 포함)가 같은 파일을 읽는다. **다른 세션과의 계약은 이 스키마다.**

```json
{
  "case_id": "...", "file": "...", "contract": "Vault", "function": "withdraw(uint256)",
  "tier": "T1", "solc": "0.8.37", "evm_version": "default", "optimizer": false, "via_ir": false,
  "ctor_args": [], "sender": "0xf39F...", "value": 0,
  "context": {},
  "params": {"amt": 100},
  "state": [{"path": "shares[sender]", "value": 1000}, {"path": "totalShares", "value": 1000}],
  "externals": [],
  "expected_termination": "return",
  "observe": {
    "exit": ["ret0", "shares[sender]", "totalShares"],
    "points": [{"line": 25, "phase": "before", "iteration": 0, "vars": ["r", "i"]}]
  },
  "range": {"amt": [0, 1000]}
}
```

- `state` 는 **변수 경로**로 적는다. 슬롯 변환 방식은 워크플로별로 다르며, 그 차이가 측정값이다 (§4.1).
- `expected_termination` 은 `return` / `revert` / `mixed` (구간 안에 둘이 공존) 중 하나다. 정상 종료는 필수 조건이 아니다. revert 도 정식 디버깅 대상이다.
- `phase` 기본값은 `before` 이며 IQDebug `in_state` 에 대응한다. `iteration` 은 같은 줄의 몇 번째 도달인지를 뜻한다.
  IQDebug 가 반복별 상태를 구분하지 않는 지점은 값 일치 대신 구체값의 포함 여부로 대조한다.
- `observe.exit` 는 학위논문 `O_exit` 와 같다. `return` 케이스에서만 post-state 를 비교한다.
- `context` 는 공통 기본 문맥(§6.5)에서 달라지는 항목만 적는다.

상태 복원은 필드가 아니라 고정 규칙이다: **모든 입력은 같은 초기 상태에서 실행한다.**

---

## 4. 측정 지표

### 4.1 준비 요구량

디버깅 세션이 성립하는 데 요구되는 항목을 잰다. 합산 점수를 만들지 않으며 개별 분포만 보고한다.

| 지표 | 정의 | 수집 |
|---|---|---|
| `R_input` | 구체값이 요구되는 입력 수 (파라미터, sender, value) | 케이스 명세 |
| `R_state` | 구체값이 요구되는 상태 항목 수 | 케이스 명세 |
| `R_env` | 구체값이 요구되는 환경 항목 수 (블록 문맥, 대상 주소) | 케이스 명세 |
| `R_dep` | 구성이 요구되는 외부 의존 수 | 케이스 명세 |
| `C_slot` | 상태 주입에 필요한 storage binding 구조. 단순·mapping·중첩·packed·동적 분리 집계. 자동화 여부와 무관하게 0 이 되지 않는다 | layout |
| `A_slot_tool` | **도구가 기본 제공하는 기능**으로 해결한 binding 수 (`stdstore`, `cast index` 등) | 생성기 로그 |
| `A_slot_harness` | **이 실험의 하네스가 대신 계산한** binding 수. 도구의 기능으로 해석하지 않는다 | 생성기 로그 |
| `A_env` | 환경 작업 수: 노드 기동, 배포, 생성자 인자, 라이브러리 링크, mock 컨트랙트, 프록시 | 생성기 로그 |
| `A_access` | 접근 경로 산출물: 하네스 계약, 노출 함수, public 입구 경유 여부 | 생성기 로그 |
| `E_src` | 대상 소스 수정 줄 수 (`console.log` 등). 관찰 변수 수에 비례 | 생성기 로그 |
| `L_add` | 자동 생성물 줄 수. 정규화(`forge fmt`) 후, import·공백 제외. 추가 산출물의 크기만 뜻한다 | 산출물 |
| `F_cli` | 명령 인자 개수·슬롯 인자 개수 (W2·W4 용) | 명령 |

`L_add` 는 단독으로 쓰지 않는다. W2·W4 가 0 이 되기 때문이다.
private 은 `vm.store` + layout 경로로 주입 가능하며, 이때는 `A_slot_tool` 이 아니라 슬롯 지식 요구로 센다.

**상태 입력 수준** (구조 × 워크플로 기능 표. 준비 축의 핵심 비교):

도구가 상태 입력을 어떤 수준에서 받는지 기록한다. 이것은 작업량이 아니라 입력 인터페이스의 기능 차이다.
값은 `변수`(도구가 변수 경로를 받음), `슬롯`(슬롯·offset 수준으로 내려가야 함), `불가` 중 하나다.
구조는 단순, 1단 mapping, 중첩 mapping, struct 필드, packed, 동적 배열, string 이고 각각 public / private 을 구분한다.

| 구조 | W1 (`stdstore`, 2026-11-06 실측) | W2 | W3 | W4 |
|---|---|---|---|---|
| 단순, public | `변수` | `슬롯` | `슬롯` | `슬롯` |
| 1단 mapping, public | `변수` | `슬롯` (`cast index`) | `슬롯` | `슬롯` |
| 중첩 mapping, public | `변수` (`with_key` 반복) | `슬롯` (키마다 반복) | `슬롯` | `슬롯` |
| struct 필드, public | `변수` (`depth`) | `슬롯` | `슬롯` | `슬롯` |
| packed, public | `변수` (`enable_packed_slots` 필수) | `슬롯` (한 슬롯 합성) | `슬롯` | `슬롯` |
| 동적 배열 원소 (빈 배열) | `슬롯` (`Slot(s) not found`) | `슬롯` (길이·원소 분리) | `슬롯` | `슬롯` |
| string | `슬롯` (`Slot(s) not found`, 전용 쓰기 함수 없음) | `슬롯` | `슬롯` | `슬롯` |
| private, 값 그대로 반환하는 view 있음 | `변수` | `슬롯` | `슬롯` | `슬롯` |
| private, 가공값만 반환 | `슬롯` (`vm.store`) | `슬롯` | `슬롯` | `슬롯` |

W1 열은 소형 예제 실측이다. W2·W4·W3 열과 packed·동적 배열·string 의 실제 주입은 S1 에서 확인한다.
`stdstore` 는 forge-std 테스트 라이브러리이므로 W2·W3 에는 없다. 즉 Foundry 안에서도 **테스트를 없애면 입력이 슬롯 수준으로 내려간다.**
`forge inspect <C> storage-layout` 이 슬롯·offset 을 내주므로 "계산이 어렵다"가 아니라 "입력 인터페이스의 수준이 다르다"로만 해석한다.
IQDebug 열은 다른 세션이 같은 구조로 채운다 (§7). 변수 경로로 받을 수 있는지와 그 값이 구체 실행과 맞는지를 함께 확인해야 비교가 성립한다.

**실행 결과** (케이스·워크플로별 필수 기록. 서로 독립인 필드로 둔다):

| 필드 | 값 |
|---|---|
| `setup` | `OK` / `F_COMPILE` / `F_DEPLOY` / `F_ACCESS` / `F_STATE` / `F_PRECOND` / `F_TIMEOUT` |
| `termination` | `return` / `revert` / `none` |
| `goal_reached` | 관찰 목표 달성 여부. `revert` 여도 지점이 앞서면 참일 수 있다 |
| `cause` | 실패 시 `tool_unsupported` / `harness_incomplete` / `dataset` / `resource` |
| `dep_mode` | `none` / `real` / `mocked` / `opaque` |

`F_PRECOND` 는 실행 문맥을 만들지 못한 경우다. 도구 실행 실패와 합치지 않는다. revert 는 실패가 아니다.
`dep_mode` 가 다른 성공을 하나의 멀티컨트랙트 지원률로 합산하지 않는다. S1 의 실패는 원인을 수동 확인한다.

### 4.2 시간

모든 값은 구간별로 쪼개 기록한다: `compile` / `node` / `deploy` / `state` / `instrument` / `execute` / `trace_load` / `first_obs`.

| ID | 시나리오 | 측정 |
|---|---|---|
| `T_cold` | 캐시 없음 | 컴파일 포함, 공통 관찰 목표 달성까지 |
| `T_warm` | 캐시·노드 유지 | 재실행, 공통 관찰 목표 달성까지 |
| `T_dbg1` | `build` 직후 첫 `--debug` | 디버그 빌드 분리 비용 |

일회성 환경 준비(forge-std, npm install)는 제외하고 별도 표기한다.
`--with-local-artifacts` 는 매 호출 컴파일 여부를 확인하므로, 프로젝트 크기에 따른 warm 시간 변화를 S1 에서 본다.

### 4.3 재획득

**입력 변경** (같은 소스, 입력 1개 변경 후 같은 관찰):

| 변형 | W1 | W2 | W3a | W3b |
|---|---|---|---|---|
| 기본 | 테스트 리터럴 수정 + 재컴파일 | `cast call` 인자만 변경 | RPC 인자만 변경 | 스크립트 인자 변경 + 재실행 |
| 최선 | `vm.envUint` 파라미터화 → 재컴파일 없음 | 같음 | 같음 | 상주 노드 + `evm_snapshot`/`evm_revert` |

**소스 편집**: 값 / 연산자 / 구조 3분류 (학위논문 프로토콜) × 호환성 2분류. 시나리오당 타깃별 고정 변이 1개.

- **E1**: storage layout 과 배포 의미론을 유지하는 편집.
- **E2**: layout, 생성자, 상속 구조 등이 달라지는 편집.

| | W1 | W2 | W3 |
|---|---|---|---|
| 필요 단계 | 재컴파일 + 재실행 | 재컴파일 + 재배포 + 재주입 + 호출 | 재컴파일 + 재배포 + 재주입 + 호출 |
| 최선 (E1) | 증분 컴파일 | `--override-code` | 상주 노드 + `hardhat_setCode` |

**code override 허용 규칙**: 다음을 모두 통과하면 override, 아니면 재배포한다.
layout 동일, 생성자 동일, `immutableReferences` 비어 있음, `linkReferences` 비어 있음, 자기 코드 조회(`codehash`, `extcodesize`) 없음.
override 는 값 편집에서만 검증되었다. 연산자·구조 편집은 S0 에서 확인한다.

기록: 재컴파일 파일 수, 슬롯 재계산 필요 여부 (상태변수 추가 시), 디버거 위치 보존 여부 (SolDB 는 세션 종료이므로 breakpoint 재설정 명령 수), override 사용 여부와 거부 사유.

### 4.4 범위

입력 영역 크기 `N ∈ {1, 10, 100, 1000, 10000}` (구간 폭 × 슬롯 조합) + **열거 불가 구간** (넓은 구간, 다중 파라미터 곱).

이 축의 주장은 속도가 아니다. N≤10,000 에서 W1 은 1초 안쪽, W2 는 약 8초(외삽)로 baseline 이 지지 않을 수 있다.
중심은 구체 결과의 추상 결과 포함 여부와 열거 불가 구간이며, 속도 차이는 있는 그대로 보고한다.

| 측정 | 방법 |
|---|---|
| `T_single` | 입력 1개 실행 |
| `T_enum(N)` | 영역 전수 실행 시간. **같은 초기 상태 복원 필수.** 외삽값은 실측과 구분 표시. 정답 수집은 포함하지 않는다 |
| 최선 경로 | W1: 입력 배열 + `vm.snapshotState`/`revertToState`. W2: 상주 anvil + `eth_call`. W3a: 상주 노드 + `eth_call` override |
| 기록량 | 전수 실행에서 디버거로 볼 수 있는 실행 수 (`--debug` 는 1건) |
| 산출 | **구체 정답** (§5). `T_enum` 과 별도 실행으로 수집 |

유한 영역 전수와 표본 N 개는 구분해서 보고한다. 표본 포함은 soundness 의 증거일 뿐 증명이 아니다.
이 산출물이 IQDebug 의 포함 검사 입력이다 (§7). N=1 은 baseline 이 이길 수 있으므로 반드시 포함한다.
`fuzz` 비교(`--fuzz-runs {8, 256, 1024}`)는 전수·포함 결과가 확보된 뒤의 부가 분석이다.

### 4.5 관찰 능력

| 지표 | 정의 |
|---|---|
| `V_local` | 관찰 지점의 스코프 내 지역변수 중 값이 표시된 비율 |
| `V_state` | **대상 컨트랙트의 관찰 대상 상태변수** 중 디버거가 그 지점에서 값을 표시한 비율 (`<unknown>` 은 미표시). forge-std 상속 변수는 분모에서 제외 |
| `V_acc` | 독립 검증 가능한 표시 값 중 정답과 일치하는 비율. **분모(검증 가능 수)와 함께 보고** |
| `V_frame` | 실행된 호출 중 source 수준 프레임으로 복원된 비율 |
| 호출 내부 | `-vvvv --decode-internal`, Hardhat `-vvvv` 가 보여 주는 범위 |

`V_state` 의 외부 조회 가능 여부는 주입 방식별로 따로 적는다. override 로 주입한 값은 체인에 없어 `cast storage` 로 볼 수 없고, `anvil_setStorageAt` 은 볼 수 있다.
컴파일 설정별(기본 / optimize / via-IR / via-IR+optimize) `V_local` 은 표본 일부에서 측정한다.
SolDB 의 지역변수는 추론값이므로 `V_acc` 없이 "보인다"고 단정하지 않는다.
`V_acc` 정답은 `console.log` 삽입본이다. 바이트코드가 달라지므로 gas, code size, 코드 해시에 의존하는 변수와 최적화로 제거된 변수는 검증 불가로 분류한다.

### 4.6 탐색

| 지표 | 정의 |
|---|---|
| `Ops_goal` | 타깃 진입 후 관찰 목표 달성까지 **operation 수** (`break`+`continue`+`vars` 최선 경로 기준) |
| `Ops_step` | `next` 만 썼을 때 operation 수 (보조) |
| `Steps_target` / `Steps_session` | 타깃 구간 / 세션 전체 스텝. **서로 다른 값으로 보고, 조작 수로 해석 금지** |
| `L_session` | 디버거가 조작 가능해질 때까지 |
| `L_goal` | 관찰 목표 달성까지 |
| `L_op` | 로드된 세션의 개별 operation 응답. W4 `--json` 측정값이며 standalone 값으로 표기. W1 과의 동등성은 소규모로만 확인 |
| `Mem` | 세션 peak RSS, 번들 크기 |
| 지원 표 | 역방향, 조건부 breakpoint, 쓰기 감시 등 operation 지원 여부 |

지원하지 않는 operation 은 수치를 부여하지 않는다 (0 또는 ∞ 금지). W3 은 `해당 없음` 으로 둔다.
SolDB 의 step 은 구체 trace 위, IQDebug 의 step 은 추상 CFG 위의 이동이므로 수치로 직접 비교하지 않는다. 비교는 지원 여부(기능)와 `L_goal`·`Ops_goal`(목표 기준)로 한다.

---

## 5. 정답과 관찰값 추출

**정답 수준**

| 수준 | 내용 | 범위 | 출처 |
|---|---|---|---|
| G1 | 종료 형태, 반환값 | 전체 필수 | `debug_traceCall` 결과 |
| G2 | post-state | 전체, 성공률 보고 | `prestateTracer` diffMode + 정규화 |
| G3 | 지점별 변수값 | 계측 가능한 표본 | `console.log` 삽입본, SolDB 대조 |
| 방문 분기 | 입력별 실제 분기 | 범위 실험 대상 | 최소 structLog + 소스맵 해석 |

**구체 기준값은 anvil `debug_traceCall` 결과로 정의한다.** 별도 실행 엔진은 만들지 않는다.
다만 도구들이 EVM 구현을 공유할 수 있으므로 EVM 자체의 정확성까지 독립 검증했다고 주장하지 않는다.
S0·S1 에서는 실제 트랜잭션 실행 결과와 대조한다.

**G2 정규화**: 관심 슬롯이 `post` 에 있으면 그 값, `pre` 에만 있으면 0, 둘 다 없으면 초기값으로 해석한다. 슬롯은 케이스 경로에서 역으로 계산해 대조한다.
`-vvvvv` 출력은 revert 된 쓰기까지 표시하므로 정답으로 쓰지 않는다.

**방문 분기 수집**: structLog 는 입력당 약 85KB 이므로 저장하지 않고 스트림으로 분기 지점만 남긴다.

| | 지역변수 / 중간 지점 | 정상 종료 시 값 |
|---|---|---|
| W1 | 명령 파일 `break <line>` → `continue` → `vars` | 번들 `trace.json` 의 타깃 `exit_step` → `goto` → `vars`, `print <path>` |
| W2 | 같은 명령을 stdin 으로 | 디버거 표시는 `V_*` 측정용. 값 정답은 위 G2 |
| W3a | 지원 없음 (`해당 없음`) | G1·G2 |
| W3b | 소스 `console.log` | 호출 후 getter, `eth_getStorageAt` |
| W4 | `-x … --batch --json` | 같음 |

디버거 출력 파서는 공용 모듈 하나로 둔다. PowerShell 파이프는 BOM 문제가 있으므로 Python `subprocess` 에 bytes 로 넘긴다.

---

## 6. 대상 선정과 준비 자동화

### 6.1 단계

| 단계 | 대상 | 통과 기준 |
|---|---|---|
| S0 | `Vault` 전 워크플로 E2E | G1·G2 전 워크플로 일치, G3 일치 지점 확보 |
| S1 | 손으로 고른 8개 (leaf / 호출 / 루프 / modifier / 상속 / library / 외부 호출 / 생성자 인자) | 지표 수집 완성, 실패 원인 수동 확인 |
| S2 | 계층 파일럿 20개 | **자동 준비 성공률 추정 → 범위 결정** |
| S3 | 자동 준비 성공 타깃 전부 | 성공률 보고 |

S2 성공률이 낮으면 S3 은 계층 표본으로 내린다. 층: T1/T2, 호출 유무, 루프 유무, 외부 호출 유무.
**루프 타깃을 제외하지 않는다.**

### 6.2 컴파일·상태변수 선행 조사

569 파일의 컴파일 성공은 미검증이다 (`DATASET_RECORD` §10). 측정 전에 다음을 수행한다.

- 스냅샷별 격리 디렉터리 + 의존 폐포 + 허용 최고 solc. `evaluation/solc` 의 바이너리는 0.8.0–0.8.13 일부와 0.8.38 뿐이다 (0.8.37 은 없음. 필요 시 svm 또는 fetch_solc.py 로 보충).
- 원본 소스를 수정하지 않는다. 정규화 입력(`normalize_source.py`)은 쓰지 않는다.
- Hardhat 은 프로젝트 config 를 케이스별로 생성한다. WASM solc 사용 여부를 기록한다.
- **solc 버전은 케이스별로 같고, 도구 간 동일**하게 고정한다.
- **상태변수 구조 빈도**: 타깃이 읽는 상태변수를 §4.1 구조별로 센다 (getter 유무 포함). 단순·1단 mapping 이 대부분이면 입력 수준 차이의 효과는 작으므로, 이 빈도가 준비 축 주장의 크기를 정한다. 실험 진행 중 파악한다.

### 6.3 구체값 찾기

케이스는 `return`, `revert`, `mixed` 를 모두 포함한다. 아래 탐색은 `return` 케이스를 구성하기 위한 절차다.

1. 케이스 명세 값 (다른 세션의 입력 생성기 D1 과 공유 가능)
2. 조건식에서 추출한 상수·경계값
3. `forge` fuzz 로 revert 없는 입력 탐색 (시드 고정, 예산 고정)
4. 실패 시 `F_PRECOND` 로 기록하고 제외하지 않는다

탐색 비용은 준비 시간에 넣지 않고 별도 보고한다. 값이 주어졌다고 가정하는 편이 baseline 에 유리하기 때문이다.

### 6.4 외부 의존

- 외부 호출 타깃은 실질 타깃의 약 24.4% (추정).
- `real` 을 우선한다. 불가하면 W1: `vm.mockCall` 또는 mock 컨트랙트. W2: 대상 주소에 `--override-code`, mock 배포. W3: mock 배포.
- mock 은 호출당 `A_env` 로 세고 `dep_mode` 를 `mocked` 로 표시한다. 값은 케이스 `externals` 에서 가져온다.
- 대형 프로젝트처럼 mock 이 많으면 `F_STATE` 로 처리하고 사유를 남긴다.

### 6.5 공통 실행 문맥

기본 문맥을 한 번 정의하고 케이스 `context` 로만 덮어쓴다. 실제 사용한 값은 결과에 전부 기록한다.

- **고정**: `block.timestamp`, `block.number`, `block.chainid`, `msg.sender`, `tx.origin`, `msg.value`, hardfork.
  W1 은 `--block-timestamp`, `--block-number`, `--chain`, `--tx-origin`, `--sender`. W2 는 anvil `--timestamp`, `--number`, `--chain-id`, `--hardfork`. W3 은 Hardhat 설정 (확인 필요).
- **기록만**: basefee, prevrandao, coinbase, gas limit, 배포 주소. anvil 에서 앞의 일부는 플래그가 확인되지 않았고, 직접 `gasleft` 를 쓰는 타깃이 0 건이다.
  `address(this)` 에 의존하는 타깃은 사유를 표시한다.

### 6.6 주입 확인

API 성공과 의미 성공을 구분한다. 디버깅 실행 전에 주입한 값을 getter 또는 storage 조회로 확인한다.
override 경로처럼 조회가 불가능하면 `prestateTracer` 의 `pre` 로 확인한다. 이 단계는 시간에서 제외하고 성공 판정에 포함한다.

Hardhat 3.21.1 의 `debug_traceCall` `stateOverrides` 는 쓰지 않는다. `eth_call` override 결과와 `debug_traceCall` 결과를 같은 실행으로 가정하지 않는다.

### 6.7 접근 규칙

| 타깃 | W1 | W2 / W4 | W3 |
|---|---|---|---|
| public/external | 직접 | 직접 | 직접 |
| internal | 상속 하네스 | public 입구가 있으면 경유, 없으면 `F_ACCESS` | 상속 하네스 |
| private | **소스 수정 필요** → `E_src` 집계 또는 public 경유 | public 경유 | 동일 |
| library internal | 호출 래퍼 | 경유만 | 호출 래퍼 |
| abstract | 구체화 하네스 | `F_DEPLOY` | 구체화 하네스 |
| storage 참조 파라미터 | 전용 하네스 | 경유만 | 전용 하네스 |

public 입구 경유는 입구의 전제조건까지 만족시켜야 한다. 그 항목도 `R_input`, `R_state` 에 들어간다.

---

## 7. 다른 세션에 넘기는 산출물

| 파일 | 내용 |
|---|---|
| `harness/cases/*.json` | §3 케이스 명세 |
| `harness/results/<workflow>.jsonl` | 케이스·반복별 지표와 실제 사용 문맥 |
| `harness/truth/<case>.jsonl` | 입력별 구체 정답 (G1, G2, 방문 분기, 가능한 경우 G3) |

IQDebug 쪽은 같은 케이스에 대해 §4.1 의 대응 지표(채운 슬롯 수 등)와 상태 입력 수준 표의 IQDebug 열, 공통 관찰 목표 시간, 재획득 시간, 분기 판정을 내면 된다.
포함 검사, 과잉근사 정도, 거짓 feasible 집계는 양쪽 데이터를 합쳐 계산한다.

---

## 8. 측정 절차

- **고정**: forge 1.9.0, soldb 0.4.5, Hardhat 3.21.1 또는 3.21.2 (결정 필요), forge-std v1.16.2, Node v22.21.1, OS·CPU·RAM 기록.
- **타이머**: 측정 프로세스 내부의 `perf_counter`. 셸에서 인터프리터를 띄워 시각을 찍지 않는다.
- **반복**: 케이스당 10회. 첫 회는 warm-up 으로 분리. cold 는 매회 캐시·산출물 삭제. 케이스 대표값은 반복의 중앙값이며, 반복을 독립 표본으로 쓰지 않는다.
- **격리**: 다른 세션의 벤치와 동시에 돌리지 않는다. Defender 실시간 검사 영향 확인. anvil 포트 고정, 매 케이스 새 노드.
- **제한**: 케이스 타임아웃 120초 (S1 에서 재조정).
- **통계**: 케이스별 쌍체 비율(또는 log-ratio)의 중앙값, 프로젝트 단위 cluster bootstrap 신뢰구간. 유의성은 필요 시 Wilcoxon signed-rank. micro 와 프로젝트별 macro 를 함께 보고.
- **실패**: 성공률을 시간과 함께 보고한다. 타임아웃은 검열 자료로 취급한다. 공통 성공 집합을 명시한다.
- **기록**: 원시 stdout/stderr, 명령, 버전, 커밋을 전부 보존한다.

---

## 9. 비교 방법

| 비교 | 방식 | 주의 |
|---|---|---|
| 입력 수준 | 구조 × 워크플로 기능 표 + 구조 빈도 | 작업량으로 해석하지 않는다. IQDebug 지원 범위를 같은 표에 둔다 |
| 준비 | 같은 케이스의 지표 쌍 비교 | 단일 합산 점수를 만들지 않는다. 입력 구간·⊤ 로 요구량이 줄면 정밀도도 함께 보고 |
| 첫 관찰 시간 | 공통 성공 집합 위 쌍체 | N=1 을 숨기지 않는다 |
| 입력 변경 | 기본 / 최선 변형 모두 | 최선 변형 기준으로 주장 |
| 소스 편집 | 3분류 × E1/E2 | 수술 미지원 편집은 IQDebug 측 실패로 집계 |
| 범위 | N 곡선 + 포함 + 정밀도 + 열거 불가 구간 | 구체값 N 개와 구간 1개는 **같은 산출물이 아니다** |
| 관찰 | 비율 표 | 값의 종류가 다름을 명시 (구체 vs 추상) |

**우선순위**

- 필수: 준비 요구량, 첫 관찰, 입력 변경, 소스 편집, G1·G2, 지원 성공률, 입력 범위와 구체 정답.
- 가능하면: `V_*`, 탐색 목표 지표, 의존 모드별 지원 범위.
- 보류 가능: fuzz 대규모 비교, 전체 데이터셋 G3, operation 미세 지연.

---

## 10. 위협 요소

- **측정 범위**: 도구 기능만 비교한다. 사용성과 작업 시간은 측정하지 않으며 주장하지도 않는다.
- **생성기 하한**: 생성물은 최소 구성이다. baseline 에 유리한 편향이므로 그대로 둔다.
- **하네스 자동화**: `A_slot_harness` 의 해결은 도구 기능이 아니다. `A_slot_tool` 과 합산하지 않는다.
- **상태 입력 비교의 양면성**: IQDebug 가 해당 구조를 지원하지 못하면 이 비교는 불리하게 작용한다. 지원 범위를 측정 전에 확정한다.
- **기존 테스트**: 데이터셋에 테스트가 있는 스냅샷이 있다 (`*.t.sol` 50개, `test/` 하위 파일 1,534개, 실행 가능 여부 미확인).
  기존 테스트가 타깃에 닿으면 준비 요구량이 작아진다. 재사용 가능한 타깃 비율을 **민감도 분석**으로 표본 측정한다.
- **W2 의 빠른 재획득**: 소형 Vault 측정값이다. 프로젝트 규모별로 다시 잰다.
- **code override 의미 보존**: 허용 규칙 밖의 조건이 남을 수 있다. S1 에서 재배포 결과와 대조한다.
- **구체 기준값**: 도구 간 EVM 구현 공유 가능성이 있다. 실제 트랜잭션 대조는 S0·S1 에 한정한다.
- **도구 변화**: 전부 active development. 버전·조회일 고정.
- **Windows**: 프로세스 기동 비용이 다르다. 구간별 시간으로 분리 보고한다.
- **추론 지역변수**: SolDB 값은 추정이다. `V_acc` 로 검증한다.
- **표본 대표성**: 감사된 공개 소스 집합으로 한정한다. 프로젝트 쏠림 존재.
- **Hardhat 대표성**: W3a·W3b 가 Hardhat 전체를 대표하지 않는다. 플러그인 디버거는 범위 밖으로 명시.

---

## 11. 구현 순서 (`harness/` 신규)

| 순서 | 항목 | 산출 | 통과 조건 |
|---|---|---|---|
| 1 | 케이스 스키마, 공통 문맥, 공용 디버거 구동기·파서 | §3, §5, §6.5 | 같은 초기 상태 표현 |
| 2 | Vault S0: W1–W4 E2E + G2 정규화 + 주입 확인 | §5, §6.6 | G1·G2 일치, revert 포함 |
| 3 | 관찰 시점 검증 | §3 | 분기·호출·루프에서 `before` 대응 |
| 4 | 컴파일·상태변수 선행 조사 | §6.2 | 성공률, `F_COMPILE` 사유, getter 없는 변수 비율 |
| 5 | layout → 슬롯 변환기, 상태 입력 수준 표 검증 | `C_slot`, `A_slot_*`, §4.1 표 | 단순·mapping·중첩·struct·packed·동적·string 통과 |
| 6 | 생성기: W1 테스트·하네스, W2 명령열, W3 스크립트 | §4.1 | |
| 7 | S1 → S2 파일럿 | **범위 결정** | |
| 8 | 재획득·범위 측정기, 구체 정답 덤프 | §4.3, §4.4, §7 | 입력별 같은 초기 상태 |
| 9 | S3 전체 실행 + 집계 | 결과표 | 실패 포함 |

### 시작 전에 미리 검증할 항목

- `--override-code`, `hardhat_setCode` 의 연산자·구조 편집 (값 편집만 검증됨)
- immutable 값을 `immutableReferences` 위치에 패치하는 경로 (미시험. 되면 허용 규칙 완화)
- Hardhat `prestateTracer` 의 상태 주입 후 post-state (미시험. diff 응답 자체는 확인)
- 다중 호출 시퀀스 필요 케이스 (`approve` 후 `transferFrom` 처럼 선행 호출이 상태를 만드는 경우. 현재 설계는 상태를 직접 주입)
- 멀티컨트랙트에서 `--with-local-artifacts` 가 모든 주소의 소스를 잇는지
- `cast` stdin 구동의 시간 측정 신뢰도
- Hardhat 3 의 블록 문맥 고정 방법, anvil 의 prevrandao·coinbase 지정 가능 여부
- Hardhat 3 이 데이터셋의 구버전 solc 를 정상 처리하는지
