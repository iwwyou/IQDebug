# Baseline 실험 계획 (forge / Foundry 직접 호출 / Hardhat 3)

작성 2026-11-06. 2026-11-09 구성 재배치. **2026-11-20 데이터셋 동결본과 준비 규칙 확정을 반영해 0 장을 추가.**
범위: **비교 도구 쪽 측정만.** IQDebug 측정과 논문 논리·RQ 번호는 다른 세션 소관이다. 여기서는 축을 `준비`, `재획득`, `범위`, `관찰` 로만 부른다.
관리: **이 문서는 baseline 세션만 수정한다** (`../ROADMAP.md` 20행). 논문 서술에 필요한 규칙은 0 장에서 가져간다.
짝 문서: `RELATED_TOOLS.md`, `FOUNDRY_RELEASE_NOTES.md`, `../Dataset/DATASET_SELECTION.md`(데이터셋 정본), `../ROADMAP.md`(D4·D5 가 이 문서의 구현 항목).

**문서 구성**
- **0 장 — 현행 규칙.** 최종적으로 읽을 내용. 1–6 장과 충돌하면 0 장이 우선한다.
- 1–6 장 — 2026-11-09 판 본문(참고). 비교 축·지표 구조는 그대로 유효하고, 0.7 에 적은 항목만 대체됐다.
- 부록 A–G — 실측 기록과 세부 정의(참고).

---

# 0. 현행 규칙 (2026-11-20 확정)

## 0.1 평가 대상

1. 대상은 `evaluation/Dataset/target_functions.json` 에서 `stage == "selected"` 인 **415 파일**의 `selected == true` 인 **2,653 함수**다
   (T1 1,608 · T2 1,045). 선정 과정은 `DATASET_SELECTION.md`. 이전 기준(486 파일, `targets_final.json`)은 폐기했다.
2. **단위는 함수다.** 성공률·비용·관찰 결과는 함수 단위로 낸다.
3. **T1·T2 분모를 나눠 보고한다.** T2 도 단독 디버깅 대상이다.
4. 함수 단위(micro)와 스냅샷 단위(macro)를 함께 보고한다(상위 1 스냅샷이 14.3%).
5. `cg3_effect`(782), `has_delegatecall`(47), `requires_storage_binding`(117) 함수는 결과에서 따로 표시한다.
6. 데이터셋 선정은 도구 결과로 되돌려 바꾸지 않는다. 실행·관찰 실패는 지우지 않고 원인별로 보고한다.

## 0.2 소스와 컴파일

1. 입력 소스는 `evaluation/Dataset/norm_sources/<id>/` 다(세 도구 공통, 원본과 바이트코드 동일 검증됨).
2. **`norm_sources/` 는 읽기 전용이다.** 하네스·가시성 완화 등 모든 변형은 타깃별 **별도 작업 사본**에서만 한다
   (`evaluation/baselines/harness/work/<id>/`, 생성물). 사본을 만들 때 `manifest.json` 의 sha256 과 대조해 출발점이 같음을 기록한다.
3. solc 는 `manifest.json` 의 타깃별 버전(13 개 버전 고정), optimizer off, `bytecodeHash = none`. 도구 간 같은 바이너리를 쓴다.
4. 비상대 import 가 있는 3 타깃은 `remappings.txt` 를 forge `remappings` 로 옮긴다.
5. **빌드 조사 (2026-11-20, `harness/build_survey.py` → `harness/results/build_survey.jsonl`)**: 415 타깃 전부 **forge 1.9.0 415/415, Hardhat 3.21.1 415/415 빌드 성공**.
   - 작업 사본의 sha256 불일치 0. remapping 3 타깃 포함.
   - 두 도구 모두 `evaluation/solc` 의 같은 바이너리를 썼다. forge 는 artifact metadata, Hardhat 은 build-info 의 `solcVersion` 이 415 전부 manifest 버전과 같고,
     Hardhat build-info 의 optimizer off · `bytecodeHash = none` 도 415 전부 일치. Hardhat 은 설정의 `solidity.path` 로 바이너리를 지정하며, wasm 사용 0, 컴파일러 새로 받기 0.
   - 빌드 시간(4 병렬, 콜드): forge 중앙값 2.0 초 · 최대 5.7 초, Hardhat 중앙값 2.9 초 · 최대 7.2 초. 시간 지표가 아니라 조사 기록이다(병렬 실행).
   - Hardhat 프로젝트는 `harness/work/node_modules` 를 `playground/hardhat/node_modules` 로 디렉터리 정션해 같은 설치본을 쓴다.

## 0.3 비교 대상

1. 주 비교군은 **W1**(forge test --debug), **W2**(anvil + `cast call --debug`), **W3**(Hardhat 3: 관찰 W3b, 실행 W3a 방식)이다.
2. W4(standalone SolDB)는 비교군이 아니며 `--json` 계측 도구로만 쓴다.
3. **도구마다 실제로 필요한 최소 준비만 부과한다.** baseline 에 불필요한 과정을 더하지 않는다.
   Hardhat Solidity 테스트(W3c)가 W3b 보다 준비가 적은 경우가 있으면 S1 에서 확인하고 최선 경로로 쓴다.

## 0.4 접근 규칙 (타깃 함수를 실행하게 만드는 방법)

원칙: **원본 정의(함수 본문)는 바꾸지 않는다.** 실행 문맥은 하네스로 만든다. 예외는 아래 4 의 가시성 키워드 하나뿐이다.

1. **public/external (T1)**: 직접 호출.
2. **contract internal**: 상속 하네스가 노출 함수로 부른다. W1·W3 은 테스트에서, **W2 는 하네스 계약을 `forge create` 로 배포한 뒤 `cast call`** 로 부른다.
   W2 의 internal 은 "불가"가 아니라 "래퍼 필요"로 센다.
