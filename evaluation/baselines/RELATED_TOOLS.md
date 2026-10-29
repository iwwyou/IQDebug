# 기존 스마트 컨트랙트 디버거 — 작동 방식·기능·차별점 조사 기록

작성 2026-10-29. 용도: 논문의 *Background / Related Work / Evaluation(baseline 선정·threats)* 작성 근거.
짝 문서: `README.md`(설치·실행·실측 출력), `../DAppSCAN/DATASET_RECORD.md`(모집단).

**근거 등급을 반드시 구분해서 쓴다.**

| 등급 | 뜻 |
|---|---|
| **[실측]** | 이 저장소에서 직접 돌려 확인(2026-10-29). 버전 명시. 논문에 수치로 쓸 수 있다 |
| **[문서]** | 공식 문서·저장소 README·PR 설명. 조회일 명시. 논문에 "according to" 로 쓴다 |
| **[미확인]** | 아직 돌려 보지 않았다. 논문에 단정해서 쓰면 안 된다 |

---

## 1. 다섯 도구의 작동 방식

### 1.1 Foundry — `forge test --debug` **[실측: forge 1.8.6]**

- 테스트 함수 하나를 **구체 실행**하고 전 과정을 기록한 뒤, TUI 에서 그 기록 위를 전후로 움직인다.
- 소스 연결은 **solc source map**(`pc → 소스 바이트 범위`). 스텝 레코드 자체에는 소스 줄이 없다.
- `--debug --dump <path>` 로 **비대화형 덤프**가 가능하다 → 평가 하네스에서 쓸 수 있다.
- 구조: `{contracts: {identified_contracts, sources}, debug_arena: [frame]}`,
  `frame = {address, kind, calldata, returndata, gas_limit, step_offset, decoded, contract_name, steps}`,
  `step = {pc, op, stack, push_stack, memory, returndata, gas_*, storage_change, status, immediate_bytes, decoded}`.
- 전제: 컴파일되는 Foundry 프로젝트 + 테스트 함수 + (internal 타깃이면) 상속 harness +
  (setter 없는 상태변수면) 슬롯 계산 + `vm.store`.
- 상태 주입: cheatcode(`vm.store`/`vm.deal`/`vm.warp`/`vm.prank`).
- 입력 영역: `fuzz` = 무작위 **점** N개(기본 256). `--debug` 는 fuzz 실패 시 **첫 반례**, 실패 없으면 마지막 케이스를 연다.

**측정값** (`Vault.withdraw` — 6줄 함수, `test_withdraw()` 1회):

| | 값 |
|---|---|
| 총 디버거 스텝 | **4,601** |
| 콜 프레임 | **18** |
| 타깃 호출 프레임(`VaultHarness.withdraw(100)`) | **1,014 스텝** |

### 1.2 Hardhat 3 **[실측: 3.21.1]**

- **인터랙티브 스텝 디버거가 없다.** `npx hardhat --help` 의 task 전체 =
  `build clean compile console flatten node run telemetry test` (+ `test solidity`). `debug` 없음.
- 소스 수준 관찰 수단은 `console.log`(= **소스를 고치고 재컴파일·재배포·재실행**) 하나.
  그 아래 수준은 `debug_traceTransaction` 의 opcode trace.
- **HH3 에서 새로 생긴 것(중요)**: Foundry 호환 **Solidity 테스트**. `vm.store`·`vm.expectRevert`·
  `bound`·256회 fuzz 가 **그대로 동작함을 실측**했다. 런타임은 EDR(Rust).
- → **HH2 기준으로 쓴 "상태 주입은 forge=cheatcode / Hardhat=JSON-RPC" 구분은 HH3 에서 무효.**
  논문에서 Hardhat 을 "cheatcode 가 없어서 불리한 도구" 로 묘사하면 사실과 다르다.
- 전제: 컴파일되는 프로젝트 + 스크립트/테스트. internal 타깃은 forge 와 같이 상속 harness.
- 상태 주입: Solidity 테스트의 cheatcode, 또는 JSON-RPC `hardhat_setStorageAt`/`evm_setNextBlockTimestamp` **[실측]**.

### 1.3 Remix **[문서, 조회 2026-10-29]** — 정성 비교만

- 브라우저 IDE 내장 디버거. 배포 후 **트랜잭션 하나를 재생**하고 solc source map 으로 소스에 올린다.
- step into/over/back, locals·state·stack·memory·storage 조회.
- 전제: 배포 + 트랜잭션. 측정 재현성이 낮아(브라우저·수동) **정량 비교 대상에서 제외**한다.
- **[미확인]** 직접 돌려 보지 않았다. 기능 서술은 공식 문서 기준으로만 쓴다.