3. **library internal**: 래퍼 계약이 `Library.f(...)` 를 직접 부른다. library external/public(31)은 library 를 배포해 직접 부른다.
4. **private (123: 계약 77, library 46)**: 작업 사본에서 **같은 줄의 `private` 를 `internal` 로만 바꾼 뒤**(가시성 완화) 2·3 과 같이 하네스로 부른다.
   - 근거: private 는 상속 하네스로도 부를 수 없어 원본 유지만으로는 직접 진입할 수 없다. 호출자 경로는 진입값을 직접 정할 수 없고, 본문 복사는 원래 정의가 실행되지 않는다.
   - 줄 번호와 함수 본문은 그대로다. public/external 로 바꾸지 않는다(storage 참조 파라미터·calldata 문제, 디스패처 변화).
   - 원본·완화본 바이트코드 비교는 **기록용**이다. 달라도 진행하고 결과를 함께 보고한다.
   - 완화 후 컴파일 실패(하위 계약의 같은 시그니처 등)는 `F_ACCESS` 로 기록한다.
   - 보고: 별도 준비 유형 "가시성 완화"로 건수를 센다.
5. **abstract 계약 소속 (299: T1 183, T2 116)**: **abstract 를 contract 로 바꾸지 않는다.** 구체 하위 계약(하네스)으로 배포한다.
   - 모든 함수가 구현된 계약(159)은 빈 하위 계약(`contract H is A {}`)으로 충분하다.
   - 미구현 함수가 있는 계약(140)은 하위 계약에서 구현(stub)을 채운다. 채운 구현은 실험이 만든 환경 가정이다.
   - 함수마다 둘로 나눈다. (a) 타깃 실행이 stub 에 의존하지 않음 → 주 비교. (b) 의존함 → 환경 가정을 명시해 따로 비교하거나, 의미 대응이 불가하면 `semantic_undefined` 로 기록.
   - 하네스에서 **실제로 실행된 본문이 선정한 원래 정의인지** source map 으로 확인한다(override 되지 않았는지 포함).
   - IQDebug 가 미구현 virtual 호출을 어떻게 해석하는지는 다른 세션 F0 에서 확인한다. 그 결과가 (b) 의 비교 가능 여부를 정한다.
6. **storage 참조 파라미터 (117)**: 하네스에 storage 변수를 두고 정본 케이스의 경로별 값으로 채워 넘긴다. W2 는 그 변수의 슬롯을 계산해 주입한다.
7. 1–6 을 한 함수에 여러 개 적용할 수 있다(예: abstract 계약의 private 함수 = 5 + 4).

## 0.5 결과와 비용의 기록

1. **실행 가능성과 준비 비용을 따로 센다.** 같은 어려움을 두 번 세지 않는다.
2. 보고 지표 네 가지: 선정 함수 수 / 하네스 필요 함수 수 / 하네스 자동 생성 성공률(수동 수정 없이) / 동등 관찰 성공률(같은 입력·실행 의미에서 실제 타깃을 관찰).
3. 준비 비용은 함수마다 범주별로 센다: 상태 주입, `msg.*`·블록 문맥 설정, 코드 배치(배포·생성자·immutable), 외부 호출 mock, 래퍼·하위 계약·가시성 완화.
4. 비용은 세 부분으로 나눈다: **준비**(하네스 생성·컴파일·배포·입력 구성), **초기 실행**(구체 실행·trace 생성), **재관찰**(같은 trace 탐색 또는 새 입력 재실행).
   IQDebug 쪽 대응(소스 로드·폐포·입력 슬롯 / 추상해석 / 재평가)은 다른 세션이 같은 구분으로 낸다.
5. 하네스는 재사용할 수 있으므로 **최초 디버깅과 재사용 이후 디버깅을 나눠** 잰다. 입력마다 하네스 생성 비용을 다시 부과하지 않는다.
6. 실패 원인은 `tool_unsupported`(도구 기능 부족) / `harness_incomplete`(하네스 생성 실패) / `semantic_undefined`(stub 의존 등 구체 실행 문맥의 의미 미정) / `dataset` / `resource` 로 구분한다.
7. 논문 서술 원칙(논문 세션에 전달): "baseline 은 못 한다"고 쓰지 않는다.
   "자동화할 수 있지만 함수마다 구체 실행 문맥과 소스 변형이 필요하다(종류·건수)"로 쓰고, IQDebug 쪽 비용과 ⊤ 로 인한 정밀도 손실을 나란히 보고한다.

## 0.6 미결 (결정 또는 확인 대기)

1. **CG3 외부 호출**: 제안은 mock 을 주 방식으로 쓰고 따로 집계하는 것. W1 은 `vm.mockCall`, W2·W3 은 그 주소에 가짜 계약 코드를 올려야 한다.
   CG2(@IReturn)의 mock 값은 IQDebug 입력과 같게 둔다. 코드 없는 주소로의 고수준 호출(revert)과 저수준 call(true 반환)을 따로 규칙화한다.
2. **생성자·immutable** — **확정(2026-11-20)**:
   - IQDebug 는 immutable 을 **디버깅 입력으로 받는다**(사용자 결정). 정본 케이스에 immutable 값이 들어간다.
     ⚠ 현재 엔진은 immutable 을 constant 경로로 처리해 입력 슬롯에서 뺀다(`engine_api.py` 1163행 `isConstant` 제외, `ContractAnalyzer.py` 185행).
     입력으로 받으려면 엔진·프론트 수정이 필요하다(다른 세션 소관). 생성자에서 대입하는 immutable(초기화식 없음)의 현재 처리 결과도 그쪽에서 확인한다.
   - baseline 은 같은 값을 실제로 맞춘다(생성자 인자, 또는 실행 코드의 `immutableReferences` 자리 패치).
     **단 이 작업은 준비 비용으로 세지 않는다**(배포 시 정해지는 고정값이므로). 맞추지 못한 경우만 실패 원인으로 기록한다.
   - immutable 을 읽는 선정 함수는 156 개. 패치 방식의 외부 library 링크, `address(this)` 의존은 미검증.
   - 일반 상태변수는 배포 뒤 storage 를 정본 값으로 덮어쓰므로 생성자 인자는 "revert 하지 않는 값"이면 된다.
3. **정본 케이스 형식**: 다른 세션의 입력 생성기(D1)와 함께 확정한다. §6.1 스키마를 그에 맞춰 고친다.
4. **F0 기록 항목**(다른 세션): immutable 입력 여부, 미구현 virtual 호출과 IQDebug 처리, CG2 슬롯 수, 저수준 call 포함 여부.
5. ~~빌드 조사~~ — **완료(2026-11-20)**: forge 415/415, Hardhat 415/415, 같은 solc 바이너리 고정 확인(§0.2-5). 빌드는 실행 가능성의 첫 관문일 뿐이며 배포·호출 성공은 S0–S2 에서 본다.
6. ~~Hardhat 버전 고정, 범위 실험의 N 상한~~ — **정리됨(2026-11-20)**:
   - Hardhat 은 **3.21.1 로 고정**한다(실측 판). 3.21.2 는 `hardhat init` 템플릿의 forge-std 를 v1.17.0 으로 올린 것뿐이라 실행·컴파일·trace 에 차이가 없다.
     3.21.1 의 내용은 EDR v0.25.0 과 Solidity 테스트 gas 수정이다. 이후 3.22.0 이 있으나 실험 중 바꾸지 않는다.
   - 범위 실험 수치는 **`../ROADMAP.md` §0.2-3·§0.3-3 이 정본**이다(2026-11-17): 모든 입력을 구간으로 주고 baseline 은 조합을 전수 열거,
     모든 변수에 같은 폭 w, 조합 수가 전수 상한(10^4 안)을 넘는 설정은 비용(외삽)만 보고, T = T_고정 + n·t, baseline 전략 셋(전수·대표·동일예산).
     w 상한(5/10)·표본 수·전수 상한 값은 외부 검토 뒤 확정(그쪽 ⚑). 이 문서 B.4 의 N 사다리는 대체됐다.

## 0.6a 진행 단계 (2026-11-20 확정)

1. **S0 먼저.** `Vault` 로 W1·W2·W3 를 하네스 생성부터 관찰까지 자동화하고, 손 실행 결과(`playground/WALKTHROUGH.md`)와 일치를 확인한다. 케이스는 손으로 쓴다.
   - **1 차 결과 (2026-11-20, `harness/run_s0.py` → `harness/results/s0.json`): PASS.** 케이스 `harness/cases/s0_vault_withdraw.json`(amt=1000, 상태 3 슬롯).
     구체 기준값(anvil `eth_call` + `prestateTracer` diffMode)이 손 실행과 일치(반환 993, post `shares[sender]`=0 · `totalShares`=0).
     W1·W2·W3 모두 종료 형태·반환값 일치, 25 행 대입 전 값 `(r,i)` = (1000,0)·(997,1)·(995,2) 세 도달 모두 일치.
     세션 스텝: W1 2,201(`vm.store` 하네스), W2 1,014. W3b 계측은 관찰 줄의 문장 앞(같은 줄)에 `console.log`, import 는 파일 끝 → 줄 번호 보존.
   - **2 차 결과 (2026-11-20): 3 케이스 전부 PASS, 검사 55/55.** 1 차의 남은 항목 넷을 추가했다.
     - 케이스: `s0_vault_withdraw`(return), `s0_vault_withdraw_revert`(amt=2000, 16 행 require revert), `s0_vault_afterfee`(internal `_afterFee`, amt=10000, 반환 9912).
     - 워크플로: W1(`vm.store`), **W1s**(getter 있는 상태는 `stdstore`, 없는 것만 `vm.store`), W2, W3. 각 워크플로가 종료 형태 · 반환값 · 관찰 지점 값 · post-state 를 손 실행과 대조.
     - post-state: W1·W1s 는 테스트 안에서 `vm.load`, W3 는 `eth_getStorageAt` 으로 읽어 대조. **W2 는 `cast call` 이 상태를 바꾸지 않으므로 G2 를 tracer 경로(= 기준값)로만 얻는다.**
     - revert: 기준값 규칙을 고쳤다. revert 면 post-state = 주입값이고 `prestateTracer` diff 의 post 가 비어야 한다(확인됨). 부록 C 의 G2 정규화는 return 에만 적용한다.
       W1 은 대상 호출을 `try/catch` 로 감싸 revert 여도 테스트가 끝까지 돌고 디버거가 열린다. W3b 는 revert 된 트랜잭션 안의 `console.log` 도 출력된다(16 행 값 확인).
     - internal: 상속 하네스(`src/__harness__/VaultHarness.sol`, `exposed__afterFee`)를 작업 사본에 더해 W1·W1s·W2·W3 모두 같은 경로로 호출. 원본 파일 무변경, 줄 번호 그대로.
     - 세션 스텝: W1 3,180 / **W1s 67,928** / W2 1,014(withdraw). stdstore 는 슬롯 탐색 실행이 세션에 기록돼 약 21 배 길다.
       W1 이 1 차(2,201)보다 긴 것은 try/catch 와 post 읽기를 넣었기 때문. revert 케이스 W2 228 스텝.
     - `_afterFee` 케이스는 상태가 private `feeBps` 하나라 W1s 도 `vm.store` 로 떨어진다(`A_slot_tool` 0, `A_slot_harness` 1). getter 없는 상태는 도구 기능으로 못 넣는다는 §4.2 와 일치.
   - **S0 에서 아직 안 한 것**: W3c(Hardhat Solidity 테스트) 경로, private·abstract·storage 참조 접근 경로(통합 파일럿에서), 시간 측정 반복(현재 1 회 값).