### 1.4 Simbolik (Runtime Verification) **[문서, 조회 2026-10-29]**

- VS Code / Cursor 확장 + **Simbolik API server** + `kontrol-node`(RV 의 EVM 실행 엔진, `kup install kontrol-node`).
- 공개된 동작은 **구체 실행 + trace lifting** 이다. DAP 서버를 오픈소스화한 PR #90 기준 패키지 구성:
  `protocol`(와이어 타입) · `engine`(JSON-RPC, launch/attach) · `lifting`(kontrol-node 트레이스 정규화) ·
  `solc`(컴파일 산출물·source map·바이트코드→컨트랙트 식별) · `ethdebug-gen`(변수가 스택 어디에 있는지) ·
  `debugger`(스텝·스코프·프레임·breakpoint) · `sources`(Sourcify 에서 검증 소스 가져오기).
- 세션 흐름: 로컬 노드 기동 → 테스트 컨트랙트 배포 → 계정 prefund → `setUp` 실행 →
  `kontrol_traceTransaction` 류로 트랜잭션 재생 → 각 트레이스 스텝을 source map 으로 소스에 매핑.
  변수 위치는 **PC별 stack-provenance 분석**으로 구한다(viaIR 과 legacy codegen 의 스택 배치가 달라서 필요).
- **프레임 모델이 정교하다**: EVM 콜 프레임을 **internal 함수 프레임**으로 확장, **modifier 호출마다 독립
  프레임 + 함수 헤더에 정지점**, cheatcode 호출은 합성 프레임, 소스 없는 코드는 "foreign" 프레임.
- 스코프: Locals / State / **Globals(`msg`,`tx`,`block`,`gasleft`)** / Events / EVM(디코딩된 calldata·
  returndata·accounts 트리). 예외는 caught / uncaught / expected(`vm.expectRevert`) 로 분류.
- 전제: Foundry 형태 프로젝트. 복잡한 경우 **`setUp()` + public·무인자 테스트 함수**가 있어야 `▷ Debug` 가 뜬다
  (→ 입력을 테스트 안에 **하드코딩**해야 한다).
- **심볼릭 실행 모드는 접근 제한**이다 — 공식 문서: "Access to the symbolic execution mode is currently
  restricted." 심볼릭 모드는 임의의 symbolic storage·calldata 로 시작해 분기 CFG 와 경로 조건을 보여준다고 서술.
- 기타: time travel, Foundry Test Explorer(fuzz 포함), 커버리지 리포트, EVM 수준 디버깅. BSD-3-Clause.
- 알려진 한계(PR 기준): legacy codegen 에서 base constructor 인라인 → constructor local 미표시,
  `bytes`/`string` calldata 파라미터는 슬롯 배치가 달라 **값을 비워 둔다**, 컨트랙트 식별은 추측하지 않음
  (정확 일치 → immutable 마스킹 일치 → CBOR 유일 일치), 구버전 kontrol-node 비호환.
- **[미확인]** 설치·실행하지 않았다. 심볼릭 모드는 접근 권한이 없어 측정 불가.

### 1.5 EDB (`edb-rs/edb`) **[문서, 조회 2026-10-29]**

- Rust. **온체인 트랜잭션 재생** 디버거. `edb replay <txhash> --rpc-urls ...`.
- source map 을 **쓰지 않는다.** Solidity AST 에 훅을 심어 **재컴파일**한다:
  `BeforeStep` / `VariableInScope` / `VariableOutOfScope` / `VariableUpdate`.
  Foundry 의 solc 래퍼로 컴파일하고, **생성 트랜잭션의 initcode 를 계측본으로 바꿔치기**(생성자 인자 보존).
- 소스는 **Etherscan 검증 소스**를 받아 캐시한다. `private` 함수·변수는 훅이 접근하려면 **가시성을 바꿔야** 한다
  (→ forge harness 와 같은 종류의 수술이 내부적으로 일어난다).
- 실행: 포크에서 **같은 블록의 선행 트랜잭션을 먼저 재생**한 뒤 대상 트랜잭션을 REVM `transact_commit()` 으로 실행.
- 스냅샷 2층을 **하나의 타임라인으로 병합**: opcode 스냅샷(명령어 단위, 전 구간 커버) +
  훅 스냅샷(소스 수준 계측 지점). 가능하면 훅 스냅샷을 우선 표시. RPC: `edb_getSnapshot`·`edb_navigate`·
  `edb_evalOnSnapshot`(식 평가).