2. **S1 과 S2 는 통합 파일럿 하나로 돌린다.** 25–30 함수를 0.4 의 접근 유형·CG3·루프·T1/T2 를 강제로 포함하는 층화 표본으로 뽑고, 실패는 전부 원인을 손으로 확인한다.
3. **통합 파일럿은 다른 세션의 입력 생성기(D1, 정본 케이스)가 나온 뒤에 한다.** 그래야 IQDebug 와 같은 입력이 되어 비교에 의미가 있다.

## 0.7 아래 본문 중 대체된 항목

1. §3 43행 "테스트 없는 입구는 internal 직접 호출 불가" → 0.4-2(W2 는 래퍼 배포로 가능).
2. §5.1·§5.2 의 대상 단위(파일) → 함수 단위(0.1). S1·S2 의 층에 T1/T2, abstract, private, storage 참조, CG3 를 넣는다.
3. §6.1 스키마 예시의 `solc: 0.8.37`, `ctor_args` → 0.2-3, 0.6-2·3.
4. 부록 A.1 "internal 은 어느 입구로도 불가" → 0.4-2.
5. 부록 D.1 전체(569 파일, "정규화 입력은 쓰지 않는다") → 0.2(정규화 소스를 쓰고, 별도 사본에서 변형).
6. 부록 D.6 접근 규칙 표 → 0.4.
7. 부록 B.1 `cause` 값 → 0.5-6.
8. §2 의 Hardhat 버전·N 상한 행, 부록 B.4 의 N 사다리, 부록 E 의 Hardhat 버전 → 0.6-6.

---

# 참고: 2026-11-09 판 본문

## 1. 실험의 성격

**디버깅 기능 자체의 비교.** 도구가 무엇을 요구하고, 무엇을 내주며, 얼마나 걸리는지만 잰다.
사용성이나 작업 노력은 측정하지도 해석하지도 않는다. 준비 지표는 "디버깅 세션이 성립하는 데 요구되는 것"으로만 읽는다.

**공통 관찰 목표**: 모든 시간·성공 판정의 기준은 "케이스가 지정한 지점(`observe.points`)에서 지정한 변수의 값을 얻는 것"이다.
실행 완료나 trace 로드는 목표가 아니다. 구체값과 추상값은 같은 산출물이 아니므로 시간과 값의 종류를 항상 함께 보고한다.

**최선-경로 원칙**: baseline 은 항상 도구가 제공하는 가장 빠른 자동화로 측정한다 (`stdstore`, 캐시 유지, env 파라미터, 스냅샷 재사용, code override 등).
느린 변형은 부가 행으로만 둔다. baseline 에 유리한 조건에서도 성립하는 결과만 주장한다.

## 2. 결정 대기

| 항목 | 현재 문서의 가정 | 비고 |
|---|---|---|
| ~~W2 주 비교군, W4 보조~~ | **확정 2026-11-09**: 주 비교군 W1·W2·W3(관찰 W3b, 실행 방식 W3a). W4 는 비교군에서 제외, 계측 도구로만 | Background 는 W1·W2·W3b 를 상세 서술 (`playground/WALKTHROUGH.md`) |
| Hardhat 버전 고정 | 3.21.1 또는 3.21.2 | 실측은 3.21.1 |
| 범위 실험의 N 상한 (100, 1000, 10000) | 사다리에 포함 | 실험하면서 결정 (2026-11-09 합의) |

## 3. 비교 대상 워크플로

| ID | 정체 | 구성 | 역할 |
|---|---|---|---|
| **W1** | forge 테스트 | 생성한 `.t.sol` (+ 필요 시 하네스) + `forge test --debug` | 테스트 기반 경로 |
| **W2** | Foundry 직접 호출 | `anvil` + `forge create` + `cast call --trace --debug --with-local-artifacts --override-state-diff` | 테스트 없는 최선 경로 |
| **W3a** | Hardhat 3 RPC 실행 | `hardhat_setStorageAt` + `debug_traceCall` / `eth_call` | 실행·재획득 |
| **W3b** | Hardhat 3 소스 계측 | `console.log` 삽입 + 재컴파일 + 실행 | 지점 관찰 |
| ~~W4~~ | standalone SolDB | `soldb run --runtime --storage` + legacy `combined.json` | **비교군 아님** (W2 와 엔진 동일, 생성자 상태·런타임 코드를 수동 지정). `--json` 계측 도구로만 사용 |

- Foundry 안에서 테스트·하네스 없이 입력과 상태를 주는 경로가 있다 (W2, 실측 부록 A.1). standalone SolDB 는 필수가 아니다.
- 테스트 없는 입구(W2 등)의 공통 한계: internal 함수 직접 호출 불가. W1·W3 은 하네스로 가능.
- W3 은 비교군 하나다. 관찰은 W3b, 범위·입력 변경의 실행은 같은 Hardhat 의 W3a 방식(최선 경로)으로 잰다. W3a 단독으로는 관찰 목표에 도달하지 못한다.
- W3 은 Hardhat 전체가 아니라 이 두 경로만 대표한다. Hardhat Solidity 테스트(W3c)는 W1 과 준비가 거의 같으므로 S0·S1 에서 작동만 확인한다.
- W3a 는 소스 수준 지역변수를 내주지 않는다. 이것은 성능 실패가 아니라 기능 지원 수준으로 기록한다.
- Remix 는 정성 표로만 다룬다. W1·W2·W4 는 SolDB 탐색 계층을 공유하므로 **탐색 기능 지원 여부는 W1 에서 한 번만** 조사한다.

## 4. 비교 축과 핵심 판단

### 4.1 비교 방법

| 비교 | 방식 | 주의 | 상세 |
|---|---|---|---|
| 입력 수준 | 구조 × 워크플로 기능 표 (§4.2) + 구조 빈도 | 작업량으로 해석하지 않는다. IQDebug 지원 범위를 같은 표에 둔다 | B.1, D.1 |
| 준비 | 같은 케이스의 지표 쌍 비교 | 단일 합산 점수를 만들지 않는다. 입력 구간·⊤ 로 요구량이 줄면 정밀도도 함께 보고 | B.1 |
| 첫 관찰 시간 | 공통 성공 집합 위 쌍체 | N=1 을 숨기지 않는다 | B.2 |
| 입력 변경 | 기본 / 최선 변형 모두 | 최선 변형 기준으로 주장 | B.3 |
| 소스 편집 | 3분류 × E1/E2 | 수술 미지원 편집은 IQDebug 측 실패로 집계 | B.3 |
| 범위 | N 곡선 + 포함 + 정밀도 + 열거 불가 구간 | 구체값 N 개와 구간 1개는 **같은 산출물이 아니다** | B.4 |
| 관찰 | 비율 표 | 값의 종류가 다름을 명시 (구체 vs 추상) | B.5 |
| 탐색 | 지원 표 + 목표 기준 지표 | step 수치를 도구 간 직접 비교하지 않는다 | B.6 |

**우선순위**

- 필수: 준비 요구량, 첫 관찰, 입력 변경, 소스 편집, G1·G2, 지원 성공률, 입력 범위와 구체 정답.
- 가능하면: `V_*`, 탐색 목표 지표, 의존 모드별 지원 범위.
- 보류 가능: fuzz 대규모 비교, 전체 데이터셋 G3, operation 미세 지연.

### 4.2 상태 입력 수준 (준비 축의 핵심 비교)

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

- W1 열은 소형 예제 실측이다. W2·W4·W3 열과 packed·동적 배열·string 의 실제 주입은 S1 에서 확인한다.
- `stdstore` 는 forge-std 테스트 라이브러리이므로 W2·W3 에는 없다. 즉 Foundry 안에서도 **테스트를 없애면 입력이 슬롯 수준으로 내려간다.**
- `forge inspect <C> storage-layout` 이 슬롯·offset 을 내주므로 "계산이 어렵다"가 아니라 "입력 인터페이스의 수준이 다르다"로만 해석한다.
- IQDebug 열은 다른 세션이 같은 구조로 채운다 (§6.2). 변수 경로로 받을 수 있는지와 그 값이 구체 실행과 맞는지를 함께 확인해야 비교가 성립한다.
- 이 비교의 크기는 데이터셋의 구조 빈도가 정한다 (D.1, 실험 진행 중 파악).

### 4.3 실측으로 드러난 baseline 의 강점 (주장 범위에 직결)

- W2 는 warm 조건에서 관찰 목표까지 약 0.06초다 (소형 Vault). 입력 변경 재획득에서 baseline 이 이길 수 있다.
- 범위 실험의 주장은 속도가 아니다. N≤10,000 에서 W1 은 1초 안쪽, W2 는 약 8초(외삽)다. 중심은 포함 검사와 열거 불가 구간이다.
- code override 로 값 편집 후 재배포를 생략할 수 있다. 단 immutable 등은 조용히 의미가 바뀐다 (B.3 허용 규칙).
- `stdstore` 가 중첩 mapping·struct·packed 까지 변수 수준으로 주입한다 (§4.2).

## 5. 진행

### 5.1 단계

| 단계 | 대상 | 통과 기준 |
|---|---|---|
| S0 | `Vault` 전 워크플로 E2E | G1·G2 전 워크플로 일치, G3 일치 지점 확보 |
| S1 | 손으로 고른 8개 (leaf / 호출 / 루프 / modifier / 상속 / library / 외부 호출 / 생성자 인자) | 지표 수집 완성, 실패 원인 수동 확인 |
| S2 | 계층 파일럿 20개 | **자동 준비 성공률 추정 → 범위 결정** |
| S3 | 자동 준비 성공 타깃 전부 | 성공률 보고 |

S2 성공률이 낮으면 S3 은 계층 표본으로 내린다. 층: T1/T2, 호출 유무, 루프 유무, 외부 호출 유무.
**루프 타깃을 제외하지 않는다.**

### 5.2 구현 순서 (`harness/` 신규)

| 순서 | 항목 | 참조 | 통과 조건 |
|---|---|---|---|
| 1 | 케이스 스키마, 공통 문맥, 공용 디버거 구동기·파서 | §6.1, C, D.4 | 같은 초기 상태 표현 |
| 2 | Vault S0: W1–W4 E2E + G2 정규화 + 주입 확인 | C, D.5 | G1·G2 일치, revert 포함 |
| 3 | 관찰 시점 검증 | §6.1 | 분기·호출·루프에서 `before` 대응 |
| 4 | 컴파일·상태변수 선행 조사 | D.1 | 성공률, `F_COMPILE` 사유, getter 없는 변수 비율 |
| 5 | layout → 슬롯 변환기, 상태 입력 수준 표 검증 | B.1, §4.2 | 단순·mapping·중첩·struct·packed·동적·string 통과 |
| 6 | 생성기: W1 테스트·하네스, W2 명령열, W3 스크립트 | B.1 | |
| 7 | S1 → S2 파일럿 | §5.1 | **범위 결정** |
| 8 | 재획득·범위 측정기, 구체 정답 덤프 | B.3, B.4, §6.2 | 입력별 같은 초기 상태 |
| 9 | S3 전체 실행 + 집계 | | 실패 포함 |

시작 전에 미리 검증할 항목은 부록 G.

## 6. 다른 세션과의 계약

### 6.1 공통 입력: 케이스 명세

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

- `state` 는 **변수 경로**로 적는다. 슬롯 변환 방식은 워크플로별로 다르며, 그 차이가 측정값이다 (§4.2, B.1).
- `expected_termination` 은 `return` / `revert` / `mixed` (구간 안에 둘이 공존) 중 하나다. 정상 종료는 필수 조건이 아니다. revert 도 정식 디버깅 대상이다.
- `phase` 기본값은 `before` 이며 IQDebug `in_state` 에 대응한다. `iteration` 은 같은 줄의 몇 번째 도달인지를 뜻한다.
  IQDebug 가 반복별 상태를 구분하지 않는 지점은 값 일치 대신 구체값의 포함 여부로 대조한다.