- UI: 웹(기본, 바이너리에 Bun 번들 내장) / TUI(`--ui=tui`). 크레이트 3개: `edb`·`rpc-proxy`·`tui`. AGPL-3.0.
- 스스로 밝힌 미구현: **Foundry 테스트 디버깅, 멀티체인, breakpoint 조건**. 엔진은 `127.0.0.1` 바인딩·인증 없음.
- 저자: Columbia DAPLab(@troublor, @i2huer). EF ESP 후원. **학술 논문은 아직 없다** — README·DAPLab 페이지에
  논문 참조가 없다. → 인용은 저장소/프로젝트 페이지로 해야 한다 **[문서]**.
- **이 데이터셋에 적용 불가**: 온체인 tx 해시 + Etherscan 검증 소스가 필요한데, DAppSCAN 은 감사 시점
  **소스 스냅샷**이다. 배포 주소도 트랜잭션도 없다. → **정량 비교가 구조적으로 불가능**하고,
  그 사실 자체가 scope 분리의 근거다(배포된 코드의 사후 분석 vs 작성 중인 코드의 편집 시점).

---

## 2. 전제 조건·커서 대조표 (논문 §2 표 초안)

| | Remix | forge `--debug` | Hardhat 3 | Simbolik | EDB | **IQDebug** |
|---|---|---|---|---|---|---|
| 실행 모델 | tx 재생 | 테스트 1회 실행 | tx/테스트 1회 실행 | kontrol-node 실행 + trace lifting | 온체인 tx 재생(계측 재컴파일) | **실행 없음 — 추상해석** |
| 소스 연결 | source map | source map | (없음 / opcode trace) | source map + stack provenance | **소스 계측 훅** | **CFG 노드 ↔ display_line** |
| 커서 | 트레이스 인덱스 | 트레이스 인덱스 | — | 트레이스 인덱스 | 스냅샷 타임라인 인덱스 | **NG 노드 + frontier 집합** |
| 스텝 디버거 | 있음 | 있음 | **없음** | 있음 | 있음 | 있음(추상 상태 위) |
| 역방향 | 트레이스 스크럽 | 트레이스 스크럽 | — | time travel | time travel | **NG 선행 집합(join 에서 다중)** |
| 한 세션이 덮는 실행 | 1 | 1 | 1 | 1 (심볼릭=제한) | 1 | **입력 구간 전체** |
| 필요한 선행 조건 | 배포+tx | 프로젝트+테스트+harness | 프로젝트+스크립트/테스트 | 프로젝트+`setUp`+무인자 테스트 | **배포+검증소스+tx+RPC** | **소스 텍스트만** |
| 모든 입력에 구체값 필요 | 예 | 예 | 예 | 예 | (과거 tx 가 결정) | **아니오 — 구간/빈칸 허용** |
| 편집 후 재측정 | 재컴파일·재배포·재전송 | 재컴파일·재배포·재실행 | 동일 | 재컴파일·재실행 | **불가(새 tx 없음)** | **증분 수술 + 커서 보존(D9)** |
| 모르는 값 | 항상 구체값 | 항상 구체값 | 항상 구체값 | 항상 구체값 | 항상 구체값 | **⊤ 로 명시** |
| 도달 불가 판정 | 불가 | 불가 | 불가 | (심볼릭이면 부분) | 불가 | **모든 입력에 대해 + 사유(O15)** |
| 루프 3회전 | 3개 위치 | 3개 위치 | — | 3개 위치 | 3개 위치 | **접은 노드 1개 + loop delta** |

---

## 3. 차별점을 세우는 축

1. **커서의 정체 — 논문 기여의 정당화.** 다섯 도구 전부 커서가 **기록된 선형 트레이스의 인덱스**다.
   그래서 operation 의미론을 정의할 것이 없다(스텝 = `i±1`). `Back ∘ Fwd ≠ id` 같은 성질은 질문 자체가 성립하지
   않는다. **상태가 집합이 되고 위치가 그래프 노드가 되는 순간** operation 이 비자명해진다 → 그 정의가 기여.
   실측 수치로 받친다: `withdraw` 1회 = forge 1,014 스텝 vs IQDebug 착지 노드 소수.
2. **경로 기반 vs 요약 기반.** Simbolik 의 심볼릭 모드가 가장 가까운 이웃이지만, 심볼릭 실행은
   **경로마다 경로 조건**을 들고 루프를 전개/한계지음다. 추상해석은 **프로그램 지점마다 fixpoint 요약**이고
   루프를 접는다. 결과적으로 심볼릭 모드의 커서도 여전히 **경로 위**에 있다 — join 에서 Step Backward 가
   선행 **두 개**를 주는 일은 요약에서만 일어난다. 그리고 navigation 에 solver 호출이 0이다.