- `observe.exit` 는 학위논문 `O_exit` 와 같다. `return` 케이스에서만 post-state 를 비교한다.
- `context` 는 공통 기본 문맥(D.4)에서 달라지는 항목만 적는다.

상태 복원은 필드가 아니라 고정 규칙이다: **모든 입력은 같은 초기 상태에서 실행한다.**

### 6.2 넘기는 산출물

| 파일 | 내용 |
|---|---|
| `harness/cases/*.json` | §6.1 케이스 명세 |
| `harness/results/<workflow>.jsonl` | 케이스·반복별 지표와 실제 사용 문맥 |
| `harness/truth/<case>.jsonl` | 입력별 구체 정답 (G1, G2, 방문 분기, 가능한 경우 G3) |

IQDebug 쪽은 같은 케이스에 대해 B.1 의 대응 지표(채운 슬롯 수 등)와 §4.2 표의 IQDebug 열, 공통 관찰 목표 시간, 재획득 시간, 분기 판정을 내면 된다.
포함 검사, 과잉근사 정도, 거짓 feasible 집계는 양쪽 데이터를 합쳐 계산한다.

---

# 부록 (참고)

## A. 실측 기록 (2026-11-06, Foundry 1.9.0 / Hardhat 3.21.1, Windows, Vault 및 소형 예제)

### A.1 테스트 없는 입구

| 입구 | 테스트·하네스 | 상태 주입 | 생성자 상태 | 소스·지역변수 | 결과 |
|---|---|---|---|---|---|
| `forge script src/Vault.sol:Vault --sig "withdraw(uint256)" 100 --debug` | 불필요 | **수단 없음** | 자동 | ✅ | 잔고 0 으로 16행 REVERT (248 스텝) |
| 위 + `--fork-url <anvil>` | 불필요 | 불가 (스크립트가 새로 배포) | 자동 | ✅ | 같은 REVERT |
| `cast call … --trace --debug --override-state-diff …` | 불필요 | ✅ | 자동 | ❌ **instruction 단위만** | 1,014 스텝 RETURN |
| 위 + **`--with-local-artifacts`** | 불필요 | ✅ | 자동 | ✅ | **1,014 스텝, `amt`·`r`·`i` 표시** |
| `anvil_setStorageAt` 후 `cast call … --with-local-artifacts` | 불필요 | ✅ | 자동 | ✅ | 같음 |
| `cast send` 후 `cast run <tx> --debug --with-local-artifacts` | 불필요 | ✅ | 자동 | ✅ | 같음 (트랜잭션 실제 전송) |
| standalone `soldb run` + legacy `combined.json` | 불필요 | `--storage` | **수동** (`--runtime` 필요) | ✅ | 1,014 스텝 |

요구되는 것은 anvil 실행, 배포, 슬롯 지식이다.
공통 한계: internal 함수 직접 호출은 어느 입구로도 불가 (`forge script` 도 `not implemented in your script`).
`cast` 에는 `--dump` 가 없어서 스텝 전수 추출은 불가하다. 대신 stdin 명령으로 디버거를 구동한다.

### A.2 실험 설계에 영향을 준 실측

| 항목 | 결과 | 설계 반영 |
|---|---|---|
| `cast call` 후 상태 | anvil 슬롯 불변 | post-state 는 tracer 로 얻는다 (C) |
| anvil `debug_traceCall` + `prestateTracer` diffMode + `stateOverrides` | 슬롯별 pre/post JSON, 트랜잭션 불필요 | 구체 기준값 경로 |
| 위 결과에서 0 으로 삭제된 슬롯 | `post` 에 없고 `pre` 에만 있음 | 정규화 필요 (C) |
| `cast call --trace -vvvvv` | 텍스트로 storage 변경 표시. **revert 된 하위 호출의 `0 → 7` 도 표시** | 정답으로 쓰지 않는다 |
| W1 1만 회, 복원 없음 | 45건만 성공 (정답 1,001) | 상태 복원 필수 (B.4) |
| W1 1만 회, `vm.snapshotState`/`revertToState` | 통과. 복원 없는 테스트·stdstore 테스트 포함 스위트 전체 0.79초 | 스위트 값이므로 `T_enum` 으로 쓰지 않는다 |
| `stdstore` (`shares`, `totalShares`) | `with_key` + `checked_write` 로 슬롯 없이 주입 성공 | W1 최선 경로에 포함 (B.1) |
| `stdstore` 구조별 (소형 예제 10건) | 중첩 mapping·struct 필드·packed(플래그 필수)·view 있는 private 성공. 빈 배열 원소·string·가공값만 내는 private 실패, `vm.store` 는 성공 | 상태 입력 수준 표 (§4.2) |
| W2 다중 주입 | 슬롯 3개(private 포함, 결과 73 = 손계산), 컨트랙트 2개, 플래그 반복 모두 작동 | 주입 단위는 슬롯 |
| W2 warm (단일 프로세스 7회 중앙값) | 일반 호출 0.044초, `break`→`continue`→`vars` 0.059초 | 재획득 비교의 기준 |
| 셸에서 Python 으로 시각을 찍은 측정 | 0.14–0.22초로 부풀려짐 | 타이머는 측정 프로세스 내부 (E) |
| 입력당 비용 (anvil, 200건) | `eth_call` 0.76ms, `prestateTracer` 0.79ms, 최소 structLog 6.6ms·85KB, 전체 structLog 10.5ms·210KB | 정답 수집을 시간 측정과 분리 (B.4) |
| `--override-code` + `--with-local-artifacts` | 값 편집(`i < 2`)에서 소스·지역변수 연결, 804 스텝 | 값 편집에 한해 검증됨 |
| `hardhat_setCode` | 값 편집에서 804 스텝 | 값 편집에 한해 검증됨 |
| immutable 컨트랙트에 artifact 코드 교체 | `owner()` = 0 주소, `isOwner()` true → false, **오류 없음** | 교체 전 검사 (B.3) |
| Hardhat `debug_traceCall` + `stateOverrides` | **오류 없이 무시**, revert 응답 | 쓰지 않는다. 주입 확인 단계 (D.5) |
| Hardhat `eth_call` 세 번째 인자 override | 작동 | 상태 무변경 일괄 실행 경로 |
| Hardhat `hardhat_setStorageAt` + `debug_traceCall` | 1,014 스텝, gas 36,812 (forge 와 일치) | W3a 경로 |
| SolDB `break` 25행 첫 도달 | `r=100, i=0` (실행 직전) | IQDebug `in_state` 와 대응. 한 사례이므로 S1 에서 재확인 |
| SolDB 미접근 슬롯 | `vars` 와 `print` 모두 `<unknown>` | `V_state` 정의 (B.5) |
| revert 실행 | W1·W2 모두 디버거 열림 (W2 228 스텝) | revert 케이스 포함 가능 |

## B. 지표 상세 정의

### B.1 준비 요구량

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
상태 입력 수준 표는 §4.2.

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

### B.2 시간

모든 값은 구간별로 쪼개 기록한다: `compile` / `node` / `deploy` / `state` / `instrument` / `execute` / `trace_load` / `first_obs`.

| ID | 시나리오 | 측정 |
|---|---|---|
| `T_cold` | 캐시 없음 | 컴파일 포함, 공통 관찰 목표 달성까지 |
| `T_warm` | 캐시·노드 유지 | 재실행, 공통 관찰 목표 달성까지 |
| `T_dbg1` | `build` 직후 첫 `--debug` | 디버그 빌드 분리 비용 |

일회성 환경 준비(forge-std, npm install)는 제외하고 별도 표기한다.
`--with-local-artifacts` 는 매 호출 컴파일 여부를 확인하므로, 프로젝트 크기에 따른 warm 시간 변화를 S1 에서 본다.

### B.3 재획득

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

### B.4 범위

> **대체됨 (2026-11-20)**: 입력 영역 구성과 수치는 `../ROADMAP.md` §0.2-3·§0.3-3 을 따른다(§0.6-6). 아래 N 사다리는 이전 판 기록이다. 상태 복원·정답 수집 분리 등 측정 절차는 유효하다.

입력 영역 크기 `N ∈ {1, 10, 100, 1000, 10000}` (구간 폭 × 슬롯 조합) + **열거 불가 구간** (넓은 구간, 다중 파라미터 곱). 100 이상의 포함 여부는 실험하면서 결정한다.

이 축의 주장은 속도가 아니다. N≤10,000 에서 W1 은 1초 안쪽, W2 는 약 8초(외삽)로 baseline 이 지지 않을 수 있다.
중심은 구체 결과의 추상 결과 포함 여부와 열거 불가 구간이며, 속도 차이는 있는 그대로 보고한다.

| 측정 | 방법 |
|---|---|
| `T_single` | 입력 1개 실행 |
| `T_enum(N)` | 영역 전수 실행 시간. **같은 초기 상태 복원 필수.** 외삽값은 실측과 구분 표시. 정답 수집은 포함하지 않는다 |
| 최선 경로 | W1: 입력 배열 + `vm.snapshotState`/`revertToState`. W2: 상주 anvil + `eth_call`. W3a: 상주 노드 + `eth_call` override |
| 기록량 | 전수 실행에서 디버거로 볼 수 있는 실행 수 (`--debug` 는 1건) |
| 산출 | **구체 정답** (C). `T_enum` 과 별도 실행으로 수집 |

유한 영역 전수와 표본 N 개는 구분해서 보고한다. 표본 포함은 soundness 의 증거일 뿐 증명이 아니다.
이 산출물이 IQDebug 의 포함 검사 입력이다 (§6.2). N=1 은 baseline 이 이길 수 있으므로 반드시 포함한다.
`fuzz` 비교(`--fuzz-runs {8, 256, 1024}`)는 전수·포함 결과가 확보된 뒤의 부가 분석이다.

### B.5 관찰 능력

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

### B.6 탐색

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

## C. 정답과 관찰값 추출

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

## D. 대상 준비 세부

### D.1 컴파일·상태변수 선행 조사

> **대체됨 (2026-11-20)**: 대상·소스·컴파일 설정은 §0.1·§0.2 를 따른다. 아래 중 "상태변수 구조 빈도" 항목만 유효하다.

569 파일의 컴파일 성공은 미검증이다 (`DATASET_RECORD` §10). 측정 전에 다음을 수행한다.

- 스냅샷별 격리 디렉터리 + 의존 폐포 + 허용 최고 solc. `evaluation/solc` 의 바이너리는 0.8.0–0.8.13 일부와 0.8.38 뿐이다 (0.8.37 은 없음. 필요 시 svm 또는 fetch_solc.py 로 보충).
- 원본 소스를 수정하지 않는다. 정규화 입력(`normalize_source.py`)은 쓰지 않는다.
- Hardhat 은 프로젝트 config 를 케이스별로 생성한다. WASM solc 사용 여부를 기록한다.
- **solc 버전은 케이스별로 같고, 도구 간 동일**하게 고정한다.
- **상태변수 구조 빈도**: 타깃이 읽는 상태변수를 §4.2 구조별로 센다 (getter 유무 포함). 단순·1단 mapping 이 대부분이면 입력 수준 차이의 효과는 작으므로, 이 빈도가 준비 축 주장의 크기를 정한다. 실험 진행 중 파악한다.

### D.2 구체값 찾기

케이스는 `return`, `revert`, `mixed` 를 모두 포함한다. 아래 탐색은 `return` 케이스를 구성하기 위한 절차다.