3. **source map 을 넘지 않는다.** EDB 가 Remix·`forge debug`·Hardhat 을 비판하는 근거가 "최적화가 켜지면
   source map 이 틀린 줄을 가리킨다" 이고, 그래서 EDB 는 계측·재컴파일을 택했다. IQDebug 는 **바이트코드
   경계를 아예 넘지 않으므로** 이 문제가 구조적으로 없다 — 대신 EVM 수준 관찰(gas·스택·메모리)을 포기한다.
4. **편집 시점 vs 사후.** EDB 는 배포·검증된 코드의 과거 트랜잭션만 다룬다. 한 줄 고치면 **재측정할 대상이
   없다.** Remix·forge·Hardhat·Simbolik 은 재컴파일·재실행이 필요하고 커서를 잃는다.
5. **사용자가 발명해야 하는 값의 개수.** 네 도구 모두 모든 파라미터·참조 상태변수에 구체값을 정해야 한다
   (+ 슬롯 계산, + 상속 harness). IQDebug 는 엔진이 슬롯을 도출하고 빈칸·구간을 허용한다.
   → C1 을 "인터페이스 변경" 이 아니라 **측정된 주장**으로 만드는 축.

---

## 4. 베이스라인 선정 권고 (평가 설계)

| Tier | 도구 | 방식 | 근거 |
|---|---|---|---|
| **A — 정량** | **forge 1.8.6** | `--debug --dump` 로 스텝 전수 추출 + 준비 비용 측정 | 유일하게 스텝 디버거 + **비대화형 덤프** + 버전 고정 가능 |
| **B — 정량(준비·관측 비용)** | **Hardhat 3.21.1** | Solidity 테스트 / `console.log` / `debug_traceTransaction` | 스텝 디버거가 없으므로 **"디버거"가 아니라 관측 관행**으로 프레이밍 |
| **C — 정성** | Remix, Simbolik, EDB | 기능·전제 조건 대조표(§2) | 아래 사유 |

- **EDB**: 모집단에 적용 불가(§1.5). 정량 비교를 시도하지 않고, **불가능 사유를 scope 로 쓴다**.
- **Simbolik**: 심볼릭 모드 접근 제한 → "측정하지 않았다" 를 **조회일과 함께 threats 에 명시**.
  구체 모드만 비교하면 전제 조건이 forge 와 같아 Tier A 에 흡수된다.
- **Remix**: 브라우저·수동이라 재현성이 낮다.
- 세 도구 모두 **active development** → 논문에 버전·조회일을 박는다.

### 측정 지표 제안

**operation 응답시간을 baseline 과 비교하는 것은 약하다** — 트레이스 디버거의 스텝도 배열 인덱싱이라 ~0ms 다.
비교의 본론은 **첫 관측에 도달하기까지 무엇을 작성해야 했는가**와 **한 관측에 몇 스텝을 지나야 하는가**다.

1. **준비 비용**: harness LOC, `vm.store` 호출 수(+슬롯 유도 횟수), **사용자가 발명해야 하는 구체값 개수**,
   첫 관측까지 wall-clock. IQDebug 는 슬롯 수(빈칸 허용).
2. **탐색 입도**: 소스 줄 A→B 로 가기 위해 지나야 하는 스텝 수. forge 는 `--dump` 로 전수 추출 가능,
   IQDebug 는 NG 상의 operation 수. (`withdraw` 예: 1,014 vs 착지 노드 수)
3. **접근 가능 모집단**: harness 없이 직접 디버깅 가능한 함수 — T2 2,126(internal/private 1,077 +
   library 1,049) 과 `requires_storage_binding` 348 이 그대로 지표가 된다.
4. **편집 후 재측정**: 재컴파일·재배포·재실행 vs 증분 수술, 그리고 커서·프레임 보존 여부.
5. **정성 capability matrix**(§2) — 지는 축을 포함해서 적는다.

---

## 5. 양보해야 하는 축 (threats 에 먼저 쓴다)