1. 케이스 명세 값 (다른 세션의 입력 생성기 D1 과 공유 가능)
2. 조건식에서 추출한 상수·경계값
3. `forge` fuzz 로 revert 없는 입력 탐색 (시드 고정, 예산 고정)
4. 실패 시 `F_PRECOND` 로 기록하고 제외하지 않는다

탐색 비용은 준비 시간에 넣지 않고 별도 보고한다. 값이 주어졌다고 가정하는 편이 baseline 에 유리하기 때문이다.

### D.3 외부 의존

- 외부 호출 타깃은 실질 타깃의 약 24.4% (추정).
- `real` 을 우선한다. 불가하면 W1: `vm.mockCall` 또는 mock 컨트랙트. W2: 대상 주소에 `--override-code`, mock 배포. W3: mock 배포.
- mock 은 호출당 `A_env` 로 세고 `dep_mode` 를 `mocked` 로 표시한다. 값은 케이스 `externals` 에서 가져온다.
- 대형 프로젝트처럼 mock 이 많으면 `F_STATE` 로 처리하고 사유를 남긴다.

### D.4 공통 실행 문맥

기본 문맥을 한 번 정의하고 케이스 `context` 로만 덮어쓴다. 실제 사용한 값은 결과에 전부 기록한다.

- **고정**: `block.timestamp`, `block.number`, `block.chainid`, `msg.sender`, `tx.origin`, `msg.value`, hardfork.
  W1 은 `--block-timestamp`, `--block-number`, `--chain`, `--tx-origin`, `--sender`. W2 는 anvil `--timestamp`, `--number`, `--chain-id`, `--hardfork`. W3 은 Hardhat 설정 (확인 필요).
- **기록만**: basefee, prevrandao, coinbase, gas limit, 배포 주소. anvil 에서 앞의 일부는 플래그가 확인되지 않았고, 직접 `gasleft` 를 쓰는 타깃이 0 건이다.
  `address(this)` 에 의존하는 타깃은 사유를 표시한다.

### D.5 주입 확인

API 성공과 의미 성공을 구분한다. 디버깅 실행 전에 주입한 값을 getter 또는 storage 조회로 확인한다.
override 경로처럼 조회가 불가능하면 `prestateTracer` 의 `pre` 로 확인한다. 이 단계는 시간에서 제외하고 성공 판정에 포함한다.

Hardhat 3.21.1 의 `debug_traceCall` `stateOverrides` 는 쓰지 않는다. `eth_call` override 결과와 `debug_traceCall` 결과를 같은 실행으로 가정하지 않는다.

### D.6 접근 규칙

> **대체됨 (2026-11-20)**: §0.4 를 따른다. 아래 표는 이전 판 기록이다.

| 타깃 | W1 | W2 / W4 | W3 |
|---|---|---|---|
| public/external | 직접 | 직접 | 직접 |
| internal | 상속 하네스 | public 입구가 있으면 경유, 없으면 `F_ACCESS` | 상속 하네스 |
| private | **소스 수정 필요** → `E_src` 집계 또는 public 경유 | public 경유 | 동일 |
| library internal | 호출 래퍼 | 경유만 | 호출 래퍼 |
| abstract | 구체화 하네스 | `F_DEPLOY` | 구체화 하네스 |
| storage 참조 파라미터 | 전용 하네스 | 경유만 | 전용 하네스 |

public 입구 경유는 입구의 전제조건까지 만족시켜야 한다. 그 항목도 `R_input`, `R_state` 에 들어간다.

## E. 측정 절차

- **고정**: forge 1.9.0, soldb 0.4.5, Hardhat 3.21.1 (§0.6-6), forge-std v1.16.2, Node v22.21.1, OS·CPU·RAM 기록.
- **타이머**: 측정 프로세스 내부의 `perf_counter`. 셸에서 인터프리터를 띄워 시각을 찍지 않는다.
- **반복**: 케이스당 10회. 첫 회는 warm-up 으로 분리. cold 는 매회 캐시·산출물 삭제. 케이스 대표값은 반복의 중앙값이며, 반복을 독립 표본으로 쓰지 않는다.
- **격리**: 다른 세션의 벤치와 동시에 돌리지 않는다. Defender 실시간 검사 영향 확인. anvil 포트 고정, 매 케이스 새 노드.
- **제한**: 케이스 타임아웃 120초 (S1 에서 재조정).
- **통계**: 케이스별 쌍체 비율(또는 log-ratio)의 중앙값, 프로젝트 단위 cluster bootstrap 신뢰구간. 유의성은 필요 시 Wilcoxon signed-rank. micro 와 프로젝트별 macro 를 함께 보고.
- **실패**: 성공률을 시간과 함께 보고한다. 타임아웃은 검열 자료로 취급한다. 공통 성공 집합을 명시한다.
- **기록**: 원시 stdout/stderr, 명령, 버전, 커밋을 전부 보존한다.

## F. 위협 요소

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

## G. 시작 전에 미리 검증할 항목

- `--override-code`, `hardhat_setCode` 의 연산자·구조 편집 (값 편집만 검증됨)
- immutable 값을 `immutableReferences` 위치에 패치하는 경로 (미시험. 되면 허용 규칙 완화)
- Hardhat `prestateTracer` 의 상태 주입 후 post-state (미시험. diff 응답 자체는 확인)
- 다중 호출 시퀀스 필요 케이스 (`approve` 후 `transferFrom` 처럼 선행 호출이 상태를 만드는 경우. 현재 설계는 상태를 직접 주입)
- 멀티컨트랙트에서 `--with-local-artifacts` 가 모든 주소의 소스를 잇는지
- `cast` stdin 구동의 시간 측정 신뢰도
- Hardhat 3 의 블록 문맥 고정 방법, anvil 의 prevrandao·coinbase 지정 가능 여부
- Hardhat 3 이 데이터셋의 구버전 solc 를 정상 처리하는지