| 축 | 누가 이기나 |
|---|---|
| 정확값(구체값) | 전부 그쪽 |
| revert·실패 경로 디버깅 | Simbolik(예외 분류)·EDB. **IQDebug 는 `error_exit` 가 항상 dead(D5)** |
| gas·스택·메모리 | forge·EDB·Remix |
| 배포된 코드의 사후 분석 | EDB |
| 다른 컨트랙트 인스턴스 storage | 전부 그쪽 (IQDebug 는 opaque + ⊤) |
| 루프 본문 안 관찰 | 전부 그쪽 (IQDebug 는 본문 착지 불가, D8) |
| modifier 프레임 모델 | **Simbolik**(modifier 당 프레임 + 헤더 정지점). 데이터셋에 placeholder 전후 문장이 있는 modifier 사용 **623건** → IQDebug 의 처리를 의미론에서 정의해야 한다 |
| 관계적 성질(`x < y`) | 심볼릭(Simbolik) — interval 도메인은 비관계적 |

---

## 6. 선행 연구 — 반드시 인용해야 하는 것

- **François Bourdoncle, "Abstract Debugging of Higher-Order Imperative Languages", PLDI '93, 46–55**
  (+ "Assertion-based Debugging of Imperative Programs by Abstract Interpretation", ESEC '93, LNCS 717, 501–516).
  시스템 이름 **Syntox**(Pascal 부분집합). **"추상 상태를 보고 디버깅한다" 는 1993년에 이미 있다.**
  PL 리뷰어는 반드시 안다 — 인용 누락은 치명적이다.
  **구분선**: 그쪽은 assertion/invariant 로부터 필요조건을 역추론하는 **분석**이고,
  step/frame/breakpoint/continue 라는 **디버거 operation 의 의미론은 아니다.** 이 구분을 Background 에
  명시하면 오히려 C3 이 선명해진다.
- 같은 논리로 처리할 것: Frama-C value analysis GUI 류(문장별 추상값 조회는 하지만 operation 이 없다),
  Astrée 등 산업용 추상해석기.
- 자기 선행 연구: **SolQDebug** (Automated Software Engineering, DOI 10.1007/s10515-026-00618-9) — 게재.
  IntentChecker(심사 중)는 익명 플레이스홀더로.
- **[미확인/할 일]** 스마트 컨트랙트 디버깅 **관행**에 대한 실증 연구(설문·인터뷰)를 찾지 못했다.
  Background 의 "개발자는 이렇게 디버깅한다" 서술을 인용으로 받치려면 ICSE/FSE/MSR 쪽을 더 찾아야 한다.
  없으면 그 문장은 주장하지 말고 도구의 전제 조건으로만 논증한다.

---

## 7. 인용 정보 (조회 2026-10-29)

| 대상 | 출처 |
|---|---|
| Foundry | https://book.getfoundry.sh/ , release v1.8.6 (2026-10-21), commit 6bad0a6 |
| Hardhat 3 | https://hardhat.org/ , npm `hardhat@3.21.1` (dist-tag `latest`; `hh2` = 2.29.1) |
| Remix | https://remix-ide.readthedocs.io/ (디버거 장) |
| Simbolik 문서 | https://docs.runtimeverification.com/simbolik |
| Simbolik 확장 | https://github.com/runtimeverification/simbolik-vscode (BSD-3-Clause) |
| Simbolik DAP 서버 구조 | https://github.com/runtimeverification/simbolik-vscode/pull/90 |
| EDB | https://github.com/edb-rs/edb (AGPL-3.0), `ARCH.md` |
| EDB 프로젝트 페이지 | https://daplab.cs.columbia.edu/projects/?view=software |
| Bourdoncle PLDI'93 | DOI 10.1145/155090.155095 |
| Bourdoncle ESEC'93 | LNCS 717, 501–516 (DOI 10.1007/3-540-58792-6_27) |
| SolQDebug | DOI 10.1007/s10515-026-00618-9 |

---

## 8. 남은 확인 작업

| 우선 | 작업 | 왜 |
|---|---|---|
| P0 | `--dump` JSON 에서 **스텝 → 소스 줄** 복원 (source map 파싱) | 탐색 입도 비교(§4.2)의 전제. 덤프에 소스 줄이 없다 |
| P1 | Simbolik 설치·구체 모드 실행 | §1.4 를 [문서]에서 [실측]으로. 심볼릭 모드는 접근 요청이 필요 |
| P1 | EDB 설치 + 공개 tx 하나로 실행 | §1.5 의 계측·재컴파일 비용을 실측(정성 서술의 신뢰도) |
| P1 | Remix 디버거 한 번 통과 | 정성 서술의 근거 |
| P2 | Hardhat 3 Solidity 테스트의 cheatcode **지원 범위** 확인 | forge 와 동등한지(`vm.load`·`vm.snapshot`·fork 류) |
| P2 | 스마트 컨트랙트 디버깅 관행 실증 연구 탐색 | §6 의 빈 칸 |
