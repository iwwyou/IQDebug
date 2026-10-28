# IQDebug — 진행상황 (handoff)

> **2026-10-20 재정리.** 위쪽 §0–§6 = **현재 유효한 상태**(최근 결정·논의 중 쟁점·할 일).
> 아래 **부록** = 2026-10-05 이전의 시간순 기록 원문(decisions #1–#51, 참고용·수정하지 않음).
> 논문 방향 정본 = `PROGRESS2.md`. 엔진 수정 이력(Guardian 반영용) = `(to_solidityguardian)_engine_fixes.md`.

---

## 0. 지금 어디인가

- **IQDebug** = SolQDebug(게재) + IntentChecker(심사중) 엔진을 확장한 intent-aware quick debugger (박사학위 3번째 컴포넌트).
- **구현(Plan A)**: 엔진 통합·직렬화·Debug View·step forward/backward·breakpoint·intent/verdict 패널·
  증분 편집(B1)·Unreachable(#42)·루프/중첩루프 정상화(#48·#49)·외부 호출 정리(#50) 까지 완료.
  남은 구현 = Step Into/Out, 루프 fold, DependencyStore 등 → `PROGRESS2.md §A.2`.
- **현재 단계 = 학위논문 Evaluation 재설계** (2026-10-05 ~). RQ 구조와 데이터셋은 **대체로 합의, 세부 쟁점 논의 중**(§1).
  다른 LLM(ChatGPT)과 framing 을 병행 논의 중이며, 추가 논의는 다음 세션에서 이어간다.
- ⚠ 제목: `PROGRESS2.md`·`main.tex` 에는 "An Intent-Aware Quick Debugging Assistant for Solidity"(2026-09-17)로
  남아 있으나, 최근 논의에서 "State-Specified", "Interpretation-Driven Debugging" 이 제목 키워드로 언급됨.
  **현행 제목 미기록 → 사용자 확인 필요.**

---

## 1. Evaluation 설계 (2026-10-20 기준, ⚠ 미확정 포함)

### 1.1 데이터셋 계층

```
89 수집 ──(E1–E8 제외 15건)──▶ 74 effective benchmark ──(intent 표현 가능)──▶ 48 expressible
```

- 74 = Guardian `evaluation/RQ2/rq2a_specification_profile.xlsx` 의 `raw_profile` 시트(헤더 포함 75행 → **74 케이스**).
  expressible 48 / inexpressible 26. **26개는 `case_built = No`** — intent 어노테이션 케이스 파일이 없고 IntentChecker 로 돌린 적 없음.
  26개 `blocker_tags`: alpha 12 · alpha_and_beta 5 · beta 5 · delta 2 · alpha_and_gamma 1 · beta_and_epsilon 1 (태그 정의 미확인).
- 48 = `RQ3/rq3_comparison_table.csv` 48행(web3bugs 41 + numscout 7) = 74의 부분집합. **IntentChecker 의 NumScout/GPTScan/SCType 비교는 이 48에서만 수행됨.**
- 89 → 74 제외 사유 E1–E8: 다른 LLM 논의에서 전달받음(E1/E3/E4/E7/E8 = benchmark validity, E5 = 재현 불가 dependency,
  E6 = multi-tx scope 밖). **⚠ 원문 미확인 — Guardian 자료로 사실 확인 필요(할 일 A5).**

### 1.2 RQ 구조

| RQ | 질문(초안) | 집합 | 상태 |
|---|---|---|---|
| **RQ1** | How does IQDebug's state-specified debugging compare with existing Solidity debugging workflows? | **74** | 대체로 합의 |
| **RQ2** | To what extent can IQDebug's intent-aware model express and validate numerical developer intent? | 표현력 **74** / 검증 **48** | 합의 |
| **RQ3** | How does state specification affect the precision of IQDebug's abstract debugging results? | **74 후보** (판정 S/V/W/U 변화는 48) | 대체로 합의 |
| **RQ4** | How does IQDebug's intent-aware validation compare with existing NLE analysis techniques? | **74** (IQDebug 행은 표현 가능한 48) | 확정 2026-10-20 |

**RQ1 — 디버깅 workflow 비교 (intent 와 무관)**
- 비교 대상: **Hardhat 2.29.1 + Foundry 1.8.4** (정량). Remix = 보류(시간 되면 정성/가능 케이스 정량). Solidity Debugger Pro = 제외.
- 공통 목표: *각 케이스의 타깃 함수에 디버깅 초기 상태를 주고, **normal function exit** 의 상태를 얻는다.* (SolQDebug 의 jump_to_end 계승)
- 질문의 초점은 "누가 빠른가"가 아니라 **같은 목표를 달성하는 workflow·계산 비용의 차이**. Forge 가 더 빨라도 결론이 흔들리지 않게 설계.
- 두 관점 분리(합의): **singleton**(IQDebug `[42,42]` vs Forge/Hardhat `42` — 직접 latency/workflow 비교) /
  **range**(IQDebug `[0,100]` vs 열거·fuzz — 상태공간 탐색 방식 차이).
- 보고 항목: 74개 분석 완료 여부, 필요한 디버깅 입력 수·종류, Forge/Hardhat 의 setup(상태 주입·mock·harness LOC·artifact 수),
  결과까지 latency, **소스 수정 후 재측정 latency**. 기존 analysis-cost RQ 는 폐지하고 IQDebug 자체 latency 를 여기서 보고.
- **정밀도(⊤ 비율 등)는 RQ1 에서 보고하지 않고 RQ3 로 보낸다.**
- **Failed = 0 원칙** (사용자 확정): parser crash / CFG 실패 / interpreter 예외 / 지원 construct 오처리는 결과가 아니라
  평가 전에 고칠 engineering defect. 고치기 전 반드시 보고(폴백 금지). Failed 0 은 "평가 전제"로 쓰고 범용성 증거로는 쓰지 않는다.

**RQ2 — intent 표현·검증**: 표현력 74(48/74 + 26개 탈락 사유), 검증 48(판정 vs 기대). 기존 specification requirement 실험은
보조 분석(intent 형태 분포, 케이스별 state spec·intent spec 종류/수)으로 흡수. Unreachable(U) 사례는 48 안 자연 발생 여부 먼저 확인,
없으면 constructed case study 로 명시.

**RQ3 — 정밀도**
- 3.1 range sensitivity = **루프 없는 케이스**, 3.2 loop = **루프 있는 케이스** (사용자 제안, 합의). 두 실험 모두 같은 범위 확대 절차 적용.
- 구성: 74개 aggregate characteristics + 선정 사례의 range-sensitivity 상세 + loop subset 전체.
- 선정 기준은 **결과를 보기 전 정할 수 있는 구조적 조건만** (결과 기반 기준 "exhibits measurable change" 는 제외).
- 측정 원칙(합의):
  - 항상 수행: **empirical soundness check** — 모든 구체 관측값 ∈ 추상 구간? (위반 = 엔진 버그, 즉시 보고)
  - **전수 열거 가능한 케이스에서만** `width(A)/width(C)` 를 over-approximation ratio 로 보고.
  - fuzz 기반 비율은 "precision ratio" 라 부르지 않는다 → "empirical interval expansion relative to observed executions". 관측 폭 0 이면 비정의.
- 원인 분류(초안): I/N(미지정 입력) · E(opaque 외부 동작) · L(loop fixpoint/widening) · J(control-flow join) · O(기타 보수적 추상/미지원 연산).
  "assembly" 는 원인 범주로 쓰지 않는다(지원 assembly 는 정상 해석). 판별 방법 후보 = 반사실 재실행(코어 수정 불필요).

**RQ4 — NLE 도구 비교**: NumScout/GPTScan/SCType vs IQDebug intent-aware validation. **집합 = 74 (사용자 확정 2026-10-20)**.
⚠ 정정: 앞서 "IntentChecker 비교는 48 에서만" 이라 했으나 틀렸다 — `rq3_comparison_table.csv` 는 48행이지만 IntentChecker 원고의
coverage 표는 GPTScan·NumScout 를 74 전부, ScType 은 적용 가능한 10, IQDebug 는 표현 가능한 48 에서 실행했다.
타 도구 결과는 IntentChecker 것을 출처 명시 재사용, IQDebug 쪽은 현재 엔진 재실행 결과로 교체.
RQ2(절대: 기대 판정 일치)와 RQ4(상대: 타 기법 비교)의 경계를 분명히.

### 1.3 디버깅 입력의 정의 (합의 방향)

- 개념적 정의: *values that may be read by the target function or its statically resolved callees before being determined by the analyzed execution*
  → 파라미터 / overwrite 전에 읽는 상태변수 / global·환경 변수 / 관련 interface 반환값.
- `_derive_debug_slots`(`engine_api.py:883`)는 **정의가 아니라 정의를 근사하는 구현**. 현재 한계:
  스칼라 상태변수를 **타깃 함수 소스 텍스트 매치**로만 판정 → internal callee 에서만 읽는 상태변수 누락 가능(48 중 internal call 34건) /
  이름만 나오면 포함 → 과다 포함 / non-view 외부 호출은 슬롯 없음.
- Forge `-vvvv` 의 SLOAD trace 는 **sanity check(observed read set)** 로만. 실행 1회는 경로 1개라 ground truth 아님.

### 1.4 ⚠ 열린 쟁점 (다음 세션)

1. ✅ **`@LocalVar` → `@Param` 대체 완료 (2026-10-20, 사용자 결정: 별칭이 아니라 대체)**.
   g4 `debugParam : '@Param' varRef '=' debugValue` + 파서 재생성(4.13.2) / visitor `visitDebugParam` / ContractAnalyzer
   `process_param_for_debug` / engine_api `DebugKind="Param"`·`_KIND_KW` / 프론트 `result.ts`·`DebugInputPanel`·`serialize.ts`·mock.
   회귀: temp 16종 전부 통과(test_http·test_vault 는 서버 필요라 미실행), `tsc --noEmit` 0. 파서 백업 = scratchpad `parser_bak/`.
   기존 데이터(`// @LocalVar` 98건)·`tools/generate_case_jsons.py` 는 그대로 — 새 드라이버가 `//` 제거 + `@LocalVar→@Param` 변환.
   전제(사용자): 디버깅 입력 = **타깃 함수의 param / statevar / globalvar / ireturn 만**. 진짜 로컬(192_H_01 `_asset` 1건)은 입력 아님.
   ⚠ 별건 결함: `frontend/src/debug/serialize.ts` 가 4종 모두 아직 `// @…` 형태를 만든다(#37 이후 문법과 불일치). 미수정.
2. 원인 분류 글자 **"U" 가 판정 U(Unreachable)와 충돌** → I 또는 N 등으로. (사용자 고민 중)
3. **singleton 값 선택 규칙** — 고른 점이 `require` 에 걸리면 Forge revert → 비교 불성립. range 안에서 고르고 forge 로 정상 종료 확인 등.
   range 가 모든 경로를 revert 로 보내는 경우(함수 끝 도달 불가) 허용 여부. (사용자 고민 중)
4. **관찰 대상** — 반환값 + 타깃 함수가 변경한 상태변수로 고정 제안(callee 가 쓴 것 포함 여부 결정 필요).
   "NLE 관련 중간값" 은 작성자 선택이 들어가므로 비권장. 끝 시점 모든 local 은 제외. (사용자 고민 중)
5. **48개의 기존 IntentChecker 어노테이션을 RQ1 입력으로 재사용할지** — 사용자: "새로 안 써도 그 값으로 끝까지 돌지 않나".
   구현 담당 의견(미전달): 끝까지 도는지보다 문제는 (a) 기존 값은 **버그를 드러내도록 intent 기준으로** 고른 것이라 RQ1 의
   intent 독립 정의와 어긋나고, (b) intent 에 필요 없던 입력은 비어 있을 수 있으며, (c) 26개는 새 규칙으로 써야 해 **48/26 간 프로토콜이 섞인다**.
   절충안 = 기존 값은 유지하고 빠진 슬롯만 같은 규칙으로 보강.
6. 전수 열거 가능 케이스 수 — 사용자 추가 논의 후 회신 예정.
7. 89→74 제외 사유 원문 확인 (A5).
9. **(A2 결과) 미지정 상태변수·global 이 0/고정값으로 시작** — 사용자: **상태변수 0 은 맞다**(Solidity 기본값). 영향은 사용자 검토 중.
   **callee 의 상태변수는 채우지 않는다**(사용자 확정): 분석 전제가 타깃 함수이고, callee 상태변수를 주면 sound 하지 않을 수 있다.
10. **(B4) contraction 방식** — 사용자: contraction 은 한다(주석 등 제거 필요). ⚠ 그런데 기존 contraction 은 도구 출력이 아니다:
    `tools/preprocess_contraction.py` 로 원본을 처리해도 기존 16개 .sol 이 **하나도 재현되지 않는다**(타깃과 무관한 함수·modifier·
    event 삭제, 여러 줄 require 를 한 줄로, `from→_from` 등 수동 편집). 26개를 (a) 도구만으로 / (b) 48 처럼 수동 축약 — 결정 필요.
8. RQ1 범위 입력의 범위값 규칙(기준값 ±k% / RQ3 단계 `[10,10]→[5,15]→[0,100]→⊤` 등), harness 작성 규칙, 기대 결과의 사전 명시.

---

## 2. 평가 자산 현황

| 경로 | 내용 | 상태 |
|---|---|---|
| `evaluation/Dataset/cases48.csv` | Guardian RQ3 표 사본(RQ4 용 원본 보존, 수정 안 함) | |
| **`evaluation/Dataset/cases74.csv`** (2026-10-20) | 74 = 48 + 26. 열: case_id/source/contract/function/function_alt/target_file/pattern/expressible/group/blocker_tags/note. w77 contract→`MathLib`, w16 target_file=`lib/LibBalances.sol` 정정 | ⚠ 4건 function 복수 지정 → primary 결정 필요(25_H_01 `_peek`/`_get`, 58_H_04 `tvl`/`_push`/`_pull`, 70_H_03 `_calculateUSDVPrice`/`_calculateVaderPrice`, 70_H_09 `mint`/`burn`) |
| `evaluation/Dataset/target_original/` | **74개** (26개 = Guardian `evaluation/RQ1/target_contracts_original/` 에서 복사, 2026-10-20) | |
| `evaluation/Dataset/target_contraction/` | IQDebug 용 contraction | **26개 없음** — 생성 방식 결정 대기(§3 B4) |
| `evaluation/Dataset/Dependencies/` | pool·reconstructed(3)·fetched(4)·Guardian tree 사본 | |
| `evaluation/Dataset/reassemble.py` | 원본 → forge 프로젝트 재조립 + `forge build`. 소스 내용 불변. 2026-10-20 부터 `cases74.csv` 입력 | **74/74 통과**(프로젝트 42; hardhat 도 74/74 — `hardhat_gate.py`) |
| `evaluation/forge/` · `evaluation/hardhat/` | Foundry 1.8.4(`install.ps1`) / Hardhat 2.29.1 | hardhat 게이트 미실시. `hardhat/package.json` 이름 HH412 수정함 |
| `evaluation/playground/` | 두 도구 실습(`Vault.sol`, forge test·cheatcode·fuzz, hardhat JSON-RPC 스크립트) + `README.md` | 검증 완료, 사용자 실습 예정 |
| `main.py:111-112` | interface 스캔 경로 → `evaluation/Dataset/...` | |

자산 결함·경위 상세 = 부록 decision **(51)**. 논문에 적을 것 ★: numscout 원본 교체 + SwordCrowdsale "simplified reproduction".
⚠ 기존 결함: `tools/generate_case_jsons.py`·`tools/preprocess_contraction.py` 가 없는 경로(`tools/target_contracts_contraction`)를 가리킴.
⚠ `Dependencies/main.py` 의 `_con_order` 하드코딩 — 26개 신규 의존성 pkl 생성 시 걸림돌.

---

## 3. 할 일 목록

표시: 🔍 읽기만 · ✏ 생성/수정(승인) · ⚙ 코어 수정(별도 승인) · ⏸ 사용자 결정 대기

**A. 사전 조사** (2026-10-20 결과)
- A1 ✅ 26개 원본 = Guardian `target_contracts_original/` 전부 존재(타깃 컨트랙트·함수 확인). 타깃 함수 출처 =
  `evaluation/RQ1/annotation_plans.md` 의 Contract/Function. ⚠ 사용자: annotation_plans·l4_l5 는 옛 자료, **기준은 Guardian
  `evaluation/RQ2/`**. 그 폴더에는 `rq2a_specification_profile.xlsx`·`rq2b_latency.csv` 뿐이고 xlsx 에 contract/function 열이 없다.
  xlsx `source_file` 이 가리키는 최신 근거 `First Revision/phase_reviews/NN_<case>/analysis.md`(26개 중 8개)는 **로컬 어디에도 없다**.
  xlsx `finding_level_note` 의 Member A/B = 한 finding 에 함수·의도가 여러 개인 경우(48 쪽 선례 35_H_08 `mint;burn`). `blocker_tags` α/β/γ 정의 = `evaluation/RQ2/l4_l5_case_review.md:42-44`
  (α 관계에 함수 호출 필요 / β 관련 변수 자체가 scope 에 없음 / γ 다지점 회계). **전부 intent 표현 문제 → 엔진 분석을 막지 않음.**
  단 annotation_plans status 상 L3 unsupported-construct-top(44_H_02, 8_H_03)·L1 loop(34_H_01, 45_H_02, 70_H_03, 71_H_11) 존재.
- A2 ✅ ★ **미지정 상태변수 = ⊤ 가 아니라 Solidity 0 기본값**(uint/int/bool [0,0], initializer 있으면 그 값, address ⊤,
  동적 배열 len 0, mapping entry ⊤). 파라미터 ⊤, **global 은 고정값**(timestamp/msg.value/block.number [0,0], msg.sender {101}).
  근거: ContractAnalyzer.py:946-993, Evaluation.py:3631-3653, StaticCFGFactory.py:23-104, 331-338.
  → 미지정 입력이 ⊤ 로 드러나지 않고 **정밀해 보이는 0** 으로 나온다(쟁점 9).
  ★ **callee 에서만 읽는 상태변수는 슬롯에서 빠진다** 확인(engine_api.py:991-993, 1010-1011). 엔진은 callee 로 하강하며 caller 의
  entry 값(=미지정 시 0)을 쓴다. 실케이스: EthereumGod·HIT·29_H_08·62_H_03·62_H_08 에서 누락, 기존 수동 어노테이션이 그 대부분을 직접 채웠었음.
- A3 ✅ 48 JSON 전부 구문(`// @…`). **`//` 접두어만 떼면 443/443 파싱**(#41 문법 변경 불필요). 그대로 돌리면 조용히 실패.
  접두어 제거 후 5_H_07·5_H_08·45_H_01 = Violated(옛 게이트와 일치). 단 `"// @"` 분기가 main.py:142-156·SolidityAnalyzer.py·
  ContractAnalyzer.py:131 에 있어 **파일을 고치면 코어 수정** → **드라이버에서 접두어 제거(사용자 승인)**. engine_api 경로는 새 드라이버 필요
  (debug 값 dict / IntentItem 변환) + **DepSpec 없이 32/48 실패**(interface/library 미해결).
  중복 JSON 7개: **정본 = `cases/<id>/<id>.json`**(Guardian run_all.py 가 쓰는 경로, `@Debugging BEGIN/END`·수정된 intent 포함).
  상위 `cases/<id>.json` 7개는 옛 초안(BEGIN/END 없음, 예: 56_H_02 `totalCredit(entry <= exit)`, 58_H_02 다른 값).
- A4 ⏸ 루프 분류 — 74 확보 후
- A5 ✅ E1–E8 = IntentChecker 리비전 원고 `paper/first_revision/main.tex:2113-2125` 와 일치(E1 패치된 소스 3, E2 overflow 2, E3 중복 2,
  E4 버그 아님 1, E5 의존성 없음 2, E6 multi-tx 3, E7 언어 의미 1, E8 타깃 오인 1).
- A6 ✅ `@Param` 은 **별칭 추가로 가능**(`debugLocalVar` 에 `| '@Param' varRef '=' debugValue`, intent 규칙 무관, 파서 재생성 후
  `temp/test_snapshot_grammar.py` 재확인). `@LocalVar` 의 진짜 로컬 사용은 1건(192_H_01 `_asset`). 의미상 @StateVar 와 동일 처리.
  영향: g4·생성파서·visitor:915·ContractAnalyzer:3068·engine_api 4곳·프론트 3파일(serialize.ts 는 아직 `// @LocalVar` 생성 — 별도 결함)·
  데이터 98건·tools 2·문서. ⚠ CLAUDE.md 의 "`_con_order` 하드코딩" 서술은 낡음 — 이미 topo-sort(Dependencies/main.py:412+).

**B. 데이터**
- B1 ✅ 26개 원본 복사(Guardian → `target_original/`). 의존성은 기존 pool/flat 힌트로 대부분 해결.
- B2 🟡 forge 게이트 **73/74** (2026-10-20 수정 승인 후). w110 = `NPM_MAP` 에 `openzeppelin-contracts-upgradeable/`→ozu 추가 ✅ /
  w34 = v4-core@35b00f7 package.json 고정 버전 `@pooltogether/owner-manager-contracts@1.1.0`·`pooltogether-rng-contracts@1.4.0`
  npm 설치 + `NPM_MAP` ✅ / w17 = `ROOT_IMPORT_PREFIX={"w17":"contracts/"}`(+ remapping `contracts/=src/`)로 루트 기준 import 해결했으나
  **upstream contest 저장소(code-423n4/2021-06-gro) 자체에 `contracts/interfaces/IChainlinkAggregator.sol` 이 없다**(타깃은 upstream 과
  바이트 동일) + `@chainlink/contracts@0.0.11` 미설치 → 복원 여부 결정 대기.
- B3 ✅ hardhat 게이트 **73/74** (실패 w17 = forge 와 같은 데이터 결함). `evaluation/Dataset/hardhat_gate.py`(신규):
  remappings.txt → `projects/<p>/node_modules/<prefix>` **junction**(사용자 승인 방식). 대상이 패키지 하위 폴더면(solmate/src,
  복원 bentobox-sdk) package.json 래퍼 + 하위 폴더 junction. 생성물은 manifest(`.hh_gate_manifest.json`)대로만 삭제(대상 내용 불가침).
  컴파일러 = 2021~22 시대 solc 목록(forge 에서 핀한 프로젝트는 그 버전만). 보고서 `hardhat_gate_report.json`.
  부수 수정: `reassemble.py --only` 가 보고서 전체를 덮어쓰던 기존 결함 → 해당 항목만 갱신.
- B4 ⏸ contraction 은 import·주석·**constructor 제거**, 한 줄 블록 전개 등 소스 변형 전처리. 26개를 같은 방식으로 만들지,
  RQ1 에서 원본을 직접 분석할지 결정 필요(쟁점 10). 의존성 pkl 은 contest 5·45·47·51·112 분만 있음 → 26개 의존성 추가 필요.
- B5 ✅ `cases74.csv` (위 §2)

**C. IQDebug 실행**
- C1 ⏸ 입력 정의 세부·singleton 규칙·관찰 대상 확정 (§1.4)
- C1' ✏ 74개 RQ1 입력 명세 작성(정의 기반 규칙, `_derive_debug_slots` + callee 보강) — 쟁점 5 결과에 따라 범위 변동
- C2 ✏ 함수 끝 관찰 runner (`engine_api` analyze→evaluate)
- C3 🔍 74개 실행 → **Failed 목록 보고(고치기 전)**
- C4 ⚙ 승인된 수정 적용 + engine_fixes 기록
- C5 ✏ 필요 시 `_derive_debug_slots` callee 보강

**D. Forge/Hardhat harness**
- D1 ✏ storageLayout → `vm.store`/`setStorageAt` 생성기
- D2 ✏ GlobalVar(warp)·IReturn mock·internal harness·private→internal 패치 + 바이트코드 diff 0
- D3 ✏ forge runner: singleton / 열거·fuzz / SLOAD trace(sanity check)
- D4 ✏ hardhat runner (Python JSON-RPC)

**E. 측정**
- E1 ⏸✏ latency (IQDebug 연산별 / forge·hardhat compile·deploy·seed·run 분리)
- E2 ⏸✏ 증분 재측정 프로토콜(편집 스크립트)
- E3 ✏ 정밀도 원인 분류(반사실 재실행)
- E4 ✏ soundness check(항상) + over-approx ratio(전수 열거 가능 케이스만)

**F. RQ2/RQ4**
- F1 🔍✏ 현재 엔진으로 48 재검증 → IntentChecker 판정과 비교, 차이 보고
- F2 🔍 U(Unreachable) 사례 집계

**G. 문서** — G1 ✏ 단계마다 이 문서 §0–§3 갱신

제안 순서: A → B → C2–C3 → D → E → F.

### 3.1 2026-10-20 실행 결과 (요약)

- **main.tex Evaluation 장 ↔ 실험 대조 (사용자 요청: 글 검토가 아니라 실험 정합성)**. 원고 수정은 ChatGPT 담당, 사용자가 전달.
  충돌 → 처리: ① RQ1 "callee 전용 상태변수도 필요 입력" → **결정과 반대**(타깃만, callee 상태 미지정) — 원고 수정 예정,
  "concrete 결과는 IQDebug 범위 안의 한 점" 서술로 / ② RQ4 집합 → **74** 확정 / ③ RQ3 검증 수치(33/14/1)는 엔진 오류 수정 후
  재실행 값으로 교체, Unreachable 행 추가 / ④ OS = Windows 11 Pro, Python 3.10.11 / ⑤ Remix 제외 근거 — 사용자·ChatGPT 논의.
  추가로 원고에 "IQDebug 입력 = contraction(원본 아님)" 명시 필요.
  표·그림별 결과 생성 준비도(✓ RQ3 표현력·검증 형식 / △ RQ1 준비표 IQDebug 쪽, edit 표(update 경로 표시 없음 + engine_api.py:1486
  surgical 실패 시 예외 삼키고 rebuild 폴백), RQ2 분기(B_C = forge coverage 로 가능), 루프(반복 횟수·widening 계측 = 코어),
  RQ4 IQDebug 10회 반복 / ✗ Forge·Hardhat runner, harness 생성기, 프로젝트 단위 pkl, 제외 15건 출처별 내역) — **엔진 오류 수정 후 재논의**.

- **게이트**: forge **74/74**, hardhat **74/74**. w17 = 복원 `Dependencies/reconstructed/IChainlinkAggregator.sol`(upstream 누락,
  MockAggregator·호출부에서 복원) + `@chainlink/contracts@0.0.11`(gro package.json 고정) + `ROOT_IMPORT_PREFIX` / hardhat 은 프로젝트
  루트 `contracts` junction 을 sources 로(HH415 회피).
- **cases74.csv 타깃 정정**: Guardian `evaluation/RQ1/cases` 어노테이션 위치 기준 — 35_H_08 `mint;burn`→**burn**, 35_H_12 `mint`→**burn**,
  62_H_03 `recoverTokens`→**claimReward**, 62_H_10 `recoverTokens`→**creatorClaimSoldTokens** (RQ3 표 값이 틀렸음). 70_H_09 = burn(사용자).
  25_H_01·58_H_04 는 케이스 파일·phase_reviews 모두 없음 → primary 미정.
- **★ 상태변수·글로벌 기본값 코어 수정 (사용자 승인)**: 상태변수(initializer 유무 무관, constant/immutable 제외) = 타입 ⊤,
  고정 길이 numeric 배열 원소 ⊤, `block.*`·`tx.gasprice`·`msg.value` = ⊤, **non-payable 함수의 msg.value = [0,0]**
  (`ContractAnalyzer._narrow_msg_value_for_nonpayable`). 로컬·named return 0 유지. 근거 = IntentChecker 원고 main.tex:468-470,993-997.
  변경: `ContractAnalyzer.process_state_variable`, `StaticCFGFactory._create_global_variables`. 백업 scratchpad `core_bak_default/`.
  temp 회귀 16종 통과. ⚠ 미해결: 동적 배열은 여전히 len 0 / `_sync_constructor_state` 가 생성자 결과를 상태변수 노드에 반영(배포 직후 가정) /
  `immutable` 무초기화식 처리 미확인 / engine_api `top_vars` 가 numeric ⊤ 는 보고하지 않음(AddressSet 만).
- **새 RQ3(검증) 48 재실행** — 드라이버 `evaluation/intent_validation/run_cases.py`(케이스 파일 불변, `//` 제거 + `@LocalVar→@Param`,
  케이스별 새 프로세스), 결과 `results_before.json`(수정 전) / `results_after.json`(수정 후).
  IntentChecker 판정 일치: **수정 전 41/48 → 수정 후 38/48**.
  - 수정 전부터 다름: EthereumGod Satisfied(기대 A:Violated/B:Unsupported) / 16_H_04 결과 없음(ANTLR 파싱 오류 1) /
    16_H_06·36_H_02 **Unreachable**(기대 Warning/공란) / 35_H_10 Warning(기대 공란) / 58_H_02 Warning(기대 Violated) /
    **78_H_02 엔진 예외** `NoneType.is_bottom` (Interval.divide) — **Failed**.
  - 수정으로 바뀐 것: **Nokon V→W** (payable `buy` 의 msg.value 미지정 → 이제 ⊤; 기존 어노테이션이 0 기본값에 기대고 있었음) /
    **3_H_05 V→W** (미지정 상태값 의존) / **52_H_23 W→엔진 예외** `'str' object has no attribute 'multiply'`
    (Evaluation.evaluate_binary_operator:2303, ⊤ 화로 드러난 결함 — 원인 미조사) — **Failed**.
  - 엔진 판정 용어 불일치: 만족이 경로에 따라 `satisfied`/`success`(Engine._combine_logic_results). 드라이버는 둘 다 Satisfied 로 집계.
  - 총 해석 시간 435s → 551s.
- **엔진 오류 원인 조사 (2026-10-20, 수정 전 — 승인 대기)**: 스크래치 monkeypatch 로 재현·해소 검증(`scratchpad/agentErr/`).
  - **A. `_force_join_before_exit`(Engine.py:1416-1421) 가 EXIT 의 선행만 join 하고 RETURN 노드 쪽 선행을 빠뜨린다** → 조기 `return` 만
    있는 함수(PRBMath.mulDivFixedPoint)의 EXIT 가 ⊥ → 호출자에 ⊥ 상태가 역기록되어 경로 전체가 죽음. 16_H_04(결과 없음)·16_H_06(Unreachable)
    원인. 패치 후 16_H_04 Warning(일치), 16_H_06 **Violated**(IntentChecker Warning). ★ 조기 return 이 있는 모든 함수의 @Post 에 영향.
  - **78_H_02**: 의존성 pkl 이름 충돌 — OZ `ERC20` 과 solmate `ERC20` 이 모두 `con_ERC20.pkl` 로 저장(나중 것이 덮어씀, 무경고,
    Dependencies/main.py:814) + load_dependencies 가 숫자 접두어를 떼서 `con_51_ERC20` 도 가림 → 본문 없는 interface 선언으로 해석되어 None.
  - **52_H_23**: 케이스 레코드에 `using UQ112x112 for uint224;` 누락(케이스 구성 결함) → 미해결 멤버가 `"symbolic(...)"` 문자열로 새어
    나감(Evaluation.py:1915). ⊤ 화로 `timeElapsed>0` 분기가 열리면서 드러남. 관련 잠복 버그 Evaluation.py:1459 `bits`→`type_length`.
  - **16_H_04 레코드**: 옛 슬라이서 형식(`} else if` 에 closeBefore 없음) — 케이스 재생성 필요.
  - **36_H_02**: contract 타입(Ownable) 외부 호출 반환형을 `ifc_` pkl 에서만 찾아 실패 → "uint256 top" 폴백(Evaluation.py:2629)이 address
    자리에 들어가 `require(account != address(0))` 가 불가능 → Unreachable. 로드된 contract_cfgs 에서 반환형 조회로 수정 시 Violated.
  - 부가: `@During` 이 ⊥ 노드면 아무것도 안 냄(unreachable 로 보고해야), public 상태변수 getter 미지원, `TokenProxyLike.parent_cfgs['IERC20']`
    가 `iERC20` CFG 를 가리킴(이름 충돌 의심).
- **contraction 수동 규칙 역추적 (48)**: 텍스트 callee 폐포로 남긴 함수 집합 일치 **33/48**. 불일치 15건은 수동 작업 자체가 비일관
  (numscout 류는 생성자·onlyOwner·무관 함수까지 유지하고 일부 callee 는 삭제, 42_H_01 은 callee 6개 삭제, JSON 케이스는 다른 파일의
  interface 를 인라인). 수동 편집 = 다줄 문장 합치기(다수) + 식별자 rename. 문법 토큰과 충돌하는 식별자(74 원본):
  `from` 8케이스, `array` 3(45_H_02·5_H_07·5_H_08), `Exit` 1(39_H_02). 현 도구는 `from` 을 **파라미터일 때만** 바꾼다.
- **RQ1 시간 모델 (논의 중, 사용자·ChatGPT)**: 단일 지표 T_result, 시나리오 base/edit/scaling. base 는 타깃 처리 포함(IQDebug analyze ↔
  타깃 증분 컴파일+배포), 의존성 준비·도구 기동 제외. scaling = T_fixed + N·T_per-state (+ (N−1)·restore), IQDebug = T'_fixed + T_analysis(S_N).
  N = 범위 상자의 상태 수(전수 열거). ⚠ forge 는 CLI 라 기동 제외 불가 → no-op build 시간을 별도 측정해 보정 제안. edit 프로토콜은 논의 중.

---

## 4. 실행/환경 메모

- 엔진 실행: **반드시 `.venv\Scripts\python.exe`** (글로벌 Python310 networkx 2.5.1 깨짐).
- ⚠ `evaluation\RQ1\run_all.py` → 20/20 VIOLATED 는 **더 이상 유효하지 않다**(#37 로 의도된 0/20 회귀).
  **버그-탐지 e2e 회귀 스위트는 없다.** `temp/` 의 구조 회귀 스크립트만 있음(CFG·states·문법 무결성용).
- ANTLR: CLI 4.13.2 / 런타임 `antlr4-python3-runtime==4.13.2`. 재생성 = `Parser/` 에서 `antlr4 -Dlanguage=Python3 -visitor Solidity.g4`.
- `load_dependencies` interface 스캔 3개 디렉토리: `evaluation/Dataset/target_contraction`, `evaluation/Dataset/Dependencies/pool`,
  `Dataset/Numscout/contraction`. 없으면 interface 호출 해석이 조용히 실패.
- forge: `evaluation\forge\bin\forge.exe` (gitignored, `install.ps1` 로 재설치). hardhat: `evaluation\hardhat` 의 `npx hardhat`.
- 프론트: `cd frontend; npm install`(1회), `npm run dev`, 타입체크 `npx tsc --noEmit`.
- 서버 세션은 `contract.func` 키로 메모리 영속 → 테스트 컨트랙트가 프론트 세션을 오염시킬 수 있음(재시작으로 클리어).

## 5. 작업 규칙 (요약 — 원문은 CLAUDE.md / memory)

- 객관적·비판적 검토 / 수정·생성·삭제는 승인 후 / 모호하면 질의 / 코어 수정은 별도 승인.
- **오류는 고치기 전에 보고 + 수정 방안 제시. 폴백 금지, 근본 원인 수정.**
- **SolidityGuardian 디렉토리 수정 금지**(복사는 Guardian→IntentKeeper 단방향). 동기화는 2026-07-18 부로 중단.
- intent 문법(@During/@Post, `Parser/Solidity.g4`)은 논문 제출본이라 변경 금지.
- 엔진 수정 내역은 `(to_solidityguardian)_engine_fixes.md` 에 별도 기록.
- 논문 문장·framing 은 ChatGPT 담당, 구현 담당은 구현·실험·데이터·확정 LaTeX 삽입·PROGRESS 유지.

---

## 6. 디버깅 조작(navigation) 명세 — 확정본 (2026-10-23 정리)

> 출처 대조: `PROMPT_frontend_impl.md` §2(확정 결정 1–13)·§3(미결 U1–U6), `REFACTOR_PLAN.md` §6 2026-10-22 (6)
> (사용자 결정 F1–F4, U2–U6), 구현 `frontend/src/debug/{navGraph.ts, useNavigation.ts, DebuggerControls.tsx, CodePane.tsx}`.
> 결정과 구현이 다른 곳은 **⚠ 불일치**로 표시했다(고치지 않음 — 결정 필요). 출처에서 내용을 확인할 수 없는 항목은 **❓**.

### 6.1 기본 전제
- **탐색은 전부 로컬**이다(탐색 중 엔진 호출 0). 엔진은 analyze → evaluate → verify 에서 노드별 상태를 미리 계산해 내려주고,
  프론트는 그 위에서 커서만 옮긴다. 탐색은 상태를 바꾸지 않는다.
- **커서 = "이 노드 실행 직전"**, 표시 상태 = 엔진이 내려준 실행 전 상태 `in_state`(U1). 노드 식별은 불변 `uid`.
- **루프 헤더의 실행 전 상태 = 루프 밖에서 들어오는 상태**(F2). 루프 효과는 헤더의 loop delta 와 loop exit 상태로 본다.
- if/else-if join 의 표시 줄 = 체인을 닫는 `}` 줄(`display_line`, U3).

### 6.2 착지 규칙 (`navGraph.isLandable`)
- 착지 가능: 문장 노드, 조건 노드, join(merge), loop exit, 함수 EXIT(= 최종 상태).
- 착지 불가: ENTRY, 분기 더미(then/else 블록 시작), 루프 φ(fixpoint_evaluation), **루프 본문 전체**, RETURN/ERROR sink,
  **바로 다음이 또 join 인 join**(else-if 체인의 안쪽 join — 연속 join 은 한 step 으로 접는다).
- `return` 뒤 RETURN sink 는 함수 EXIT 로 착지한다(정상 반환 = 함수 끝).
- 도달 불가(⊥) 노드에는 착지하지 않는다.

### 6.3 연산 집합 (`DebuggerControls`)
| 연산 | 동작 |
|---|---|
| Step Forward | back edge 를 뺀 feasible 후속의 착지 노드로. 루프 헤더에서는 **loop exit 으로**(루프에 들어가지 않는다). 후보가 여럿이면 첫 후보(엔진 edge 순서 = true 먼저)로 가고 나머지를 하이라이트 |
| Step Backward | feasible 선행의 착지 노드로. 여럿이면 첫째로 가고 나머지 하이라이트. EXIT 의 선행에는 `return` 경로(RETURN sink 의 선행)도 포함 |
| Select Successor / Select Predecessor | 후보가 2개 이상일 때만 표시. 그 간선을 따라 처음 나오는 착지 노드(분기 첫 문장)로. **초점 선택**이며 "그 분기가 실행됐다"는 뜻이 아니다. 선택 기록에 의존하지 않는다 |
| Step Into | 노드의 진입 가능한 callee 로 프레임 push(callee 첫 착지 노드). 같은 노드에서 반복하면 **엔진의 평가 순서대로 다음 호출**. 다 지나면 "이 줄의 호출을 모두 지났습니다" |
| Step Out | pop — **호출 지점 노드**로 복귀(그 노드 상태 = 호출 효과 반영) |
| Continue | 다음 도달 가능한 breakpoint 로. **진입 가능한 callee 안의 breakpoint 까지 내려간다**. callee 를 다 지나면 부모의 호출 지점(다음 callee 순번)에서 이어서 찾는다. 없으면 함수 끝(타깃 EXIT) |
| Next Breakpoint | Continue 와 같되, 없으면 이동하지 않고 안내만 |
| Prev Breakpoint | **현재 프레임 안에서** 선행을 거슬러 처음 만나는 breakpoint. 여럿이면 첫째로 가고 하이라이트 |
| Next / Prev Intent | 타깃 프레임 기준, 판정이 있는 intent 줄을 **줄 번호 순서**로 |
| Restart | 타깃 함수 첫 착지 노드로, 스택은 타깃 하나로 리셋 |
| (패널) Call Stack 프레임 클릭 | 그 깊이까지 pop(`popToFrame`) |
| (패널) Verdict 클릭 | 그 줄의 착지 노드로, 스택은 타깃 하나로(`jumpToLine`) |

- 제외: Step Over Branch(F3), 기록 기반 Navigate Back/Forward, Run to Line(breakpoint 와 동치), Select Call, 루프 진입.

### 6.4 분기점과 Continue (U2)
- 각 feasible 경로에서 처음 만나는 멈출 곳을 모은다. 하나면 그곳으로.
- **여러 feasible 분기에 서로 다른 breakpoint 가 있으면 분기가 갈라지는 지점에서 멈추고** Select Successor 를 요구한다
  (고정 순서로 하나를 고르지 않는다 — 다른 분기의 breakpoint 를 조용히 놓치지 않기 위해).
- breakpoint 가 join 이후에만 있으면 경로와 무관하게 그곳에 도달한다.

### 6.5 breakpoint
- 키 = **(소스 파일, 줄)**(U6). 프레임별이 아니라 파일별이라 Continue 가 callee 안에서도 멈춘다
  (파일 키: 편집 중 파일 = `''`, 의존성 = `dep:<컨트랙트>`).
- **착지 노드가 있는 줄에만** 걸 수 있다(`canBreakpointOnLine`). 따라서 **루프 본문 줄에는 걸 수 없다**(U5),
  `}`·구조적 줄에도 걸 수 없다.
- 도달 불가 줄의 breakpoint 는 막지 않고 표시만 바꾼다(그 줄의 착지 노드가 전부 ⊥ 이면 `isLineUnreachable`). 거기서 멈추지 않는다.

### 6.6 evaluate(Apply) 직후
- 스택을 타깃 프레임 하나로 리셋하고, entry 에서 Continue 한 위치(첫 도달 breakpoint)로. breakpoint 가 없으면 **함수 EXIT(최종 상태)**.
- 코드 편집(analyze)만 바뀐 경우: uid 가 그대로인 노드는 커서를 유지하고, 사라진 노드·callee 를 가리키는 프레임이 있으면 타깃 첫 착지로.

### 6.7 호출 범위
- Step Into 대상 = 같은 컨트랙트 internal 호출, 상속(부모·`super`·`Parent.f()`) 함수, 라이브러리 호출.
  **실제로 해석(하강)한 호출만** 진입 가능 목록에 오른다(엔진 `_capture_callee_states` 적재분).
- 주소/인터페이스/컨트랙트 타입 변수를 통한 외부 호출 = **opaque**(진입 불가, 사유 표시). 반환값 = 선언 타입 ⊤ 또는 `@IReturn`.
- 재귀: 재진입 지점은 엔진이 요약(⊤·havoc, 2026-10-23 D7 구현)하므로 해석되지 않고, 따라서 진입 목록에 오르지 않는다(첫 호출은 진입 가능).
  프론트에는 안전 상한 `MAX_CALL_DEPTH = 16` 이 따로 있다.
- 함수 포인터 호출(2026-10-23, ⊤ 요약)도 해석하지 않으므로 진입 불가.

### 6.8 ⚠ 결정과 구현이 다른 곳 (결정 필요)
1. **Step Forward 의 다중 후보**: `PROMPT_frontend_impl.md` §2-7 은 "feasible 후속이 2개 이상이면 **이동하지 않고** 후보를 제시 →
   Select Successor 로 고른다"인데, 구현은 **첫 후보로 이동**하고 나머지를 하이라이트한다(Select 로 형제 후보로 갈 수 있음).
   Step Backward 도 같다.
2. **feasibility 판정**: §2-13 은 "문자열 탐지가 아니라 엔진이 노드 상태 분류(reachable / unreachable / error-sink)를 명시적으로
   내려준다"인데, 구현 `isDead` 는 여전히 상태 문자열에 `'⊥'` 가 있는지로 판정한다.
3. **왜 도달 불가인가**: §2-12 는 "엔진이 원인을 기록"인데, 구현 `whyUnreachable` 은 프론트에서 죽은 구간을 거슬러 올라가
   처음 만나는 살아 있는 조건 노드를 찾는다(엔진 기록 없음).
4. **Next/Prev Intent**: §2-9 는 "판정별(예: Violated/Warning)"인데, 구현은 판정 종류 필터 없이 intent 줄 순서로만 이동하고,
   타깃 프레임에서만 동작한다(callee 안 intent 는 대상 아님).
5. **callee 첫 문장에서 Step Backward**: 선행이 ENTRY 뿐이라 이동하지 않는다(자동 Step Out 없음). §2 에 규정 없음 — 의도 확인 필요.

### 6.9 ❓ 출처에서 내용을 확인할 수 없는 것
- **F1·F4**: REFACTOR_PLAN 에 "사용자 결정 F1~F4" 로만 남아 있고 내용이 없다(F2 = 루프 헤더 IN, F3 = Step Over Branch 제외는 확인).
  engine_fixes F65 제목의 "F1" 은 노드 uid 또는 실행 전 상태로 보이나 단정할 수 없다.
- **U4 (modifier 흐름 모델)**: 구현은 modifier 를 별도 프레임으로 두지 않고 합성 CFG(`modifier::노드`)를 평평하게 따라간다.
  §2-6 "modifier 코드가 화면에 보여야 한다"가 충족되는지(다른 파일의 modifier 소스 전환), modifier 본문 intent 허용 여부는
  문서에서 확인되지 않는다.

---
---

# 부록 — 이전 기록 (2026-10-05 이전, 참고용)

> 아래는 재정리 전 PROGRESS.md 원문이다. 섹션 번호 앞에 `부록-` 을 붙인 것 외에는 수정하지 않았다.
> 내용 중 "현재/다음 할 일" 류 서술은 작성 시점 기준이며, **현재 상태는 위 §0–§3 이 우선한다.**

---

## 부록-0. 한 줄 요약

**제목 (2026-09-17 선정, 변경 가능):**
> **An Intent-Aware Quick Debugging Assistant for Solidity**
>
> 형용사 없는 형태로 확정. 이전 잠정 제목 `Towards Safer Solidity Development: An Intent-Aware Quick
> Debugging Assistant`(2026-06-22)와 그 선정 근거는 폐기. 논문 방향의 정본은 **`PROGRESS2.md`**
> (Plan A = intent-aware abstract debugging assistant 필수 / Plan B = intent-guided debug input
> generation 선택; LLM·intent 자동생성·repair·multi-tx 제외).
> `paper/Phd_thesis/main.tex` 의 4곳(표지·속표지·초록 헤딩·`pdftitle`)에 반영 완료.

**IQDebug** = SolQDebug(게재) + IntentChecker(심사중) 엔진을 확장한 **intent-aware quick debugger** (박사학위 3번째 컴포넌트).
- 엔진 = interval abstract interpretation, multi-contract single-tx, intent(@During/@Post) 검증. (IntentKeeper에 동기화 완료, 20/20 VIOLATED 검증.)
- 추가 = 인터랙티브 디버거 UI(step/breakpoint/back navigation) + LLM/solver debug-value 제안 + precompute(후순위).
- 현재 단계: **증분 편집 아키텍처(B1 + T-stmt) 동작** (2026-06-22). `EngineSession.update(new_source)` =
  SyntaxValid 게이트 → record-space diff → 변경분만 적용(delete=`delete_cfg_range` SESE 수술, 조건 modify=
  in-place, simple modify=delete+add). `engine_server /analyze` 가 첫 호출 fresh·이후 증분. **프론트 무변경**.
  end-to-end analyze→update→evaluate→verify 가 fresh 빌드와 등가(temp 테스트 61개 통과). 코어 변경:
  `DynamicCFGBuilder`(delete/modify 메서드 추가), `ContractAnalyzer`(process_if/while/for modify 분기),
  `Engine.py`(freeze 게이트 + warning-combine), `engine_api`(update/closeBefore). 상세·잔여 = §5.6.
  **⚠️ 정정(2026-07-13) → 해결(2026-07-18, decision #37)**: 이 "동작"은 오래도록 **주석 없는 인공 소스
  한정**이었음 — 실제 .sol(첫 줄 `// SPDX-…`)은 SyntaxValid 게이트가 거부해 증분 편집이 전혀 적용되지
  않았고, 프론트가 diagnostics 를 삼켜 **조용히 실패**했다. 문법에서 어노테이션의 `'//'` 를 제거하고
  `LINE_COMMENT` 를 복원해 해결(#37), 진단 표시도 추가(#38). **이제 실제 .sol 에서 동작한다.**
- **주력 주제 = decision #40**(2026-08-05) "intent 위치 debug 입력 자동 생성" — 목적은 버그 판정이 아니라
  **warning 을 판정이 확정되는 입력 영역들로 분할**(verdict-guided partitioning). (A1) falseRegions·
  (A2) Unreachable ✅ / (B) 분할 탐색기 미착수.
- **문법 최신 상태 = decision #41**(2026-09-16): snapshot-qualified reference 가 `arithFactor` 1급 항
  (`balance(Exit) == balance(Entry) - amount`). @Post/@During 스코프는 파서 멤버 `inDuring` 으로 게이트.
- 툴명 = IQDebug, repo/project = IntentKeeper 유지.

---

## 부록-1. 확정된 설계 결정 (decisions log)

1. **UI = 두 화면 분리** (옵션 2): Workspace(편집+deps 등록) ↔ Debug view(함수 focused). 상단 탭 전환.
2. **분석은 백엔드 live(edit-time), 표시만 화면 전환**: 분석 트리거 = 코드편집/디버그입력/intent/deps 변경. 화면전환·step navigation은 트리거 아님(표시일 뿐).
3. **navigation은 전적으로 로컬**: 분석 1패스가 함수 전체 program-point 추상상태를 다 계산 → CFG-with-states 통째 전송 → step/back/into/out은 그 위 커서 이동(엔진 호출 0). = "quick" 정체성의 구조적 근거.
4. **step 의미 재정의** (concrete 디버거 아님, path-insensitive AI):
   - backward = 시간되감기 아니라 **provenance 추적**.
   - merge node backward = 비-⊥ predecessor **전부 하이라이트**(⊥ 분기는 dead로 표시). SC는 분기 적어 follow-all 기본.
   - loop = iteration별 step 없음. step forward 진입 시 **fixpoint로 접어 표시**, backward가 loop exit면 **fixpoint evaluation node** 조회. 본문값 = 루프 불변식 라벨.
   - step into: 외부/cross-contract(@IReturn)는 "opaque, 못 들어감" 표시 = derivable/opaque 경계 노출.
   - continue = 다음 (라인) breakpoint 또는 함수끝까지 전진. prev/next breakpoint 버튼 있음.
5. **Stack&Memory / Tx Details 제외** (바이트코드 아님). Tx 대신 @GlobalVar 구간 선언.
6. **debug 입력 = 분석-주도 슬롯, 값만 입력** (함수-스코프):
   - @StateVar(상태변수 선언 시), @LocalVar(함수 파라미터/로컬), @GlobalVar(static 화이트리스트), @IReturn(인터페이스 호출 statement 등장 시).
   - 사용자는 targetExpr/funcName/accessChain을 타이핑 안 함 → 엔진이 슬롯 제공. **값만** 입력.
   - 값 타입 = 슬롯의 valueKind 결정: interval / bool / address(symbolicAddress). array/bytes/enum은 advanced.
7. **intent = 코드 밖 패널 메타데이터 + lineRef** (코드 삽입/밀림 없음):
   - @During = 라인-스코프(코드 라인 옆) → 라인-클릭 부착 UX(미구현, (a)).
   - @Post = 함수-스코프(entry/exit).
8. **annotation "삽입 없는 적용"**: 코드는 순수 Solidity. 어노테이션은 캐논 텍스트로 직렬화 → engine_api가 *그 텍스트만 파싱*해 lineRef/함수 노드에 등록, `update_code` 삽입 단계 생략 → 라인 밀림 원천 제거.
9. **LLM vs solver 결정 보류**: suggestion은 backend-agnostic(`backend: llm|solver|hybrid`). 무엇이 나은지는 L2 실험이 결정(derivable→? opaque→? 경험적 매핑이 originality).
10. **프론트 = 로컬 웹앱**(Vite+React+TS+Monaco), VSCode 아님(DAP 임피던스·커스텀 패널 자유도·엔진 editor-event 정합). localStorage 영속.
11. **코어 수정은 승인 후에만**. 지금까지 엔진 코드 미수정(읽기만). 기존 출력 경로 보존(run_all.py 20/20 = 회귀 가드).

### 부록-1.1 추가 확정 (2026-06-19 세션)

12. **두 레이어로 분리**:
    - **결정론 코어**(abstract interpreter, edit-time, 빠름) = `analyze`/`evaluate`/`verify`.
    - **생성 보조**(solver/LLM, async, 느림, L2 originality) = `suggest_value`/`reach`/`cover`/`suggest_intent`.
    - 보조는 코어의 *자식*이 아니라 위에 얹힌 *형제* — 코어 출력(CFG/states/path condition) + 추가입력(주석/패턴) 소비.
13. **debug는 analyze의 하위 함수가 아님** — 같은 분석을 *값으로 파라미터화한 재호출*. navigation은 엔진 0콜(프론트 로컬). 슬롯(debugSlots)은 analyze 산출물(구조적, 값 이전).
14. **3계층 = 캐시 무효화 계층(dirty 신호 = 액션 종류)**:
    - `analyze(records)` → CFG 구조 + 빈 슬롯. [코드 편집]
    - `evaluate(debug값)` → fixpoint → CFG-with-states. [값 변경] (CFG 재사용)
    - `verify(intents)` → verdict. [intent 변경] (CFG·states 재사용). **states 종속** = 없으면 lazy evaluate.
    - 체인 종속: analyze → evaluate → verify.
15. **인터랙티브 = 3콜, 실험/격리 = 1콜(`analyze_once`)**. 3콜이 dirty 신호를 공짜로 운반(1콜은 dirty 재인코딩이라 캐시 이득 상실).
16. **두 루트**: Root A 타겟 분석 세션(`EngineSession`) / Root B 의존성(`DependencyStore`). deps는 분석의 *선행 리소스*(세션 생성 전 빌드돼야 @IReturn 해소) → peer 아님, 별 루트.
17. **suggest 분해**(입력이 다름 → 표면 분리, generator 공유): `suggest_value`(opaque 슬롯) / `reach`(특정 path 도달 입력, witness) / `cover`(전 분기, 후순위) / `suggest_intent`(주석·취약점패턴 → intent 제안, 입력=원문+주석). derivable→solver / opaque→LLM 매핑이 L2.
18. **intent_anchors 출력 drop** — @During 부착 가능 라인 = `{CFGNode.src_line}`으로 프론트가 유도.
19. **입력 모델 = delta fragment**(full-slice 아님): Monaco onChange→`(startLine,endLine,code,event)` 어댑터. `slice_solidity` record 형식과 동일 경로(update_code 증분) = SolQDebug "quick" 강점 유지. paste=multi-line record.
20. **syntax gate = ANTLR(`Solidity.g4`)로 누적 전체 코드 파스** → 실패 시 `Diagnostic` 반환. **solc 게이트 보류**(런타임 무겁고 의미검사는 분석 선결조건 아님, 과거 compile-first 실패 이력). 필요 시 별도 deep-validate 액션으로 분리.
21. **출력층 `send_report_to_front`(print만, ContractAnalyzer.py:2934) 교체 허용** → `engine_api.to_analysis_result`가 구조화 Result로 대체.
    - **(2026-06-20 정정)** `send_report_to_front`는 **CFG/risk/region을 만들지 않음** — `recorder.ledger`(라인별 텍스트 record)를 콘솔에 print할 뿐. CFG/states = `FunctionCFG.graph`, verdict = Engine 해석 중 `combined_result`. → "교체"가 아니라 **신규 뷰 직렬화기** 필요. (risk/region 출처는 §2.1 참조.)

### 부록-1.2 추가 확정 (2026-06-20 세션) — 최적화 아키텍처

22. **intent ↔ debug 게이트 = 소프트 + auto-fill**: intent는 항상 허용. debug 슬롯이 비면 `reach`/`suggest_value`로 값을 **auto-fill 제안**(witness 생성과 연결). 막지 않고 채움. **auto-fill = 값 변경 이벤트** → 일반 `evaluate`(P1)→`verify`(P2) 캐스케이드를 탐(= intent 제스처가 값 변경으로 번질 수 있음). debug 없는 의미상 하드 의존은 아님(전부 ⊤면 대부분 warning이라 무의미할 뿐).
23. **분석 3-페이즈 분해**: **P0 structure**(code→CFG 골격) / **P1 fixpoint**(code+debug값→per-node states: `variables`/`before_envs`/`return_vals`) / **P2 judge**(states+intents→verdict, **P1 출력의 순수함수**). 현재 엔진은 **P2가 P1에 용접**(해석 walk 중 인라인 검증, `Engine.py:1396+`) → 분리가 목표. 근거: `guardian_verifier.verify_*`는 이미 `cfg_node.before_envs`/`variables`만 읽음 → 루프 밖 사후 호출 가능.
24. **독립성 = 절차(snapshot/restore) → 구조(deepcopy-into-working)**: `baseline_fcfg`(코드만 의존, pristine)의 **복사본에 P1 해석** → restore 불필요(매 run이 자기 복사본 = 구조적 격리), **계산결과 보존**. 그 복사본이 곧 캐시·투영 대상. `FunctionCFG`/`ContractCFG`는 pkl 왕복 검증됨(deps가 .pkl) → deepcopy/pickle 택일은 측정으로.
25. **states 버전 캐시 = 진짜 3-tier 재사용**: `states_cache[values_ver]`. 값변경→해당 버전만 P1, 나머지 히트. **intent변경→P1 스킵, P2만**. (code,값) 반복→무계산. 라우터: 값 버전 동일→P2만 / 바뀜(auto-fill 포함)→P1→P2 / **변수 첫사용 인덱스 early-out**(바뀐 변수의 첫 사용이 모든 intent보다 뒤면 재계산 스킵, 건전).
26. **최적화 도달 = Stage 1 → 등가확인 → Stage 2** (목표는 순수 P0/P1/P2 분리):
    - **Stage 1**: deepcopy-into-working + 버전 캐시 + **standalone P2**(guardian를 보존 states에 사후 호출) + 뷰 직렬화기 + first-use 인덱스. 인라인 verify는 **지우지 않고 정답 오라클로 유지**. → 이 시점에 이미 verify-only(쌈) 경로 동작.
    - **등가 확인**: standalone P2 == 인라인 verify.
    - **Stage 2**: 같음 확인 후 인라인 verify 제거(핫패스 청소). = 새 기능 아님, 중복 제거.
    - **증분 fixpoint 보류**: 단일 함수 CFG엔 이득 작고 loop widening/narrowing 건전성 위험·공수 큼. 프로파일링서 P1 병목으로 측정되면 재검토.
27. **(14) 정정 — verify는 "싼 재조회"가 아니다(현행)**: 현재 엔진에서 intent는 노드에 attach되고 **해석 패스 중** 검증됨(`Engine.py:1396+`) → verdict·states가 같은 패스에서 생성되고 **restore로 함께 소거**(`DebugBatchManager.flush:48`). "states 재사용 + 판정만"의 싼 verify는 **Stage 2(P2 분리) 후에야** 성립. 그전엔 intent 변경도 재해석 동반.
28. **증분 편집 = B1(record-space diff) + T-stmt(논문 surgical delete/modify) + ANTLR SyntaxValid** (2026-06-22). 프론트는 풀소스 전송, 서버가 prev/new 레코드 diff 로 변경분만 영속 세션에 `update_code`. raw-Monaco-델타(B2)·T2 폐기. **검증: 엔진 delete/modify 가 논문 Alg.대로 미구현**(add-only 만 실전 검증) → 구현 대상. `closeBefore` 누락 버그는 B1 apply 경로(diff 스트림이 `update_code` 에 close_before 전달)에 흡수. 상세·6단계 계획·Alg↔API 매핑 = **§5.6**.

---

## 부록-2. 엔진 확인 결과 (코드 근거, file:line)

### 부록-2.1 program-point 상태 / CFG / loop
- per-node 상태: `CFGNode.variables`, `before_envs` (`Utils/CFG.py:47-48`). 라인↔노드: `SolidityAnalyzer.line_info[ln]["cfg_nodes"]`.
- 직렬화(interval→str): `Analyzer/RecordManager.py:292-325` (`"[lo,hi]"`, `"address(⊤)"` 등).
- **loop fixpoint 조회 핸들 = YES**: `CFGNode.fixpoint_evaluation_node`(플래그), `fixpoint_evaluation_node_vars`(`Utils/CFG.py:15,33`), `loop_exit_node`, `cond_type`. fixpoint 계산 `Interpreter/Engine.py:409`.
- **⊤ provenance 태깅 = NO**: `Domain/Interval.py`에 ⊤ 도입 출처 추적 없음. 필요시 소작업으로 추가(후순위).
- verdict: `during_annotations`/`post_annotations`(`ContractAnalyzer.py:68-69`), 검증 `Analyzer/GuardianVerificationEngine.py`, 결과 `RecordManager.ledger`.
- **(2026-06-20 확정) risk/region 출처**: 실제 검증은 `process_during`(fallback) 아니라 **해석 중** `Interpreter/Engine.py:1396+`에서 `combined_result` 생성. 매핑:
  - `VerdictItem.risk` ← `combined_result["risk_score"]` (0-10 float, `Engine.py:1418`).
  - `status` ← `"violated"/"satisfied"/"warning"` → `Violated/Satisfied/Warning` 매핑 필요.
  - `region [n,n]` = **소스 라인 스팬** `[report_line, report_line]` (값-interval 아님). `false_regions`(lower/upper violation 값구간)는 별개 도메인 → 프론트 현재 미모델(추후 확장 후보).
  - **transient**: `combined_result`는 `analysis_per_line[report_line]`에 기록되나 states와 같은 해석 패스 산물 → restore로 소거. Stage 1에서 states와 함께 freeze.

### 부록-2.2 annotation 바인딩 (삽입 없는 적용 근거)
- 라인 밀림: `SolidityAnalyzer.update_code`(`SolidityAnalyzer.py:40-106`) → `_insert_lines`(108-170) → `ContractAnalyzer._shift_cfg_meta`(92-118).
- **구조화 등록 메서드 이미 존재**(텍스트 아님): `process_during(clauses, logic_ops)`(`ContractAnalyzer.py:1748`), `process_post`(1866). 등록 키 = `current_start_line`, 노드 부착 `_cfg_node_at(line_no)`(1734)/`_find_prev_cfg_node`. @Post는 exit_node.
- **삽입 없이 등록 패턴 이미 있음**: `_is_during_inline`(`SolidityAnalyzer.py:219`)이 inline @During이면 `_insert_lines` 스킵.
- → 결론: `update_code`를 어노테이션에 호출 안 하고 텍스트만 파싱+visit하면 밀림 없음. 침습도 낮음~중간.

### 부록-2.3 debug annotation 문법/시그니처 (`Parser/Solidity.g4`)
- **⚠ 2026-07-18(#37) 이후 어노테이션 규칙에 `'//'` 가 없다** — `@During x > 0` 처럼 접두어 없이 파싱한다
  (소스 주석이 아니라 API 입력이므로). `'//'` 는 이제 일반 `LINE_COMMENT`(HIDDEN) 전용.
- `debugInput`(g4 268-294), `debugValue`(329-338).
- @StateVar/@LocalVar: `varRef '=' debugValue`. varRef는 복합경로(`config.fee[user]`, `token.decimals`) 지원.
- @GlobalVar: simple(`block.X`/`msg.X`/`tx.X` 화이트리스트) | addressBalance(`address(this).balance`).
- @IReturn: A(`cVar.f()`) | B(`IIface(addr).f()`) + accessChain(member/index/call). func은 view/pure만.
- debugValue: `[lo,hi]` | `symbolicAddress N` | `true/false/any` | `array[...]` | `arrayAddress[...]` | `symbolicBytes/String hex` | enum.
- 등록: `process_state_var_for_debug(lhs_expr, value)`(`ContractAnalyzer.py:2756`), `process_local_var_for_debug`(2782), `process_global_var_for_debug`(2710), `process_ireturn(contract_var, func_name, access_chain, value)`(2830), `process_ireturn_cast`(2876). lhs resolve = `Interpreter/Semantics/DebugInitializer.py`. snapshot/restore = `DebugBatchManager.flush`(`Analyzer/DebugUnitAnalyzer.py:33-48`) + snapman.

### 부록-2.4 dependency pkl 빌더 (이미 존재)
- `Dependencies/main.py`: `slice_solidity(source)`(.sol→records), `analyze_file(sol, mode)`(→ `ifc_/lib_/con_*.pkl` 저장, `{cfg, file_level_structs, type_aliases}`). 부모 해석 `_load_parent_pkls`. **순서 하드코딩**(`_con_order`) → 임의 첨부엔 자동 topo-sort 필요(미구현).
- 로드: `main.py:load_dependencies()` (pkl 읽기 + 3개 .sol 디렉토리 interface 이름 스캔).

---

## 부록-3. 프론트 구조 (`frontend/`)

스택: Vite + React + TS + `@monaco-editor/react`. 실행: `cd frontend && npm run dev` (포트 5173, 점유 시 5174).

```
src/
  main.tsx, App.tsx            App: view(workspace|debug) 전환 + 상단바
  types/result.ts             ★ engine_api 출력 계약(CFG/verdict/top_vars/intents/debugSlots)
  state/store.ts              ★ zustand+persist(localStorage v2). 공유상태+트리거.
                              runAnalysis(func,tier): 액션=dirty신호 → 코드편집=structure / 값=evaluate / intent=verify.
                              tier별로 results[key] 패치 후 조립. verify는 항상 마지막 1회.
  engine/analyzeClient.ts     ★ 실엔진 seam. 3콜+analyzeOnce → engine_server(FastAPI) fetch.
                              BASE=VITE_ENGINE_URL|127.0.0.1:8000. (mock 제거 완료.)
  editor/solidity.ts          Monaco Solidity 문법(Monarch)+테마 'iqdebug-dark'
  mock/
    workspace.json            컨트랙트 소스 + 함수목록 + deps
    result.withdraw.json      Vault.withdraw mock Result (CFG+verdict+slots+intents)
  workspace/
    WorkspacePanel.tsx        좌:FileExplorer+Deps / 중:Monaco / 우:FunctionList
    FileExplorer.tsx          .sol 파일 new/open(File API)/select/delete
    DepsSidebar.tsx           .sol 첨부(File API)→building→ready(pkl 빌드는 엔진 연결 시)
    FunctionList.tsx          함수별 verdict 마커(●/⚠/○) + [Debug ▷]
  debug/
    DebugView.tsx             focused 함수 디버그 레이아웃
    CodePane.tsx              Monaco(read-only) + 커서/pred/violated/bp/intent 데코
    DebuggerControls.tsx      back/fwd/into/out/continue + prev/next bp
    StateLocals.tsx           커서 노드 추상상태 + path + merge pred 표기
    VerdictPanel.tsx          S/W/V + risk + region 클릭→점프 + ⊤vars
    DebugInputPanel.tsx       ★ 슬롯-주도(그룹별) + 값만 입력(interval/bool/address)
    IntentPanel.tsx           intent 편집(@During+line / @Post) store기반
    serialize.ts              슬롯+값→g4 캐논 텍스트 (engine_api용)
    useNavigation.ts          ★ CFG 위 로컬 navigation(step/back/into/out/continue/bp)
```

### 부록-동작 vs mock
- **동작**: 두 화면 전환, 다중 파일(new/open/delete, localStorage 영속), deps 첨부(stub), Solidity 하이라이트, 함수 verdict 마커, **navigation 로컬(엔진 0)**, merge backward 양쪽 하이라이트, verdict→점프, breakpoint(gutter 토글/continue/prev/next), slot-주도 debug 입력(값만), ghost 제안 Tab수락, intent 편집+라인 마커.
- **mock/stub**: 분석 자체(Result 고정, `analyzeClient`가 seam), deps pkl 빌드, ghost 제안 내용, LLM chat 없음, step-into=forward stub, advanced value types(array/bytes/enum) 스텁.

---

## 부록-4. 다음 할 일

### 부록-완료 (2026-06-19)
- ✅ `engine_api.py` 계약 스켈레톤(타입·시그니처·docstring, 내부 `NotImplementedError`).
- ✅ 프론트 seam 3콜 배선: `analyzeClient.ts`(3콜+analyzeOnce) + `store.ts`(tier 라우팅) + `engine_api`에 `Diagnostic`/`analyze_once`.

### 부록-즉시 후보
- **(T2) Monaco delta 어댑터**: `onChange` → `(startLine,endLine,code,event)` records (decision 19). 현재 `updateActiveContent`는 전체 content를 넘김 → delta로 교체.
- **(T1) 결정론 경로 실배선 = Stage 1**(§1.2 decision 24-26): `EngineSession.analyze/evaluate/verify`.
  - **(1) deepcopy-into-working + 후크**: `DebugBatchManager.flush` restore 직전에 해석된 `fcfg`를 복사/freeze해 `states_cache[values_ver]`에 보존. 기존 snapshot/restore는 폴백 병행. 코어 변경은 flush 경로로 한정(작은 diff).
  - **(2) 뷰 직렬화기**(코어 밖, engine_api): CFGNode role 플래그→`flags`, `variables`/`before_envs`→`state`(interval→`"[lo,hi]"`, RecordManager:292-325 재사용), edge `condition`→`true/false`, join pred→`join_from`. verdict 매핑(§2.1).
  - **(3) standalone P2**: `guardian_verifier`를 보존된 states에 사후 호출 → verify-only 경로. 인라인 verify는 오라클로 유지(등가 확인용).
  - **(4) debugSlots 도출**(파라미터/상태변수/글로벌/`ireturn_registry`(opaque)) + **변수 첫사용 인덱스**(early-out용) + ANTLR 게이트→`Diagnostic`.
  - **이후 Stage 2**: standalone P2 == 인라인 등가 확인 후 인라인 verify 제거.
- **(a) @During 라인-클릭 부착**(intent 전용): 디버그뷰 라인 클릭→커서 라인 기본값+override. (현재 라인넘버 수동입력.)

### 부록-T1 끝나야 풀리는 것
- ⊤ provenance 태깅(why-⊤ 라벨), `to_analysis_result` 출력층(send_report_to_front 대체).
- websocket 배선: `analyzeClient` seam → 실제 `engine_api` 호출(현재 mock).
- Root B 의존성 빌드(slice→analyze_file→pkl) + 자동 topo-sort(임의 .sol 첨부 순서).
- 라인 anchor(워크스페이스 편집 시 @During lineRef 따라 이동 — Monaco sticky). 현재 디버그뷰 read-only라 안정적.

### 부록-생성 보조 (L2 originality, 후순위)
- `suggest_value`/`reach`/`cover`/`suggest_intent` 구현 + solver/LLM backend seam. derivable→solver / opaque→LLM 매핑 실험.

### 부록-폴리시/후순위
- Solidity 함수목록 동적 파싱(현재 mock 고정), advanced value types UI, precompute&inject(P1), intent-repair.

---

## 부록-5. `engine_api.py` (스켈레톤 작성 완료 — 내부 `NotImplementedError`)

**파일 = 프로젝트 root `engine_api.py`**. 계약(타입·시그니처)만 확정, 본문 미구현. 각 스텁 docstring에 실배선 hook이 `file:line`으로 박혀 있음.

### 부록-5.1 Root A — `EngineSession` (결정론 코어)
- `__init__(deps)` / `close()` — SolidityAnalyzer 1개 + deps 1회 로드(load_dependencies를 인스턴스 받는 형태로 신규). DebugBatchManager로 snapshot/restore.
- `analyze(records) → Structure` — delta 누적 적용 → CFG 구조 + 빈 debugSlots + `diagnostics`. nodes.state={}. [코드 편집]
- `evaluate(debug_values) → CfgWithStates` — fixpoint → states. **restore 전 스냅샷 직렬화**(불변 사본). top_vars(provenance=None). [값 변경]
- `verify(intents) → list[VerdictItem]` — states 읽어 판정. states 없으면 lazy evaluate. [intent 변경]
- `to_analysis_result(structure, states, verdict, intents) → AnalysisResultDict` — 3산출 합성(= 프론트 `AnalysisResult`). **`send_report_to_front` 대체 출력층**.
- `analyze_once(records, debug_values, intents, deps)` — 세션 없이 1회(L2 실험). 내부서 세션 만들어 analyze→evaluate→verify→to_analysis_result.

### 부록-5.2 생성 보조 (모듈 함수, solver/LLM, 표면 분리·generator 공유)
- `suggest_value(session, slot) → Suggestion` — opaque 슬롯 채우기.
- `reach(session, target) → dict[SlotKey, RangeValue]` — 특정 path/intent 도달 입력 합성(witness).
- `cover(session, scope) → list[dict]` — 전 분기 커버(reach 반복, 후순위).
- `suggest_intent(source, comments, patterns) → list[IntentItem]` — 주석·취약점패턴 → intent 제안.

### 부록-5.3 Root B — `DependencyStore`
- `build(name, source) → DepInfo` (slice_solidity → analyze_file → ifc_/lib_/con_*.pkl) / `list()` / `remove(name)`. 자동 topo-sort 미구현.

### 부록-5.4 값 모델 (frontend/src/types/result.ts와 1:1)
`Record`(code/startLine/endLine/event), `CFGNode/Edge/CFG`, `VerdictItem`, `TopVar`, `Suggestion`, `DebugSlot`, `IntentItem`, `RangeValue`(interval/bool/address/none), `Diagnostic`, 단계 산출 `Structure`/`CfgWithStates`, `Scope`, `DepSpec`/`DepInfo`.

### 부록-5.5 실배선 = Stage 1 (★ 구현·스모크 검증 완료, 2026-06-20)

**구현 완료(`engine_api.py` + 코어 3곳, `temp/smoke_engine_api.py` 로 검증):**
- `EngineSession.analyze(records)` — slice_solidity record → `update_code`+visit 누적 → CFG 구조 직렬화 + `LocalVar` 슬롯 도출. ✅ (flags/cond_type/edge true·false/entry·exit·return·error 정확, state={}).
- `EngineSession.evaluate(debug_values)` — 값→캐논 어노테이션(삽입 없이 컨텍스트 수동설정)→`batch.flush`→**freeze**→states 직렬화. ✅ (seed 전파·dead branch ⊥ 확인: `a=[0,5]`→`b=[1,6]`, `a>10` 분기 `[None,None]`=⊥).
- `EngineSession.verify(intents)` — intent 를 노드에 attach(`_attach_intents`, 삽입 없이)→`_interpret` 재해석→inline 검증이 `analysis_per_line` 에 기록→`_extract_verdict`→`VerdictItem`. ✅ (Satisfied/Violated/Warning·risk·복합 `&&`/`||` 검증. **inline verify = Stage 1**, standalone P2 는 Stage 2). `evaluate`/`verify` 는 공통 `_interpret` 공유(현재 둘 다 재해석).
- 뷰 직렬화기 `_serialize_cfg`/`_serialize_node_state`(RecordManager._flatten_var 재사용)/`_node_flags`/`_collect_top_vars`/`_derive_debug_slots`/`_extract_verdict`/`_cfg_to_dict`.
- `to_analysis_result` + `analyze_once` ✅ — 3산출 합성 → 프론트 `AnalysisResult`(키 6개, edge `from_`→`from`). (= send_report_to_front 대체 출력층.)
- **deps 선별 로딩** `_load_dependencies_into`/`_find_dep_pkl` ✅ — DepSpec 이름으로 objectfile pkl 매칭 로드(전부 로드 시 버전 충돌 회피). fail-loud(pkl 없으면 raise). topo-sort 미구현.
- **Stage 2 standalone P2** `_verify_standalone` ✅ — 보존 states(frozen)에 `_process_node_intents`/`_process_post_annotations` 사후 호출 → **재해석 없이** verdict. intent 는 live fcfg 에 파싱 attach 후 node 이름으로 frozen 미러링. **inline 과 등가 검증 5/5 OK**(결정 26 오라클). verify 는 캐시 있으면 standalone, 없으면 inline 재해석 fallback. → **엔진의 inline verify 는 제거하지 않음**(배치 경로 보존) — 우리 경로만 standalone 사용 = Stage 2 효과 달성.

**검증 중 발견·수정한 코어 버그 2건:**
- **`_combine_logic_results`(Engine.py:1987~) — `warning` 누락**: `&&`/`||` 조합이 `warning` 을 분기에 안 넣어 `warning && satisfied`→`satisfied`(증발), `warning || …`→`violated`(둔갑). 단일 절은 정상, 복합만 오류. → warning 우선순위 추가(`&&`: violated>warning>…, `||`: satisfied>warning>…). IntentChecker 는 `&&`/`||` 미사용이라 RQ1 무영향(사용자 확인).
- **status 어휘 혼용**: 단일 절(`_verify_during_clause_dynamic`)=`success`/`violation`, 복합(`_combine_logic_results`)=`satisfied`/`violated`. `engine_api._VSTATUS` 가 둘 다 정규화(우리 레이어, 코어 무수정).

**★ 핵심 정정 — states-wipe 의 진짜 위치 = `Interpreter/Engine.py:769`:**
- `interpret_function_cfg_for_debug` 가 worklist 로 `blk.variables` 에 seed된 states 계산(verification·ledger 기록은 여기서) → **함수 끝에서 `_saved_node_vars` 로 baseline 복원**(`Engine.py:769-770`). `DebugBatchManager.flush` 의 restore 는 그보다 바깥.
- 따라서 freeze 는 **`Engine.py:769` 복원 직전**이어야 함(거기서만 계산된 states 가 산다). flush 시점 freeze 는 이미 baseline → 폐기.
- **코어 변경 = `Interpreter/Engine.py` 한 곳뿐**(게이트 `an._capture_debug_fcfg`, 기본 미설정 → 게재 경로 무영향): 복원 직전 `an._frozen_debug_fcfg = pickle.loads(pickle.dumps(fcfg))`. pickle 실패는 그대로 전파(fail-loud).
- `EngineSession` 은 `_capture_debug_fcfg=True` 설정, flush 후 `ca._frozen_debug_fcfg` 직렬화. (한때 둔 `DebugUnitAnalyzer.freeze_enabled`/`ContractAnalyzer._last_debug_fcfg` 는 죽은 코드라 되돌림.)

**fallback 정책 = 전부 제거, fail-loud (2026-06-20):** evaluate freeze None→`raise`, 직렬화·드라이버 에러는 try/except 없이 전파. 조용한 baseline/`str()` 대체 없음. (유저 코드 syntax 검증은 추후 ANTLR 게이트로 *의도된* diagnostics 산출 — 버그 숨기는 catch-all 과 구분.)

**프론트 배선 (2026-06-20, HTTP 서버):**
- `engine_server.py` ✅ — FastAPI(+uvicorn, 포트 8000, CORS). 세션 키 "contract.func" 영속으로 3콜 묶음: `/analyze`(fresh 세션 재빌드+캐시 리셋) / `/evaluate`(재사용→states) / `/verify`(보존 states standalone P2) / `/analyze_once` / `/health`. full source→`slice_solidity`→records. 실행 `.venv\Scripts\python.exe engine_server.py`.
- `frontend/src/engine/analyzeClient.ts` ✅ — mock 제거, 실 서버 `fetch`로 교체(`BASE`=`VITE_ENGINE_URL`|`127.0.0.1:8000`). 함수 시그니처/인터페이스 동일 → `store.ts` 무변경. `npx tsc --noEmit` 통과.
- 검증: 단순 컨트랙트(`C.f`) 3콜 end-to-end OK(analyze slots/nodes, evaluate `b=[1,6]`, verify Satisfied). `temp/test_http.py`.

**★ 알려진 버그:**
- ✅ **(해결 2026-06-22) `closeBefore` 미전달 → if/else 분석 실패**: `EngineSession._apply_code_record` 가 `update_code(s,e,code,ev)` 4-인자만 호출. `slice_solidity` 는 `} else {`/`} while`/`} catch` 에 `closeBefore=True` 를 붙이는데(main.py `simulate_inputs` 는 `update_code(s,e,code,ev,close_before)` 전달) 이게 누락 → `Vault.withdraw`(if/else) analyze 시 `DynamicCFGBuilder.build_else_statement`: "else: target join not found" 500. **수정**: `_apply_code_record` 가 record 의 `closeBefore` 를 받아 `update_code` 에 전달. (Record TypedDict 에 `closeBefore: bool` 추가.)
- ✅ **(해결 2026-06-22) StateVar/GlobalVar 슬롯 미도출**: `_derive_debug_slots` 가 LocalVar(파라미터)·
  IReturn 만 도출했음 → **StateVar**(`ccf.state_variable_node.variables`; 스칼라=interval, 매핑·구조체·배열
  =opaque) + **GlobalVar**(`ccf.globals` 중 *함수 소스에 실제 등장한 것만* — 화이트리스트 15개 전부면 노이즈)
  추가. `structure()` 가 ccf·소스텍스트 전달. 검증: Vault.withdraw → amount(Local)/balances(State,opaque)/
  total(State)/msg.sender(Global) 정확 표시.
- **@IReturn 슬롯**: 여전히 `ireturn_registry`(값 적용 후 채워짐)만 봄 → 인터페이스 *호출 지점* 구조적 도출 잔여.
- **프론트 레이아웃 개편(2026-06-22)**: intent 가 4-컬럼 맨 끝(`max-height:110px`)이라 안 보이던 문제 →
  **상단 가로 툴바(컨트롤) + 코드 좌(크게) + 우측 세로 3분할: Intent+Verdict(상단)/Debug Input(중)/State&Locals
  (하단)**. `DebugView.tsx`·`styles.css`(`.dtoolbar`/`.dright`). tsc 통과.

**잔여(우선순위 순):**
1. **`closeBefore` 수정** → `Vault.withdraw`(if/else) 등 실컨트랙트 통과. 그 후 브라우저에서 실동작 확인(`engine_server` + `npm run dev`).
2. **@IReturn 슬롯 도출**(인터페이스 호출 지점) + deps 선별로딩 연결(프론트 첨부 deps → 서버 DepSpec).
3. `[None,None]`→`⊥` 표기, verdict message 정리(`<Expression object>` repr 노이즈), node 이름 verdict 부착.
4. before_envs(라인 단위 nav), ANTLR diagnostics(누적 코드 파스), deps topo-sort.
5. 생성 보조(L2): `suggest_value`/`reach`/`cover`/`suggest_intent` + solver/LLM seam. `DependencyStore.build`(slice→pkl).
6. 동시성: 서버 세션 잠금(현재 단일 사용자 전제), delta 어댑터(T2, Monaco onChange→record).
- **뷰 직렬화기**(engine_api): CFGNode 플래그→`flags`, `variables`/`before_envs`→`state`(interval→`"[lo,hi]"`, RecordManager:292-325 부품 재사용), edge `condition`(`graph.edges[u,v]`)→`true/false`, `get_true/false_block`. `serialize_graph_structure`(CFG.py:211)·`CFGSerializer._json_safe` 부품화하되 before_envs·interval-str·flag 매핑은 신규.
- **verdict**: `Engine.py:1418` `combined_result["risk_score"]`/status/message → `VerdictItem` (§2.1 매핑).
- **debugSlots**: `FunctionCFG.parameters`/state vars/globals/`ireturn_registry`(opaque) 스캔. **변수 첫사용 인덱스**(early-out)도 P1 walk서 수집.
- **ANTLR 게이트**: 누적 전체 코드 파스 → error listener → `Diagnostic`.

**침습 범위 (2026-06-20 갱신, 코어 수정 승인됨)**:
- **수정 대상**: `DebugBatchManager.flush`(freeze 후크, Stage 1), `Interpreter/Engine.py`(인라인 verify 제거 = Stage 2), 출력층.
- **불변 유지**: `Domain/`, `Utils/CFG.py`(구조), `GuardianVerificationEngine.py`(P2 로직 그대로 사후 호출), 파서·visitor, fixpoint 핵심 로직(`Engine.py`는 P2 분리만, P1 계산 불변).
- 회귀 가드: `run_all.py` 20/20 VIOLATED 매 단계 확인.

### 부록-5.6 증분 편집 아키텍처 (B1 + T-stmt, 2026-06-22 결정)

**배경 / 모순**: 현 `engine_server`는 매 호출 풀소스를 받아 fresh 세션으로 `slice_solidity` 전체를 add 재적용
(= 매번 통째 재구성). SolQDebug 의 핵심 주장인 *edit-time 증분 분석*을 살리지 못함.

**결정 (B1, record-space diff)**: 에디터는 풀소스를 보내고(와이어 단순), **서버가 prev/new 레코드를 diff**해
변경분만 영속 세션의 `update_code` 로 적용. Monaco 라인 좌표가 아니라 **slice-정규화된 레코드 시퀀스에서 diff**
하므로 좌표매핑·closeBefore·`}`스트립 재구현 불필요(slice 가 단일 진실원). chunk paste = "삽입 레코드 묶음"으로
검증된 add 경로를 그대로 탐 → 큰 코드일수록 B1 이 더 안전(문맥-정합 정규화). 필요시 Monaco 변경범위 힌트로
diff 를 O(변경) 한정(선택).
- 반려: **B2**(프론트가 raw Monaco 델타 전송). 물리라인↔정규화공간 불일치 + slice 의 TS 포팅(영구 동기화 부채)
  + auto-brace 로 생기는 단독 `}`(slice 가 버리는 줄) 처리 → 큰 청크에서 가장 위험. published 계약(=slice 레코드)
  에서도 더 멀어짐.

**검증 결과 (2026-06-22)**: **논문 Code Deletion/Modification 알고리즘이 엔진에 미구현**.
`update_code` delete 분기(`SolidityAnalyzer.py:83-98`)는 소스라인/`line_info`/`ledger` pop + `_shift_cfg_meta`
(src_line·ledger 시프트만) 뿐 — 논문의 `RemoveNodes`/`AddEdge(pred→succ)` 재연결/`succ` 재해석 **없음**.
다른 "delete"는 무관: `ContractAnalyzer.py:2736`=디버그값 롤백, `handle_delete`(1595)=Solidity `delete` 연산자,
`DynamicCFGBuilder` `remove_node`(55/309/386)=빌드 중 stub 정리. **미발각 이유**: slice 가 전부 `add` 라
main.py·engine_server 어디서도 delete/modify 를 호출 안 함 → add-only 만 실전 검증됨.

**T-stmt 채택** (statement 단위 surgical delete/modify 를 논문 Alg.대로 구현). 대안 T-func(바뀐 함수만 drop+add
재구성)는 더 안전하나 사용자가 논문 충실도/효율 위해 T-stmt 선택.

**Alg. → 엔진 API 매핑**:
- `M`(line→node) = `sa.line_info[ln]["cfg_nodes"]` + `CFGNode.src_line` + `recorder.ledger[ln]`
- `RemoveNodes`/`RemoveEdge`/`AddEdge` = `nx.DiGraph` 원시연산(`G.remove_node` 엣지자동제거 등)
- `IncrementalInterpretation(G,{succ})` = **기존** `engine.reinterpret_from(fcfg, seeds)`(Engine.py:1013)
- `ShiftIndex` = **기존** `_shift_cfg_meta`
- `SyntaxValid(C')` = **ANTLR 로 살림**(결정). delete 전 누적 `C'` 파스 검증, 실패 시 reject.
- 새로 짤 핵심 = **FindBoundary + 노드집합 제거 + pred→succ 재연결**.

**어려운 지점 (논문 Alg.은 이상화)**: 단순 statement 삭제는 그대로 OK. **블록 삭제**(if/else)는 cond+양분기+
**join** 서브그래프 수집, succ=join 의 successor. **루프 삭제**는 cond+body+back-edge+`loop_exit_node`. 중첩은
재귀 수집. SyntaxValid 가 1단계인 이유 = 문법유효 삭제라야 `M[Ls..Le]`가 제거대상과 정확히 일치.

**구현 순서 (위험 분리, 각 단계 독립 테스트)**:
1. ✅ **record-space diff 레이어** (`incremental.py`, **코어 무관·순수 Python**, 2026-06-22 완료):
   `diff_records(prev, new)` → `difflib` 로 레코드 `code` 시퀀스 정렬 → 엔진 좌표 `EditOp(event,start,end,
   code,close_before)` 스트림. equal=커서전진, insert=add, delete=delete, replace=1:1동일span→modify/그외
   delete+add. closeBefore 보존. `temp/test_diff_records.py` 16/16 통과(값변경=modify, 삽입/삭제, if/else
   closeBefore 전달, chunk 삽입/덮어쓰기, 무변경).
2. **엔진 Code Deletion** (`DynamicCFGBuilder.delete_cfg_range`, 2026-06-22):
   - **통일 SESE 알고리즘**: `node0=M[Ls]` → (entry,exit) 식별(루프=`find_loop_join`/`loop_exit`,
     if=node0/`_nearest_join`, 그외=단일노드) → `region = descendants(entry) ∩ ancestors(exit) ∪
     {entry,exit}` → 공유 sink 필터해 pred→succ 재연결 → region 제거 → `reinterpret_from(succ)`.
     **skeleton(t_blk/f_blk/while_join/body)·중첩·back-edge 자동 포함**(M 의존 X). join 은 구조로 탐색
     (M[Le]≠join, `}`줄에 등록되므로). 근거: `temp/inspect_cfg.py` 로 if/while ground-truth 확인.
   - ✅ **2a(simple)·2b(if/while) 완료**: `temp/test_delete_cfg.py` 7/7 — 삭제+evaluate == reduced
     소스 fresh 빌드(b fingerprint·node수 등가), reinterpret=True 무예외.
   - ✅ **2c-else 완료** (`_delete_else_clause`, kind="else"): else 줄 line_info=[if_join, else_block]
     이라 generic 이 join 을 지우는 문제 → 전용 처리. **false-분기 body 만 제거하고 빈 else_block→join 유지**
     (cond/join 보존, plain if 化). `temp/test_delete_else.py` 3/3(else 삭제 == plain if fresh, nodes·b 등가).
   - ✅ **2c-else-if 완료** (`_delete_else_if_clause`, kind="else if"): else-if cond+true-body+local_join
     제거 → false-child 를 위 cond 의 false 로 reparent, else 꼬리를 outer join 으로(체인 일반화).
     `temp/test_delete_2c.py` — else-if 삭제 == if/else fresh(nodes·b 등가).
   - ✅ **2c-lone-early-exit 완료** (`_delete_early_exit`, kind="return"/"revert"): true 분기는 sink 로 가
     join 미도달 → `_continuation_via_other_branch`(다른 분기 타고 첫 join/loop_exit) 로 fall-through 복원,
     `_walk_up_to_cond` 로 enclosing cond 탐색. lone-return 삭제 == 빈 then fresh(등가).
   - ☐ **2c 잔여(통삭제 전제)**: try-catch·do-while 은 **통째 삭제만** 지원(try-only/do-only 는 SyntaxValid
     거부 or 비의도) → step 5 배선에서 generic SESE + 미처리 시 함수 rebuild safety-net. break/continue 희소.
   - ★ **선행 blocker 수정**: `closeBefore` 미전달 버그(§5.5 known-bug #1) 해결 — `_apply_code_record`/
     `analyze` 가 `rec.get("closeBefore")` → `update_code(close_before)` 전달, `Record` 에 옵셔널 필드 추가.
     이게 없으면 else/do-while/catch 가 "else: target join not found" 로 빌드 실패(2c 의 전제).
3. ✅ **엔진 Code Modification** (2026-06-22): 두 결 — **단순 statement 값 변경**=delete+add(검증된 경로),
   **조건 헤더(if/while/for) 변경**=in-place(body 보존 필수, delete+add 불가). in-place = `DynamicCFGBuilder.
   modify_condition_expr`(cond 노드 condition_expr 만 교체) + `process_if/while/for_statement` 에 modify 분기
   (`current_edit_event=="modify"` → 새 빌드 대신 swap, return). `_is_condition_header` 로 라우팅. 검증:
   조건 modify == fresh(if `temp/test_update_block.py`, while 등가 확인).
4. ✅ **ANTLR SyntaxValid 게이트** (`incremental.syntax_valid`, 2026-06-22): 새 풀소스를 `sourceUnit`(EOF)
   로 파스 → 오류 수집. invalid 면 edit 거부(이전 valid CFG 유지) → **orphan-else/do-while·brace 불일치가
   CFG 수술에 도달 못 하게 원천 차단**(안전 전제). `temp/test_syntax_valid.py` 6/6(plain/if-else/do-while
   허용, orphan else/brace/orphan do-while 거부). **순서 정정**: 안전 전제라 2c·modify 보다 먼저 구현함.
   (적용 경로 배선은 5단계.)
5. ✅ **B1 배선** (`EngineSession.update`, `engine_server`, 2026-06-22): 영속 세션 + `update(new_source)`
   = SyntaxValid 게이트 → `diff_records` → 적용 순서 **modify(무이동)→delete(아래부터)→add(위부터)** 로 라인
   좌표 정합 유지(좌표=레코드 실제 startLine, delete=prev/add=new 공간). delete=`delete_cfg_range`(CFG)+
   `update_code(delete)`(소스 장부). `structure()` 추출(analyze/update 공용). `engine_server /analyze` 가
   첫 호출=fresh, 이후=증분 `update`. 검증: `temp/test_update*.py`·`test_session_flow.py` 통과 — 단순
   add/delete/value-modify, if-block add/delete, 조건 modify, **end-to-end analyze→update→evaluate→verify**
   전부 fresh 빌드와 등가, invalid 거부.
6. ✅ **프론트** (무변경): 프론트는 풀소스만 전송(`store.ts` `source: activeContent`) → 서버가 diff →
   **프론트 코드 변경 없이 B1 증분 적용**. (diagnostics squiggle·range 힌트는 선택 폴리시.)

**✅ rebuild safety-net (2026-06-22)**: `update()` 가 surgical 미지원 구조(try-catch/do-while/catch/
break/continue = `_is_risky_op`)면 `_full_rebuild`(새 분석기+records 전체 add-only 재적용)로, 그 외는
surgical 시도하되 **예외 시 rebuild 폴백** → **모든 valid 편집이 항상 정확**(corruption 불가). `temp/
test_safety_net.py` 6/6(do-while add/delete, break 삭제 == fresh). ② try/do-while/break/continue 해결.

**잔여(증분)**: ① reinterpret=True 증분 재해석(현재 evaluate full 재해석 — 정확하나 증분 이득 미실현;
**decision #26 보류와 일치** — 단일함수 fixpoint 이득 작고 widening 건전성 위험) ④ 다중-영역 동시편집
(현재 단일/연속 영역 검증; 비연속 다중편집은 미검증).

**✅ 출력 cleanup (2026-06-22)**: `Domain/IR.py Expression.__str__` 추가(재귀 렌더) → verdict 메시지가
`b > <Expression object at 0x..>` → `b > 0`(의미론 불변, display만). `_serialize_node_state` 가
`[None,None]`(bottom) → `⊥` 가독화(우리 레이어).

### 부록-5.7 현재 상태 / 다음 후보 (handoff, 2026-06-22)

**동작하는 것 (테스트 67개 통과)**: B1 증분 편집 end-to-end.
- 편집: statement add/delete/value-modify, if-block add/delete, 조건 modify(in-place), if/else·else-if·
  lone-return 삭제 — 전부 fresh 빌드와 노드·states 등가. SyntaxValid 게이트로 invalid 거부.
- 안전망: try-catch/do-while/break/continue/미처리 → full-rebuild 폴백 → 모든 valid 편집 정확.
- 슬롯: Local(파라미터)/State(스칼라 interval·매핑 opaque)/Global(사용된 것만). verdict 텍스트·⊥ 정리.
- 서버: `/analyze` 첫 호출 fresh·이후 증분. 프론트 무변경(풀소스 전송). 레이아웃 = 상단 툴바+코드 좌+
  우측 세로3분할(Intent상단/Input/State).

**코어 변경 총괄(최소 침습)**: `DynamicCFGBuilder`(delete_cfg_range+else/else-if/early-exit/
modify_condition_expr 메서드 추가, 기존 불변) · `ContractAnalyzer`(process_if/while/for 에 modify 분기 3개) ·
`Engine.py`(freeze 게이트 + warning-combine, 기존) · `Domain/IR.py`(Expression.__str__ display) ·
`engine_api`(update/structure/_full_rebuild/슬롯/closeBefore) · `incremental.py`(diff/syntax_valid/kind, 신규).

**의도적 보류 (이유)**:
- **@IReturn 슬롯**: 키가 인터페이스 호출 패턴(`cVar.f()`/`Iface(addr).f()`)에서 *인터프리트 중* 형성
  (Evaluation.py) → analyze 시점 빈 상태. 구조적 도출 = 호출 AST 스캔+타입해석 신규 필요 → **DeFi 시나리오
  실구동 시** 착수(투기적 인프라 회피).
- **증분 reinterpret(reinterpret=True)**: decision #26 보류와 일치 — 단일함수 fixpoint 이득 작고 widening
  건전성 위험. 현재 evaluate full 재해석이 정확.
- **매핑 StateVar 값 시딩**(balances[addr]): 프론트+백 access-chain 계약 확장 필요(표시는 됨, seed 별도).
- **다중(비연속)-영역 동시편집**: 단일/연속 영역만 검증.

**다음 후보**: ① 브라우저 실동작 확인(서버 재시작 `.venv\Scripts\python.exe engine_server.py` + `npm run
dev`) ② @IReturn 호출 AST 스캔 ③ 매핑 시딩 access-chain ④ diagnostics→Monaco squiggle.

**테스트 파일**(`temp/`): test_diff_records / test_syntax_valid / test_delete_cfg / test_delete_else /
test_delete_2c / test_update / test_update_block / test_session_flow / test_safety_net. inspect_cfg{,2,3}.py 는
구조 관찰용.

### 부록-5.8 디버그 입력 UX 세션 (2026-07-03) — 슬롯 정합·값 입력·시딩 버그

브라우저 실동작 확인 중 디버그 입력 경로에서 다수 버그 발견. 백엔드는 대체로 정상, **슬롯 도출·값 표시·다중
시딩·라인 좌표**에서 문제. 아래 ✅=수정완료(검증), 🔧=원인규명·수정방안 확정(미적용), ☐=미해결.

**✅ 수정 완료 (HTTP 검증):**
1. **① 슬롯 함수-스코프 필터** (`engine_api._derive_debug_slots`/`structure`): 이전엔 함수와 무관한 상태변수
   *전부* + 컨트랙트 어디서든 쓰인 글로벌이 떴음. → **StateVar = 이 함수가 참조하는 것만**(함수 소스 슬라이스
   `[min,max] src_line` 단어매치), **GlobalVar = `GlobalVariable.usage_sites`(func 추적) 기반**. `func_src`
   비면 필터 스킵(안전). 검증: deposit → `amt`(Local)/`balance`(State)만, fee/token/owner 제외.
2. **② valueKind 변수 타입 반영** (`_valuekind_of`, SolType 기반): 이전엔 전부 `interval` 하드코딩 → msg.sender
   가 interval 로 떴음. → `typeInfo(SolType)` 로 address/bool/interval/opaque 판별. **DebugInputPanel 이 이미
   valueKind 별 위젯 분기**(address→symbolicAddress 칸, bool→드롭다운)라 ②만 고치면 자동 정렬. 슬롯에
   `signed`/`bits` 필드 추가(Fix B 용).
3. **Fix B — 무제약(unbounded) interval 입력** (`_debug_value_text`): 빈 경계 = 그 방향 무제약, 백엔드가
   슬롯 타입 min/max 로 치환(`[5,]`=≥5=`[5,2²⁵⁶-1]`, uint 정확). 양쪽 빈칸=미입력 스킵(⊤). → 반쯤 친 값의
   500 crash 원천 소멸. (g4 문법 무변경 — 프론트 빈칸 허용 + 백엔드 치환.)
4. **Fix A — 잘못된 값 = crash 대신 구조화 에러**: `int('')` 등 → 500 대신 `Diagnostic` 수집
   (`_last_value_errors` → `CfgWithStates.diagnostics` → 서버 `/evaluate` 반환). 프론트 `runAnalysis`
   **try/finally 로 `analyzing` 항상 해제**(과거 500 시 멈춤 버그) + `analyzeError` 배너 + `valueErrors`
   슬롯 위 표시.
5. **★ debugInputs 키 불일치 (진짜 "값 입력 안됨" 원인)**: 쓰기(`setDebugValue`)·엔진전송(`runAnalysis`)은
   맨 func명("deposit"), 표시(`DebugInputPanel`)·시드는 `keyOf`("Vault.deposit") → 타이핑 값이 딴 키에
   저장돼 입력칸에 안 뜸. → **전부 `keyOf(contract,func)` 로 통일**(시드 형식이 정본).

   코어 무수정. 변경 파일: `engine_api.py`(슬롯 도출·값 치환·에러) · `engine_server.py`(/evaluate diagnostics) ·
   `frontend`(store try/finally·키통일·에러state, DebugInputPanel valueErrors·힌트, DebugView 배너,
   styles.css). tsc 통과.

**✅ 추가 수정완료 (2026-07-03 이어서):**
6. **★ 다중 슬롯 시딩 충돌 (핵심 버그) — 해결·검증**: balance=[10,20]+amt=[100,200] → 기대 [110,220]인데
   **[100,200]** 이던 문제(balance 시드 유실). 규명: `DebugBatchManager._lines` 가 **startLine 키 `dict`**
   인데 `_queue_debug_values` 가 모든 디렉티브를 `add_line(...,0,0)` → **전부 key 0 충돌, 마지막만 생존**.
   원인 = 옛 모델(annotation 을 코드에 삽입 → 라인 밀림)의 라인키 잔재; 삽입-없는 모델(decision #8)에선 무의미.
   - **적용(decision #29)**: `_lines` `dict[int]` → **`list[str]`**(순서 보존, 라인키 제거, DebugUnitAnalyzer.py
     재작성). `add(code)` 신설 + `add_line(code,s=0,e=0)` 하위호환 alias(append). `modify_line`/`delete_line`
     제거. **snapshot/restore 유지**(flush `finally: restore_from_snap` = evaluate 독립성). engine_api
     `_queue_debug_values` → `self._batch.add(...)`. `main.py` 디버그주석 분기도 add 누적으로 정리(modify/delete
     라인선택 폐기, 회귀 무관 — 사용자 허용).
   - **검증(HTTP)**: Vault.deposit, balance=[10,20]+amt=[100,200] → `Assign_5`·`EXIT` 노드에서
     `balance=[110,220]`, `amt=[100,200]`, diagnostics 없음. 충돌 소멸 확정.

**✅ 추가 수정완료 (7번 — 라인 좌표계, 2026-07-06):**
7. **★ #3 라인 좌표계 불일치 (표시 안 됨의 핵심) — 해결**: 엔진 `CFGNode.src_line` 은 **컨트랙트-절대 라인**
   (`balance=balance+amt` = 25)인데 CodePane 이 **함수-로컬 소스**(`fnMeta.source`, 그 문장=라인 2)를 표시 →
   데코/네비/State&Locals/intent glyph 전부 어긋나 값이 계산돼도 안 보임.
   - **결정 (a) 채택**: 프론트를 **절대라인 = 전체 컨트랙트 소스 표시**로 통일. 데코/네비/breakpoint/intent 가
     이미 절대 src_line 을 쓰므로 **소스만 전체로 교체하면 오프셋 코드 없이 자동 정합**((b) 매핑층 불필요).
   - **적용**: `DebugView.tsx` = CodePane 에 `fullSource`(활성파일 전체) + `focusLine`(함수 startLine) 전달.
     `CodePane.tsx` = `focusLine` prop → 마운트/함수전환 시 `revealLineInCenter` 스크롤.
     `mock/result.withdraw.json` = 시딩 intent `line 9`(구 로컬) → **21**(절대). tsc 통과.
   - **검증**: 엔진이 전체 mock 소스로 `Assign_25 src_line=25 balance=[110,220]` emit 확인 → Monaco 25번 줄
     (=`balance=balance+amt;`)에 정확 정합. **육안(브라우저) 확인은 미완**(Monaco 헤드리스 불가).
   - 잔재: `result.withdraw.json` 의 cfg/verdict 는 구 로컬좌표(src_line 1~10)지만 **표시 미사용**(store 는
     `intents` 만 읽음) → 방치. 오프라인 mock 표시 되살리면 전체 변환 필요.

**✅ 추가 구현완료 (10번 — Apply 버튼, 2026-07-06):**
10. **디버그 입력 "적용" 버튼 + 결과 표시** (decision #32) — 구현:
    - `store.ts`: `setDebugValue` 에서 자동 evaluate 제거(값 변경=상태만). `applyDebug(func)` 액션 신설
      (runAnalysis evaluate). `applyTick` 카운터 추가 → evaluate 실제 실행 시에만 +1(intent-only verify 는
      커서 안 옮김).
    - `DebugInputPanel.tsx`: `▶ 적용 (Enter)` 버튼(`analyzing` 시 비활성·"적용 중…"), 패널 `onKeyDown` Enter →
      apply. `styles.css` `.apply-btn`.
    - `useNavigation.ts`: `applyTick` 변화 시 커서 재배치 = **run-to-breakpoint** (entry→비-⊥ successor 따라
      전진, 첫 breakpoint 라인에서 정지; 없으면 **EXIT** 최종상태). → State&Locals 가 변수별 결과 즉시 표시.
    - `StateLocals.tsx`: 전역(block./msg./tx.) `<details>` 접기(기본 접힘)로 실변수 결과 강조. path 음수라인 필터.
    - tsc 통과. **브라우저 육안 확인 미완**.

**✅ 추가 수정완료 (자동계산 제거 + 함수-only 뷰, 2026-07-06 이어서):**
11. **자동 계산 버그 제거 (구조/값 분리)**: 브라우저에서 "디버그 진입만 해도 값이 자동 입력·계산됨" 보고.
    - 원인 (A) 값 자동입력 = mock seed(`Vault.withdraw` a/token) + zustand persist 가 `debugInputs` 를
      localStorage 저장 → 이전 세션 값 복원. (B) 자동계산 = tier 로직 `needEvaluate = needStructure || …` 라
      **structure(편집·디버그진입)가 evaluate 까지 발사**. Apply 버튼 무색.
    - 수정: `store.ts` `needEvaluate = tier==='evaluate'` (structure 는 CFG 만), `needVerify = evaluate||verify`
      로 verify 게이트. **openDebug/setActiveFunction = 캐시 있으면 재빌드 안 함(fetch), 없을 때만 1회 빌드**
      (사용자 방향: "workspace 편집 때 CFG 만들고 debug 는 가져오기" — updateActiveContent 가 이미 편집 시
      structure 빌드 중. per-func 세션이라 미방문 함수 첫 진입만 증분 빌드). mock 값 seed 제거 → 빈 칸 시작.
      persist 에서 `debugInputs` 제외 + version 3 migrate 로 잔재 클리어(what-if 값 세션성).
12. **함수-only 뷰 + 컨트랙트-절대 라인넘버 (#7 재조정)**: 전체 컨트랙트 대신 **해당 함수만** 표시하되 gutter
    라인넘버는 절대. `CodePane` = `startLine` prop, `offset=startLine-1`, `toLocal(abs)=abs-offset`,
    Monaco `lineNumbers:(n)=>n+offset`, 데코/breakpoint/intent 를 절대↔내부 변환(함수범위 밖 스킵). breakpoint
    클릭은 내부→절대 저장. `DebugView` = `fnMeta.source`+`startLine` 전달. 검증(수식): deposit offset=23,
    src_line25→내부2줄 정합. **(a)에서 없앴던 오프셋을 헬퍼 2개로 중앙화** — 손댈 곳 CodePane 1파일로 한정.
13. **breakpoint pre-state 표시 (실행 후 값 보이던 버그 · 단일선행)**: 브라우저에서 `balance=balance+amt;`
    에 breakpoint 찍으면 **실행 후**(`[110,220]`)가 보임. 원인 = 엔진 `CFGNode.state` 는 **out-state**(실행 후)
    라 커서 노드 state 를 그대로 표시하면 pre-state 가 아님. **사용자 통찰**: 새 in-state 필드 불필요 — "이 줄
    실행 직전" = **선행(predecessor) 노드의 out-state**. 구현(`StateLocals.tsx`): 커서 노드의 (단일)선행
    out-state 를 표시. 선행이 ENTRY(빈 상태)면 = **함수 첫 문장** → 노드 out-state 에서 시드된 진입값만 override
    하는 폴백(미시드/전역은 불변이라 out=in). EXIT 은 최종상태 그대로. note 라벨("이 줄 실행 전"/"진입 상태"/
    "최종 상태") 표시. tsc 통과.

**☐ 미해결:**
8·9. **compound 슬롯 + @IReturn 슬롯 = 통합 "접근-경로 스캐너" ✅ 구현·e2e 검증 완료** (decision #34):
    `engine_api._derive_debug_slots` 에 통합 스캐너(`_ap_*`) 추가 — node.statements AST 정적 순회로
    compound(mapping/struct/array) 경로 + 인터페이스 호출(IReturn)을 도출. compound root 상태변수는 opaque
    통짜 슬롯 대신 경로 슬롯으로 대체, 인터페이스 핸들 변수는 IReturn 이 대체(스칼라 방출 스킵).
    **e2e(.venv 실측)**: `_balances[u]`(mapping→interval) / `accountBorrows[u].principal`(mapping→struct→member
    →interval, **leaf 해소 성공**) / `token.balanceOf()`(IReturn, 인자 드롭·opaque). 스칼라 `total`·파라미터 유지.
    **잔여**: ① compound **값 evaluate** 경로의 엔진 버그 2개(아래 #34 b1/b2) ② IReturn leaf 가 address 반환일 때
    valueKind 세분(현재 interval 기본) ③ LocalVar-compound(로컬이 mapping/struct, RQ1 부재) 미처리 ④ 브라우저 육안.
14. **breakpoint pre-state — 조인(다중 선행) 지점 (decision #33) — 해결(A: 프론트-only), try/catch 잔여**:
    위 13 은 단일 선행만 정확했고, 조인 지점 표시가 문제였다. **엔진 구조 확인으로 프론트-only 해결 확정**:
    - **엔진 사실(확인)**: `ContractAnalyzer.process_if_statement` 이 **join 을 즉시 생성**(`if_join`,
      `join_point_node=True`) 후 body 채워지면 `engine.reinterpret_from(join)` → join out-state = **sound merge**.
      else-if/else 는 같은 join 재사용. join 의 `src_line = end_line`(= `}` 라인). 루프는
      `build_while/for_statement` 이 `loop_exit_node`(src_line=end_line) 생성, do-while 은 `fixpoint_evaluation_node`(φ).
      루프 멤버십 = `is_loop_body`(부모→자식 전파). **이 모두 프론트로 직렬화됨**(`engine_api._node_flags`:
      flag `merge`/`loop_exit`/`fixpoint_evaluation`/`loop_body`/`exit` + `cond_type` + `src_line`).
    - **정리(theorem)**: 이 CFG 에서 forward-pred(≥2)인 노드는 오직 ① join(`merge`) ② loop_exit(cond-false+모든 `break`)
      ③ do-while φ 뿐(루프 헤더는 back-edge 제외 시 단일). 따라서 **이 flag 노드만 breakpoint 타겟에서 빼면**
      남는 착지 노드는 전부 forward-pred=1 → 위 13 의 "선행 out-state = pre-state" 로직이 이미 정답.
    - **구현(프론트-only, 코어 무변경, tsc OK)**:
      · `useNavigation.ts`: `isForbiddenTarget`(flag merge/loop_exit/fixpoint_evaluation/exit) + `canBreakpointOnLine`
        (그 라인에 비-금지 노드 하나라도 있으면 허용). run-to-bp walk 정지조건에 `!isForbiddenTarget` 안전망.
      · `CodePane.tsx`: `}`/구조적 라인 gutter 클릭 시 breakpoint 설정 무시(+onMount 클로저 stale 방지 ref).
      · `StateLocals.tsx`: `loop_body` 노드는 pre-state 가 **fixpoint 불변식**이므로 note 에 "· 루프 불변식(모든
        iteration)" 부기. (루프 안은 **금지 안 함** — 디버깅 가치 큼, 라벨로 의미만 명확화.)
    - **잔여 ☐ try/catch**: `try_join` 은 `merge` 라 자동 포함(타겟 금지)되나, **catch 블록 진입(catch_entry/
      catch_end)의 "예외 발생 직전" pre-state** 의미는 미검증(선행 단일 여부·정상/예외 경로 구분). 별도 과제.
    - **미검증**: 브라우저 육안(특히 while/for 루프 함수 = 실엔진 필요, mock 엔 루프 없음).

**이번 세션 설계 결정 (decisions log 확장):**
- **(29) DebugBatch = 번들 모델** (라인키·BEGIN/END 폐기, snapshot/restore 유지). 위 6 참조.
- **(30) compound 접근 = 코드 스캔 자동 도출** (decision #6 "값만 입력" 준수, @IReturn 과 동일 인프라):
  함수 표현식 IR 순회 → base 가 mapping/struct/array 인 index/member 접근 체인 수집 → **등장한 distinct
  경로마다 슬롯**(key=경로문자열 `balances[msg.sender]`·`accountBorrows[k].principal`, valueKind=leaf 타입).
  사용자는 값만 채움. 근거 = **RQ1 실어노테이션이 이 형태**(`SolidityGuardian/evaluation/RQ1/cases`):
  `@StateVar balances[msg.sender]`, `_allowances[1][101]`(중첩), `accountBorrows[101].principal`(매핑[키].멤버),
  `_ctx.a.x`(중첩구조체), `self.balances[account]`(구조체.매핑). 엔진이 이미 파싱·해석(RQ1 20/20). 난점:
  ①키가 변수면(`balances[to]`) 그 변수도 seed돼야 resolve → **로컬 seed 먼저 emit** ②leaf 타입 = 체인 따라
  해석 ③함수에 안 나타나는 엔트리(불변식용)는 자동도출 불가 → 필요시 수동추가 escape hatch(후순위).
  ★일부 케이스(루프 `arr[i]` 등 구체 인덱스)는 사용자·solver 몫.
- **(31) L2 값 제안 = 엔진 오라클 + LLM/solver 제안자** (decision #9/#12/#17 구체화). 하위분해: (1)코드에 있는
  경로/키 = **도출** / (2)구체 인덱스·미등장 엔트리 = **사용자·solver** / (3a)분기도달·intent 위반 witness =
  **solver**(`reach`) / (3b)opaque·현실적 값(decimals·토큰크기·외부리턴) = **LLM**(`suggest_value`). 구조 =
  LLM 후보 → 엔진 evaluate/verify 검증 → 채택(LLM 오류를 엔진이 가드). **RQ1 어노테이션 프롬프트 활용**: 포맷
  few-shot·magnitude 주입엔 유용하나 **누수 경계**(RQ1=평가셋 → few-shot 전용, 성능평가는 held-out) +
  objective 명시("bug witness" vs "plausible"). **후순위** — 결정론 코어(6→8→7) 먼저.

- **(32) 디버그 입력 = Apply 버튼 트리거 + 결과 State&Locals 노출** (신규, 위 10): per-keystroke evaluate 는
  interval 여러 글자 입력 중 매 글자 발사 → 낭비·partial 값 transient 에러. 디버거 멘탈모델("여러 변수 what-if
  세팅 → 한 번 실행")에 맞춰 **명시 Apply 버튼(+Enter)** 로 전환. 백엔드는 이미 `debugInputs` 통째 전송이라
  번들 무변경. **적용 = "run"**: breakpoint 있으면 그 지점까지, 없으면 EXIT(최종상태)로 커서 자동 이동 →
  State&Locals 가 변수별 값을 즉시 표시(7번 좌표계 수정으로 이제 의미 있음). 대안(debounce/onBlur)보다
  예측가능. ★"안 뜸"의 해결책은 7번이고 버튼은 UX 개선임을 구분.

- **(33) breakpoint 표시상태 = 선행 out-state(= pre-state) — 확정(A: 프론트-only)** (위 13·14): CFGNode.state
  는 out-state 라 커서 노드 그대로 표시하면 "실행 후". "이 줄 실행 직전" = **선행 노드 out-state**(별도 in-state
  필드 불필요 — 사용자 통찰). 단일선행·ENTRY(시드 override)·EXIT(최종) 구현완료. **조인(다중선행) 문제는 옵션
  (a)/(b) 대신 "타겟 제한"으로 회피 확정**: 엔진이 join 을 명시 노드(`merge`)로 즉시 만들고 sound merge 하며,
  join/loop_exit/φ/exit flag 가 프론트로 직렬화되므로 — **이 구조적 노드에는 breakpoint 를 못 걸게** 하면 남는
  타겟은 전부 forward-pred=1 → 선행 out-state 로 정확. 엔진 in-state 노출/프론트 merge 둘 다 불필요. 루프 본문은
  금지 대신 "루프 불변식" 라벨. 구현 = `useNavigation`(isForbiddenTarget/canBreakpointOnLine)+`CodePane`(클릭 차단)
  +`StateLocals`(loop_body 라벨). **잔여**: try/catch catch-블록 pre-state 미검증. 위 14 참조.

- **(34) compound + @IReturn = 통합 정적 "접근-경로 스캐너" ✅ 구현완료** (decision #30 확장, item 8·9 병합; 전제·결과 e2e 검증):
  둘은 **동형 문제** — 슬롯 = **ROOT + accessor\*** , 키 = `str(maximal_path_expr)`. accessor(공통) = index/member/call.
  ROOT 만 분기: compound 상태/로컬변수(mapping/struct/array typeInfo) → StateVar/LocalVar / 인터페이스 호출
  `v.f()`·캐스트 `I(a).f()`(interface 타입·`interface_names`) → IReturn(opaque).
  **어디서 하나 = evaluator 아님, `_derive_debug_slots`(engine_api, 비코어)가 `node.statements` AST 를 훑음**:
    - evaluator 는 bottom-up 재귀라 **maximal 경로 경계("재귀의 끝")를 모름** → 중간 인터페이스 핸들이 가짜
      슬롯으로 샘. 또 fixpoint 로 여러 번·값 있을 때만 돌고 재평가 유발. statement 레벨 순회는 `stmt.left/right/
      init_expr` = 애초에 maximal 표현식에서 base 따라 root 로 하강 → 경계 공짜·1회·구문+타입만. (사용자 통찰)
  **전제 검증(e2e, `.venv` 실측)**:
    ① `Expression.__str__` 이 경로를 정규 직렬화 — `accountBorrows[msg.sender].principal`,
      `engine.mochiProfile().calculateFeeIndex()`, `IERC20(baseToken).balanceOf()` 등 6/6 정확.
    ② analyze 후 `node.statements` 에 expr AST **보존됨**(`VarDecl.init_expr=Expression:'_balances[u]'`,
      `Assign.left/right`) → 정적 스캔 실현가능. (사용자가 의심한 "evaluation 후 폐기"는 아님.)
    ③ 현 도출 갭 실증: `_balances` 매핑을 opaque 슬롯 1개로만 냄(필요한 `_balances[u]` 못 줌).
  **키-변수 의존성**: `[u]`·`[msg.sender]` 등 변수 키는 그 자체가 스칼라 슬롯(param/global/local)로 이미 도출 →
    Apply 배치에서 함께 seed → **emit 순서 무관**(#30 의 "먼저 emit" 제약 불필요). 완전 심볼릭 인덱스(`arr[i]`)는
    #30 대로 사용자·solver 몫 — 스캐너 정책(심볼릭 키째 emit vs skip) 구현 시 결정.
  **위치**: 이 프로젝트만 변경, **SolidityGuardian 반영 안 함**(사용자 지시). evaluator 코어 훅 대체 → 코어 무변경.
  **★ 도중 발견 별개 엔진 갭 2개**(슬롯 도출 무관, compound **값 evaluate** 시 물림):
    (b1) nested-literal-key 쓰기 `_allowances[1][101]=p` → "Literal '101' cannot appear standalone on LHS"
      (안쪽 `[101]` 처리 때 부모 매핑을 caller_object 로 안 넘김; Update.py:386).
    (b2) nested-literal-key 읽기 `q=_allowances[1][101]` → `Evaluation.update_mapping_in_cfg` 가 없는 속성
      `self.current_target_function` 참조 → AttributeError.
    **성격 재평가(사용자 지적, 후순위 격하)**: 두 케이스 다 **벌거벗은 이중 정수 리터럴 키** — 실 RQ1 입력엔
    안 나오는 인위적 코너(실 키는 `msg.sender`·변수·타입 있는 표현식). int/address 모호성도 실은
    매핑 **선언된 키 타입**(`ref_var.typeInfo`)으로 해소되므로 "리터럴 금지"가 아니라 **중첩 전파 구멍**.
    단일 리터럴 키(`_balances[1]`)는 정상. → 실입력 무관, 우선순위 낮음.

- **(35) forward 네비게이션 "dead-branch" 버그 수정 ✅**(프론트-only, 엔진 무결 확인):
  **증상**: withdraw 에 `a=bal=token.balanceOf()=[10,10]` 넣고 require(`a<=bal`)에서 forward → **error_exit 로 샘**.
  **진단(`.venv` repro 실측)**: 엔진은 **정상** — IReturn 값이 로컬 `bal` 에 정상 바인딩(`bal=[10,10]`),
    require true 후속 채워짐, ERROR state 는 빈 `{}`(미도달), 최종 `balance=[0,0]` 정확. 문제는 프론트 네비:
    `stepForward` 가 `succ()[0]` 만 집음 → require 엣지 순서가 `false→ERROR` 먼저라 죽은 sink 로 감.
    게다가 `isBottom` 이 `⊥`만 봐서 **빈-state ERROR(⊥ 아님)를 못 걸렀음**.
  **수정 = 엔진 `_run_worklist` 알고리즘 1:1 이식**(사용자 지시): `engineDead(id)` =
    `id===cfg.error_exit`(엔진 `_is_sink`, error_exit **만** — return_exit 은 정상도달이라 제외)
    **OR** state 에 `⊥`(엔진 `_is_bottom_env` "하나라도 BOTTOM"). 빈-state 휴리스틱 대신 **id 로 sink 직접 식별**
    (직렬화가 세 exit 를 `'exit'` 플래그로 뭉개지만 `cfg.error_exit/return_exit/exit` id 는 분리 보존).
    `forwardPick` = 살아있는 후속 우선(전부 죽으면 `ss[0]` 폴백). `stepForward/stepInto/continueRun` +
    자동배치 effect(run-to-bp, 같은 잠재버그) 일괄 적용. tsc 통과.
  **잔여 정책**: 양쪽 다 live 인 조건(require 통과·실패 둘 다 가능)에선 여전히 엣지 순서 first-live —
    명시적 "true 우선" tie-break 는 엔진 의미가 아닌 UX 결정이라 **미적용**(요청대로).
  **부수**: mock `result.withdraw.json` 의 유령 intent(`before: bal>=a`·`changed(balance)`, 잘못된 문법) 제거.
    단 이 파일은 import 안 되는 죽은 mock — 실 유령 intent 출처는 **localStorage persist(`intents`)** 일 수 있음
    (store `partialize` 가 저장, `migrate` 가 v3 서 debugInputs 만 지우고 intents 는 안 지움). 필요시 version bump.

- **(36) 서버 세션이 "클라이언트 상태의 주인" 노릇 → 유령 상태 버그 3종 ✅ 2건 수정, 1건 미결**:
  **발단**(사용자 보고): 서버는 켠 채 **프론트만 껐다 켜니** withdraw 의 debug 슬롯이 `balance`+`amt`
  (= **deposit** 의 것)로 나옴. 첫 실행 땐 정상(`a`/`balance`/`fee`/`token.balanceOf()`).
  **근본 원인 = 스코프를 요청에서 안 받고 세션 기억에서 "추측"**: `_resolve_fcfg(None, None)` 의 폴백은
    `contract=candidates[-1]`, `func=names[-1]` = **"컨트랙트의 마지막 함수"**(engine_api:809-811). Vault 의
    마지막 함수가 `deposit` 이라 그 슬롯이 나온 것. 프론트는 `results` 를 persist 안 하므로(store `partialize`)
    재접속 시 `/analyze` 재요청 → 서버 세션은 살아있음 → `_build`(스코프 명시) 대신 **`update`+`structure()`**
    (스코프 **미전달**) 경로를 탐. → **프론트 재시작 전용 아님**: 세션 생존 중 **코드 편집**도 같은 경로라 상시 발화.
  **(a) `/analyze` 스코프 명시 ✅** `engine_server:90` `sess.structure(r.contract, r.func)`.
    e2e 실증: 같은 세션 재-analyze 시 OLD=`Vault.deposit`/`[amt,balance]` → NEW=`Vault.withdraw`/원래 5슬롯.
    (부수 피해였던 `_slots` 오염도 해소 — 오염 시 `token.balanceOf()` 가 `@LocalVar` 로 방출돼 IReturn 이 조용히 깨짐.)
  **(b) `/verify` 가 요청 inputs 를 무시 → 이전 브라우저 생애의 debug 값으로 verdict ✅**:
    `verify()` 가 서버 기억(`_last_debug_values`)만 봄. 프론트는 debugInputs 를 **persist 안 함**(#32 세션성) →
    재접속 후 값 없이 intent 만 건드리면(`addIntent` → verify-only) **유령 값 verdict**. decision #22 소프트
    게이트("값 없으면 verdict 없음") 무력화. 실증: OLD=`Violated`(유령) / NEW=`[]`.
    수정: `verify(..., debug_values=r.inputs)`, states 캐시도 **이번 요청 값의 키**로 조회(`_values_key` 헬퍼 추출,
    evaluate/verify 공용). Apply 흐름(evaluate→verify 동일 값)은 여전히 캐시 적중 = 재해석 없음. 죽은
    `_last_values_key` 필드 제거.
  **원칙(결정)**: **세션 = 소스의 캐시일 뿐, 클라이언트 상태(스코프·debug 값·intent)의 주인이 아니다.**
    매 요청이 진실의 원천. → "새로 접속하면 코드는 남고 분석/판정은 빈 상태" 라는 기대 동작이 자동으로 나옴.
    (대안이던 "페이지 로드마다 세션 폐기(clientId)" 는 B1 증분 CFG 를 함께 버려서 기각. 채택 시 대형 컨트랙트
    full-rebuild 비용 측정이 선행돼야 함 — 미측정.)
  **(c) `_apply_delete` 도 같은 추측 사용 ✅ (2026-07-18 검증 완료)**: `_resolve_fcfg(None,None)` 으로
    수술 대상 fcfg 를 고름 → 타겟이 "마지막 함수"가 아니면 **엉뚱한 CFG 를 수술**(타겟엔 지운 문장 노드가
    유령으로 잔존, 소스 장부만 줄어 소스/CFG 이탈). 스코프 명시로 수정. **37 해결 후 실증**:
    `scope=Vault.withdraw` 로 해석 → `line_info[21].cfg_nodes[0]=Assign_21` 이 해당 그래프에 존재 → 수술 성공,
    withdraw CFG 에서 L21 소멸. 기존 B1 테스트가 못 잡은 이유: 테스트 컨트랙트가 **함수 1개**라 "마지막 함수"
    폴백이 우연히 정답.

- **(37) ★ B1 증분 편집이 실제 앱에서 **항상 거부**되고 있었음 ✅해결 (2026-07-18)**:
  **증상**: withdraw 에서 `balance = bal - a;` 한 줄 삭제 → `update()` 가 **아무것도 적용 안 함**. CFG 에 노드
    잔존 + 그 문장이 **여전히 실행됨**(EXIT `balance=[0,0]`, 삭제됐다면 입력 `[10,10]` 유지가 정상).
  **원인**: `incremental.syntax_valid` 가 풀소스를 `sourceUnit` 으로 **엄격 파스**하는데,
    **`Parser/Solidity.g4` 에 일반 라인 주석 규칙이 없음** — `//` 는 **어노테이션 전용 토큰**
    (`'//' '@During'`·`'@StateVar'`… 255-309행). 771-776 엔 WS skip 과 `/* */` HIDDEN 만 있음.
    → 실 .sol 의 첫 줄 `// SPDX-License-Identifier: MIT` 에서 `extraneous input '//'` → 게이트 **거부**
    → `update()` 가 `{"ok": False}` 조기 반환 → **첫 analyze 이후의 모든 코드 편집이 CFG 에 반영 안 됨**.
  **왜 여태 안 걸렸나**: ① `temp/` 의 B1 테스트 소스에 `//` 주석이 **하나도 없음**(grep 확인) → "67개 통과" 는
    주석 없는 인공 소스 한정. ② 프론트가 `/analyze` 의 diagnostics 를 **무시**(store.ts:249 `// TODO: squiggle`)
    → 게이트가 거부해도 **아무 표시 없이 조용히 실패**. (첫 analyze 는 `slice_solidity`+record apply 경로라
    ANTLR 에러 복구로 관용 → 정상 동작. 매 실행 콘솔의 `[ANTLR] 1:0 extraneous input '//'` 노이즈가 그 흔적.)
  **채택안 = C. 문법에서 `'//'` 제거 + 원본 `LINE_COMMENT` 복원 (2026-07-18)**:
    - 검토했던 A(게이트에서 주석 스트립)·B(`'//' ~[@\r\n]…` 조건부 LINE_COMMENT)는 **둘 다 기각**.
      B 는 애초에 성립 안 함 — 이 프로젝트가 실제로 뱉는 형태가 `// @During`(공백 有, `Domain/Annotation.py:178`
      ·`ContractAnalyzer.py:3013`·`frontend/src/editor/solidity.ts:59`)이라 `[ \t]*` 가 0글자로 백트랙하고
      `~[@\r\n]` 가 **공백을 먹어** 어노테이션을 삼킨다. 데이터셋에 `// @notice`·`// @dev` 도 있어 "`@` 시작 주석
      전면 배제"도 불가.
    - **근본 관찰(사용자 지적)**: IntentKeeper 에선 intent·debug 값이 **소스 주석에서 읽히지 않는다**. 프론트가
      구조화 데이터로 보내고 `engine_api` 가 `f"// {kw} …"` 문자열을 **직접 만들어** `intentUnit` 단독 파싱에
      넘길 뿐 — 붙였다 떼는 왕복이라 정보량 0. `'//'` 는 Guardian 시절 "주석이 곧 입력" 워크플로의 잔재.
    - **변경**: `Solidity.g4` 의 `debugGlobalVar`/`debugStateVar`/`debugLocalVar`/`debugIReturn`/`duringIntent`/
      `postIntent` 에서 `'//'` 제거 → 충돌이 사라져 원본 `LINE_COMMENT : '//' ~[\r\n]* -> channel(HIDDEN);` 를
      **그대로 복원**(휴리스틱 불필요). 재컴파일 `antlr4 -Dlanguage=Python3 -visitor Solidity.g4`.
      호출부는 `engine_api.py:848`(intent)·`:873`(debug 값) 두 줄에서 `// ` 접두어 제거.
    - **부수 확인**: `DebugBatchManager.flush()` 는 **소스를 전혀 안 건드림**(`update_code` 없음). `snapman`
      snapshot/restore 는 소스 롤백이 아니라 배치별 값 초기화용. 주석 14-15 행에 "디버그 값 주석엔 라인번호가
      무의미" 명시 → **debug 값 위치정보 불필요**가 코드로 확인됨(`ireturn_registry` 키도
      `(contractVar, funcName, index)` 로 라인 없음, `Utils/CFG.py:555`). 따라서 검토했던 "debug 값 직접 주입
      리팩터링"은 **불필요**로 결론. sentinel 6파일(`startswith("// @")`)도 사용자 소스 레코드만 보는
      Guardian/CLI 경로라 손대지 않음.
    - **검증**: `update() → {"ok": True, "diagnostics": []}` (이전 `{"ok": False}`), delete 실제 반영,
      intent·debug 값 파싱 회귀 없음(`bal [10,10]`·`fee [0,0]`·`balance [0,0]` + verdict + states 캐시 적중).
    - **대가(수용됨)**: `evaluation/RQ1/run_all.py` 가 **20/20 VIOLATED → 0/20 (전부 SATISFIED)** 로 회귀.
      RQ1 은 `.sol` 안에 `// @During` 이 적힌 형태로 인텐트를 먹이는데 이제 그 줄이 주석으로 스킵된다. 즉
      **위반이 조용히 "만족"으로 둔갑** — 가장 나쁜 실패 형태. `main.py:142` sentinel 에서 파서에 넘기기 직전
      `// ` 만 떼면 복구 가능하지만(소스 형식과 API 형식 공존), **RQ1 은 Guardian 복붙본이고 IntentKeeper 는
      웹 기반이라 실험 방식 자체를 다시 짜기로 결정(사용자)** → 패치하지 않음.
      ⚠ **그때까지 e2e 회귀 커버리지 0** — 위와 같은 조용한 둔갑을 잡아줄 스위트가 없다.
  **연쇄**: 36(c) 재검증 ✅ 완료. 프론트 diagnostics 표시 ✅ 완료(아래).

- **(38) 프론트가 `/analyze` diagnostics 를 삼키던 문제 ✅해결 (2026-07-18)**: 서버는 원래 정상 반환
  (`engine_server.py:93,98`), 프론트만 `store.ts:249 // TODO` 로 버림 → **37 이 반년 가까이 안 보인 직접 원인**.
  `store.ts` 에 `syntaxErrors` 상태 추가(structure tier 에서만 갱신 — evaluate-only 에서 빈 배열로 덮으면
  squiggle 이 잘못 사라짐), `WorkspacePanel.tsx` 에 Monaco `setModelMarkers` squiggle + 배너,
  `DebugView.tsx` 에 "아래 결과는 직전 유효 코드 기준" 배너. `line:0` 전역 진단은 1행 클램프.

- **(39) ANTLR 버전 정합 ✅ (2026-07-18)**: 생성물은 4.13.2, `.venv` 런타임은 4.12.0 이라 실행마다
  `versions disagree` 경고가 쏟아지던 상태(우리 변경과 무관한 기존 간극). 런타임을 **4.13.2 로 올리고**
  `requirements.txt` 반영 → 경고 소멸, 동작 동일.

- **(40) 기능 #1 "intent 위치 debug 입력 자동 생성" 목적 재정의 + 설계 방향 (2026-08-05 랩미팅 준비 세션)**:

  **① 목적 재정의 — 이 기능이 무엇을 얻으려는가.**
  잘못된 초기 프레임(내가 세웠다 폐기): ~~"SATISFIED=증명, VIOLATED=의심 → 허위 VIOLATED 제거"~~.
  **이 엔진에선 틀리다.** 추상상태 `A ⊇ 실제도달 `C``일 때 `A` 전체가 만족이면 증명, `A` 전체가
  위반이어도 (도달 가능하면) 증명 — **SATISFIED·VIOLATED 둘 다 건전**하다. 미결은 오직 `A` 가
  intent 를 **일부만** 만족할 때이며 그게 **warning**이다. 근거: `GuardianVerificationEngine.py:1590`
  `_status_from_cmp`(satisfied/violated/warning 3분기), `Interpreter/Engine.py:1989` 주석
  ("warning = 증명 불가, interval 도메인에서 흔함 → 누락 시 satisfied 로 오판").
  → **debug 입력이 없으면 파라미터가 ⊤ 라 대부분 warning 으로 떨어진다. 따라서 이 기능의 목적은**
  **"버그 유무 판정"이 아니라 "엔진 결과가 warning 인 것을, 판정이 확정되는 입력 영역들로 분할"** 이다.
  (용어: "warning 상태의 intent" 아님 — **엔진 결과가 warning 인 경우**. 사용자 정정.)

  **② 산출물 4종.**
  | 산출 | 의미 |
  |---|---|
  | 확정 **Violated** 영역 | 버그 witness (원래 "1. 버그 유무") |
  | 확정 **Satisfied** 영역 | 안전 영역 = 사실상 **빠진 `require`** 제안 |
  | **Unreachable** (도달 불가) | intent 위치가 죽은 코드 = **검증 대상 아님**. Satisfied 를 공허하게 뱉는 것보다 정직. 사용자 추가 요청 |
  | 남은 **Warning** | 정밀도 한계(관계 손실/미모델링) — 정직하게 "모름" 보고 |
  "이 경로로 이 값 거쳐 여기서 깨진다"(원래 2번)의 **메커니즘(path-exclusive 입력)은 1번의 신뢰성
  전제로 필수**, 단 **설명 UI 는 차후**(코드에 뻔한 분기조건 되풀이는 노이즈라는 사용자 지적 반영).

  **③ intent 는 항상 전제.** intent 없는 순수 입력생성 모드는 **안 만든다**(그 순간 Mythril/Echidna
  와 같은 링 = 차별점 상실. 사용자 확정). intent 가 경로를 **없애는** 게 아니라 **intent 변수 기준으로
  구별 안 되는 경로를 병합** → 경로 폭발을 억제하는 역할.

  **④ 도달 불가 — 엔진에 기계 이미 있음, 통로만 없음.** BOTTOM 전파·unreachable skip·⊥ 렌더가
  이미 구현: `Interpreter/Engine.py:581`(in_vars 빈 dict → skip), `Evaluation.py:2890`(BOTTOM 비교 →
  bottom), `engine_api.py:271`(⊥ 렌더). **없는 것 = 판정 통로**: `engine_api.py:66`
  `VerdictStatus = Literal["Satisfied","Warning","Violated"]` 에 4번째 상태 자리 없음 → **Unreachable 추가 필요**.

  **⑤ false_regions — 위반영역 계산기 이미 있음, 그러나 (a)출력공간이고 (b)API에서 버려짐.**
  `GuardianVerificationEngine.py:1643` `_false_regions_for_op(L,R,op)` 가 `>,<,>=,<=,==,!=,in,not in`
  전부에 대해 `{lower_violation, upper_violation, overlap_zone}` 을 이미 반환한다.
  - **(a) 출력공간 ≠ 입력공간**: `false_regions` 는 *비교되는 값*(예 `fee*100`)의 구간이지, 사용자가 넣는
    *진입 파라미터*(예 `a`)의 구간이 아니다. **엔진은 "어디서 깨지는지"는 알지만 "그러려면 무엇을
    넣어야 하는지"는 모른다** — 그 변환이 이 기능의 알고리즘 핵심.
  - **(b) 통로 없음**: `VerdictItem`(`engine_api.py:127`)의 `region` 은 *라인* 범위 `(line,line)` 이지 값
    범위가 아니다(`frontend/src/types/result.ts:44` 동일). 구조화된 `false_regions` 는 API 경계에서
    버려지고, `status=="warning"` 일 때 `message` 문자열 꼬리표
    (`GuardianVerificationEngine.py:280` `msg_tail`)로만 프론트에 샌다(파싱 불가 형태).
    → **`VerdictItem` 에 `falseRegions` 구조 필드 추가 필요.**

  **⑥ 위반영역이 박스 하나가 아닌 경우(왜 "몇 개 보여줄까"가 본질 문제인가).**
  - (a) **단일 변수·단일 비교에서도 발생**: `==`,`in` 은 `lower`·`upper` 동시 채움
    (`_false_regions_for_op` op=='=='). `x∈[0,100]`, intent `x==50` → 위반 `[0,49]∪[51,100]`.
  - (b) **변수 간 관계 = 대각 경계**(가장 흔함): `bal < a+101` 같은 반평면은 축평행 박스로 정확 표현
    불가 → **서로 포함 안 하는 극대 박스 여러 개**. 관계형 intent(`balance>=bal-a` 등)는 예외가 아니라 기본.
  - (c) **경로 분리**: 분기별 위반식이 다르면 조각이 입력공간 여러 구석에.
  - (d) **정수 산술**: `/`(계단), `%`(짝수만), 오버플로(구간 두 동강).
  → "박스 하나"가 오히려 특수. 그래서 **제시 개수 = intent 변수 기준 병합**으로 관리.

  **⑦ 생성 알고리즘 = 역전파 대신 "판정 기반 분할"(verdict-guided partitioning).**
  심볼릭 역전파(⑤)를 안 풀어도 된다. **입력 박스 B 를 넣고 엔진 전방 평가 → 판정을 물어보는** 재귀 분할:
  ```
  partition(B, depth):
    v = engine.evaluate(B)                       # evaluate 1회
    if v ∈ {Satisfied, Violated, Unreachable}: emit(B,v); return   # 확정 → 종료
    if depth>D or B singleton:                  emit(B,Warning); return  # 정밀도 한계, 정직 보고
    x = pick_variable(B)   # intent 등장 변수로 제한, false_regions 경계 지나는 변수 우선
    p = pick_cut(B, x)     # 경계값 우선(경로조건·intent 상수, false_regions 경계) — 중점이분 아님
    partition(B|x<p, d+1); partition(B|x>=p, d+1)
  후처리: 같은 판정 인접 박스 재병합
  ```
  - **함의: Z3 가 선택사항이 된다.** `/`,`%`,외부호출,루프 — 심볼릭 인코딩 난제를 엔진이 전방으로는
    이미 처리하므로 인코딩 없이 동작. "루프를 Z3 에 어떻게 주나" 걱정이 통째로 사라짐(추상해석
    fixpoint 가 이미 처리 = 심볼릭 실행 대비 **구조적 이점**).
  - **건전성 공짜**: 각 조각 판정이 엔진 확정 판정 → 건전. 남은 warning = "모름". **완전하진 않되 건전**.
  - **한계(정직)**: 얇은 위반영역(예 `a==57` 만 위반)엔 분할이 약함 → **여기가 Z3 의 남은 자리**
    (흔한 경우=분할, 얇은 경우=solver). 평가횟수=비용 → 조각수 상한·시간예산 필요. 대각경계는 계단
    근사라 잘게 쪼개짐 → 병합 필수.
  - **관련연구**: 입력공간 trace partitioning(Rival & Mauborgne, TOPLAS 2007), CEGAR 인접.

  **⑧ Z3 vs LLM 재정리(월요일 교수님 설명에서 정정할 것).**
  - ~~"LLM 이 범위 주면 Z3 검증은 상수비교라 빠르다"~~ **틀림**: 박스검증은 `∀x∈B.PC(x) ⟺ UNSAT(x∈B ∧ ¬PC(x))`
    = 원래와 같은 theory. 게다가 검증이 싼 경우 = 분리가능한 경우 = 애초에 LLM 도 Z3 도 불필요했던 경우.
  - 비선형(NIA)은 "느림"이 아니라 **결정 불가능**(Z3 가 `unknown`/미종료) → 폴백이 설계 필수.
  - **검증자 = Z3 가 아니라 이미 있는 엔진**. LLM/solver 는 **생성**만, **검증·건전성은 항상 엔진**.
    LLM 은 정확도가 아니라 **커버리지(Z3 가 원리적으로 못 푸는 영역)** 확장 역할.
  - 케이스화는 **코드가 아니라 치환 끝낸 경로조건 형태**로: T0 변수별 분리(구문유도) / T1 선형다변수(Z3) /
    T2 비선형(Z3+타임아웃→LLM) / T3 인코딩불가(외부호출·심볼릭키·해시 → LLM/미지원) / T4 루프(fixpoint).
    **티어는 원자 아니라 경로 단위**(경로에 T2 원자 하나면 그 경로가 T2). 분포는 추측 말고
    `evaluation/RQ1/target_contracts_original`, `Dataset/Numscout` 에서 **경로 단위 측정**(폴백 비율 = 성능 예산).

  **작업 브레이크다운**(2026-08-05 갱신):
  - **(A1) falseRegions 배관 ✅ 완료·e2e 검증**: 데이터가 이미 `details` 에 있어 순수 배관.
    `engine_api.py` `VerdictItem.falseRegions`(Optional) + `_false_regions_out`(snake→camel, 경계 문자열화
    ∵ 2^256/JS number 정밀도) / `result.ts` `FalseRegions` 타입 / `VerdictPanel.tsx` `FalseRegionsView`.
    검증: `a=[0,100]`, intent `@During a<=50` → status=Warning,
    `falseRegions={lowerViolation:None, upperViolation:['51','100'], overlapZone:['50','50']}` 프론트 verdict 까지 도달.
    (스크래치패드 `smoke_false_regions.py`.) **제약: warning·단일 절만**(복합 절은 `_combine_logic_results`
    가 드롭 = #40⑥ "박스 여러 개" 케이스, 후속). **출력공간**임을 UI 라벨에 명시("위반 영역(출력값 기준)").
  - **(A2) Unreachable 탐지 + 판정 ✅ 완료·e2e 검증 (during 한정)**: 배관 아님 — 엔진이 깨끗한 도달불가
    판정을 계산 안 했음(현 동작: unreachable intent 가 `Warning` 으로 뭉개짐, risk 낮게). **핵심 관찰**:
    도달불가는 이미 추상해석이 **BOTTOM env 로 전파**해 둠(CFG 구조 추론 불필요). 판정 = `_process_node_intents`
    진입부에서 `not cur_vars or self._is_bottom_env(cur_vars)` → `{'status':'unreachable'}` emit + clause 검사 skip.
    `_is_bottom_env`(Engine.py:1148, "변수 하나라도 BOTTOM이면 infeasible")는 **엔진 자신의 분기 pruning 기준과
    동일** → 제어흐름 판단과 일관(다른 기준 쓰면 "엔진은 죽었다는데 verdict는 살았다" 모순).
    변경 5곳: `Engine.py` `_process_node_intents` 진입부(standalone·inline 두 verify 경로 공통 choke point) /
    `engine_api.py` `VerdictStatus`+`"Unreachable"`, `_VSTATUS`+`"unreachable"→"Unreachable"` /
    `result.ts` `Verdict`+`'Unreachable'` / `styles.css` `.verdict-item.Unreachable`·`.marker.Unreachable`(회색).
    **검증(스크래치패드 probe_unreachable/probe_relational/probe_derived)**:
    · 죽은 분기(`a>100 ∧ a<50`) → `Unreachable` ✓
    · **직접 관계 비교**(`a>b ∧ a<b`, a·b 입력)도 엔진이 bottom 으로 잡음 → `Unreachable` ✓
      (⚠ 이전 세션 내 주장 "관계형은 다 놓친다"는 **오류**였음 — 엔진의 반복 정제가 라이브 변수 간 직접
      모순은 잡는다. 실측으로 정정.)
    · 정상 warning + falseRegions 회귀 없음 ✓ · **파생변수 도달불가**(`x=a+b` 후 x·a 제약)는 bottom 이 아니라
      `Violated` 로 나옴 = **오탐 없음** 확인 ✓ (동시에 이게 유일한 miss 임을 실증) · 프론트 tsc clean.
    **한계(문서화)**: 구간 도메인이 bottom 으로 판정한 도달불가만 잡음. **파생 변수 상관 소실**(`x=a+b` 후
    x 제약이 a 제약과 모순 — x 가 대입 시점에 굳어 a>100 정제가 역전파 안 됨)은 bottom 아님 → 놓침(기존 한계,
    A2 가 악화 안 함). 입력을 점에 가깝게 좁히면 이 miss 도 소멸(→ (B) 분할과 연결). **Post intent 미포함**
    (`_process_exit_node_intents` 별도 경로 — 후속). "완전 도달불가"만, join 오염(도달은 하나 상태 오염)은
    Warning=정밀도 문제=(B) 몫으로 구분.
  - **(B) 분할 탐색기 설계문서**(pick_variable·pick_cut·병합·예산) / **(C)=본 기록**. 코어 수정이므로 A2·B 는 승인 후.

- **(41) SolidityGuardian 엔진 변경 1회성 이식 (2026-09-16)**: Guardian 쪽에서만(논문 리비전 때문에) 고쳐진
  변경을 **IntentKeeper 컨셉에 맞는 것만 선별해** 가져옴. #37 의 "동기화 중단" 방침은 유지 — 상시 동기화 재개가
  아니라 **단발 포트**이며, IntentKeeper 고유 divergence(#8 삽입없는적용·#29 리스트batch·#34 접근경로스캐너·
  B1 증분편집)는 전부 보존했다.

  **① snapshot-qualified reference = intent 문법 확장 (필수 이식, ★ 논문 영향 큼)**
  - **이전**: `Before/After`·`Assign/Current`·`Entry/Exit` 가 **전용 clause 형태**로만 존재
    (`DuringBeforeAfter`/`DuringAssignCurrent`/`PostEntryExit`) → **산술식 안에 못 들어감**.
  - **이후**: `arithFactor` 의 **1급 항**으로 승격 →
    `@Post balance(Exit) == balance(Entry) - amount` 처럼 산술 표현식 *내부*에서 스냅샷 참조 가능.
  - **스코프 게이트 = ANTLR semantic predicate + 파서 멤버 `inDuring`**:
    `{not self.inDuring}? varRef '(' ENTRY|EXIT ')'` / `{self.inDuring}? varRef '(' BEFORE|AFTER|ASSIGN ')'`.
    `duringIntent` 가 `inDuring=True`, `postIntent` 가 `False` 로 세팅.
    **rule parameter 는 불가** — `arithExpr` 가 left-recursive 라 ANTLR `error(169)`. 파서 멤버가 유일한 길.
    `ParserHelpers.generate_parse_tree` 가 호출마다 파서를 새로 만들어 요청 간 상태 누수 없음(테스트 group 6 확인).
  - 제거: `DuringBeforeAfter`/`DuringAssignCurrent`/`PostEntryExit` 3개 컨텍스트 + 대응 visitor 분기.
  - **보존**: #37 의 `'//'` 없는 형태 + `LINE_COMMENT`. Guardian 문법을 통째로 덮은 게 아니라 IntentKeeper 형태
    *위에* 스냅샷 변경만 얹었다.
  - 부수: `DebugEnumLiteral` 을 `identifier ('.' identifier)*`(중첩 한정명)로, `tupleExpression` 추가.
  - `antlr4 4.13.2` 로 재생성(#39 정합 유지). visitor 5종 신설
    (`visitVarRefAtEntry/Exit/Before/After/Assign` → `Expression(context='VarRefAt*')`).
  - 평가측: `GuardianVerificationEngine._SNAPSHOT_CONTEXTS` 디스패치 +
    `_evaluate_snapshot_var_ref`/`_materialize_snapshot_refs`/`_clone_expr_with`.
    ambient 배선 4곳(`Engine.py`: `_before_cfg_node`/`_before_line_no`/`_current_fn_cfg`).
  - **★ RQ2 재산정 필요**: `evaluation/RQ2/l4_l5_case_review.md` 의 G6(다변수 불변식 PostEntryExit 표현 불가)·
    L4c/L4d(산술 PostEntryExit 부재)가 이 확장으로 **일부 해소**된다. 34 케이스 분류를 다시 매겨야 함.

  **② ⊤ → Warning 의미 변경 (2곳)**: 비교 대상이 ⊤ 일 때 이전엔 violated 로 떨어뜨렸으나
  `{"violated": False, "warning": True, "prob_true": 0.5}` + risk 계산으로 변경.
  **decision #40 ① 의 재정의("건전한 건 S/V 둘 다, 미결은 오직 warning")와 정확히 일치** — ⊤ 는 "모름"이지
  "위반"이 아니다. #40 (B) 분할의 전제이기도 하다(분할 대상 = warning).

  **③ ★ @Post 가 explicit return 있는 함수에서 로컬을 아예 못 읽던 버그**:
  `_get_post_exit_node`/`_preds`/`_exit_env` 블록 교체. 핵심 =
  `exit_n = node if node is not None else self._get_post_exit_node(fn_cfg)`.
  증상 — 스냅샷과 무관한 단순 절(`@Post b == 13`)조차
  `internal error: This 'b' is may be elementary variable but may not be declared` 로 실패.
  Phase 2 에서 6/6 실패로 드러났고 **스냅샷 기능과 무관한 선재 버그**로 확인. IntentKeeper 에 오래 잠복해 있었다.

  **④ 코어 wholesale 동기화 9파일** (IntentKeeper 고유 로직 부재를 확인한 뒤 통째 교체):
  `Domain/Interval.py`·`Domain/Variable.py`·`Utils/Helper.py`·`Utils/CFG.py`·`Analyzer/StaticCFGFactory.py`·
  `Interpreter/Semantics/{Refine,Update,DebugInitializer,Evaluation}.py`.

  **⑤ 선별 이식(부분 병합)**: `ContractAnalyzer`(`get_full_struct_enum_defs()`, enum_defs 전파 5곳, 배열 분기
  struct/enum defs, 튜플 None 슬롯 스킵 — IK 고유 `current_edit_event=="modify"` 분기 보존) ·
  `SolidityAnalyzer`(`file_level_enums`, `_is_during_inline` 재작성) · `DynamicCFGBuilder`(revert 배선 수정 —
  IK 고유 `delete_cfg_range`/`modify_condition_expr` 보존) · `main.py`(파일레벨 enum regex 스캔 +
  `Dependencies/interfaces` 를 type_scan_dirs 에 추가, 중복 `sa.update_code` 제거) ·
  `engine_api.py`(`_scan_file_level_defs_into` 신설 → **서버 경로도 CLI 와 동일하게** 파일레벨 enum/type alias 인식) ·
  `Domain/IR.py`(IK 전용 파일, `Expression.__str__` 에 스냅샷 렌더 추가 → verdict 메시지가 `balance(Exit)` 로 표시).

  **⑥ 의도적 미이식**: `DebugUnitAnalyzer`(Guardian 이 **구버전** — IK 의 리스트-batch = decision #29 가 최신) ·
  `main.py` 의 dict 기반 `batch_mgr.add_line/modify_line/delete_line`(같은 이유) ·
  `_derive_debug_slots` 접근-경로 스캐너(decision #34, IK 전용 = 기존 방침).

  **⑦ 방법론 항목 "@During 만 inline 가능 / @Post 는 항상 standalone"** — IntentKeeper 주경로에선 **구조적으로
  무의미**. decision #8(삽입 없는 적용: `_attach_intents` 가 `update_code` 를 아예 호출 안 함)이라 줄 밀림이
  발생할 여지 자체가 없다. 대신 *의미적 등가물*(라인→노드 부착 정확성)을 `temp/test_intent_attach.py` 로 검증하고,
  레거시 CLI 경로용으로 `_is_during_inline` 개선만 이식했다.

  **⑧ 죽은 코드 삭제**: `Engine.py` 의 인자 3개짜리 `_verify_post_clause_dynamic` 중복 정의(구 1815-1874, 60줄).
  호출부 2곳 모두 4-인자 정의를 쓰고 있었다.

  **검증**: 신규 `temp/test_snapshot_grammar.py`(25, 파서 레벨) · `test_snapshot_refs.py`(12, 손계산 가능한
  컨트랙트 e2e) · `test_engine_fixes.py`(33, 12그룹) · `test_intent_attach.py`(15) = **85 checks 전부 통과**.
  기존 증분편집 9종 60 checks **baseline 과 전부 동일**(회귀 0).

  **RQ1 과의 관계**: 이 이식은 `evaluation/RQ1`(이미 #37 로 0/20)과 무관 — RQ1 재작성 방침(아래 ⑤ 항목) 그대로.

- **(42) ★ require/assert 가 BOTTOM 을 전파하지 않던 버그 + @Post 도달불가 판정 (2026-09-18)**:

  **증상**: debug 입력이 함수 가드와 모순이면(`a=[200,300]` vs `require(a<=100)`) @Post 가
  `Warning` + `internal error: This 'balance' is may be elementary variable but may not be declared`
  (risk 0.0)로 나왔다. 같은 상황에서 @During 은 `Unreachable` 을 냈다 — **두 경로 비일관**.

  **근본 원인 (`Interpreter/Engine.py` `_run_worklist` require/assert 분기)**: `can_true` 가 False 일 때
  ```python
  else:
      self._set_bottom_env(t.variables)   # t.variables 는 아직 {} → 빈 dict 순회 = no-op
      # work.append(t) 없음 → BOTTOM 이 downstream 으로 전혀 안 퍼짐
  ```
  **바로 위 if/else-if 분기는 같은 상황을 올바르게 처리**하고 있었다(`copy(cur_vars)` → `_set_bottom_env`
  → `work.append`). 즉 메커니즘은 이미 있는데 require/assert 분기만 두 줄을 빠뜨린 것.
  연쇄: 이후 노드가 전부 `{}` → `_force_join_before_exit`(:1232) 가 `{}` 들을 join → `EXIT.variables={}`
  → `_is_bottom_env({})` 가 **False** → 도달불가 인식 실패 → `_exit_env` 가 `{}` → 변수 조회 실패.

  **실측 근거**(스크래치패드 probe_exit_nodes): 정상/모순 모두 `EXIT.preds=1` 로 **그래프는 안 끊긴다**.
  차이는 predecessor 의 `variables` 개수뿐(17 → 0). → **predecessor 검사로는 못 잡고 env 를 봐야 한다.**
  그리고 BOTTOM 이 아니라 *빈 dict* 였다 → `_is_bottom_env` 단독으론 못 잡는다
  (@During 의 `not cur_vars or _is_bottom_env(...)` OR 절이 우연히 막고 있었던 이유).

  **수정 3곳**:
  1. `Engine.py` require/assert 분기 → if/else-if 와 동일한 3단 패턴(seed → BOTTOM → append).
  2. `Engine.py` `_process_exit_node_intents` 진입부 → `_preds` 비었거나 `_exit_env` 가 비었거나
     BOTTOM 이면 `status='unreachable'` emit 후 clause 검사 skip. **try 블록보다 앞**에 둔다
     (그 안의 `except` 가 raise 를 삼켜 다시 internal-error 로 둔갑시키므로).
  3. `GuardianVerificationEngine._exit_env` 의 `related_variables` 폴백 → **`RuntimeError` (fail-loud)**.
     그 폴백은 `_get_post_exit_node` 가 없던 시절 "explicit return 함수의 EXIT 는 preds=0" 을 덮던
     응급처치(**Initial commit 962345c 유래** — 이 프로젝트에서 만든 게 아님)이고, 근본 원인은
     `_get_post_exit_node` 가 RETURN 을 고르면서 이미 해결됐다. 남겨두면 *도달 불가한* exit 를
     "선언 초기값 env" 로 덮어써 **vacuous verdict** 를 만든다. 9가지 함수 형태(빈 본문/revert만/단일대입/
     explicit return/named return ± explicit return/void)로 폴백 발동을 시도했으나 **0회** — 사실상 사문화.

  **부수 효과(의도된 개선)**: 직렬화가 `state={}` → `state={a:'⊥', …}` 로 바뀐다. 프론트
  `useNavigation.engineDead` 는 `⊥` 포함 여부로 dead 를 판정하는데 빈 state 에선 `.some()` 이 false 라
  **dead 노드를 못 잡고 있었다**(decision #35 가 `error_exit` 만 id 로 특수처리한 이유). 이제 자동 해결.

  **검증**: 신규 `temp/test_bottom_propagation.py` **13/13**
  (A. BOTTOM 이 EXIT 까지 전파 / B. @Post Unreachable + @During 과 일관 /
   C. ★ 부분 사망 시 `⊥ ⊔ live = live` 오염 없음 — if-분기 dead, 분기 *내부* require dead,
   양쪽 live join, early-return 분기 dead / D. 정상 verdict 회귀).
  기존 13종 **152/152** 전부 통과(회귀 0).

  **잔여**: `_make_bottom` 이 symbolic bytes(`msg.data`/`msg.sig`)엔 BOTTOM 표현이 없어 `None` 으로
  직렬화된다(표시만; 도달불가 env 안이라 무해). 프론트 `⊥` 표시 확인은 브라우저 육안 대기.

- **(43) back_edge 라벨링 — 프론트의 기존 필터가 무효였던 문제 ✅ (2026-09-18)**:

  **증상(잠복)**: `engine_api._serialize_cfg` 에 `# TODO(back_edge)` 만 있고 **어떤 엣지도
  `back_edge` 로 라벨링된 적이 없었다**. 그런데 프론트 `useNavigation` 은 이미 두 곳에서
  `e.kind !== 'back_edge'` 로 거르고 있었다 — `pred()`(= stepBackward) 와 applyTick 의
  `succOf()`(= run-to-breakpoint). **즉 필터가 6개월간 무효**였고, decision #4 의
  "back-edge 는 fixpoint 내부로 흡수되어 사용자 navigation 에 노출 안 됨" 이 안 지켜지고 있었다.
  (루프 안에서 backward 를 누르면 back edge 를 타고 루프 꼬리로 올라갈 수 있었다.)

  **판정 방식 = dominator**: `(u,v)` 가 back edge ⟺ **v 가 u 를 지배(dominate)**.
  자연 루프의 retreating edge 정의 그대로다. `nx.immediate_dominators(G, entry)` 로 idom 을 얻고
  u 의 dominator 체인에 v 가 있는지 확인한다(`engine_api._back_edges`).
  **엔진 flag(`fixpoint_evaluation_node`/`is_loop_body`)에 기대지 않는 것이 핵심** —
  while/for/do-while/중첩을 구분 없이 잡고, 플래그가 빠진 구조가 생겨도 안 깨진다.

  **우선순위**: back_edge 가 true/false 보다 우선한다(프론트가 이 라벨로 navigation 에서 제외하므로).
  실측 확인 — do-while 의 back edge 는 원래 `condition=None` 이라 **true/false 정보 손실 없음**.

  **검증**: 신규 `temp/test_back_edge.py` **13/13** —
  A. 루프 없는 CFG 는 back edge 0개(오탐 방지) + true/false 라벨 생존 /
  B. while·for·nested·do-while 각각 정확히 1/1/2/1개 /
  C. 루프 헤더로 되돌아감, **`for_init → for_join` 은 back edge 아님**(정상 진입) /
  D. 중첩은 서로 다른 헤더 2개 / E. 모든 엣지가 유효 kind.
  기존 14종 포함 **178/178** 통과(회귀 0).

  **★ 남은 것 = 이번 범위 밖(⑤ 루프 fold)**: 프론트 `succ()`(= `stepForward`/`continueRun`)는
  back_edge 를 **안 거른다**. 그래서 forward 는 여전히 back edge 를 타고 루프를 돈다
  (= decision #4 가 금지한 "iteration 별 stepping" 착각). 여기서 단순히 걸러버리면 루프 꼬리에서
  커서가 멈춰버리므로, **"루프 진입 시 fixpoint 로 접어 표시"(§6.4)와 함께 설계해야 한다.**
  라벨은 이제 있으니 ⑤ 착수 시 바로 쓸 수 있다.

- **(44) 함수 목록 API (`outline`) — 프론트 함수 목록이 mock 고정이던 문제 ✅ (2026-09-18)**:

  **증상**: `store.ts:111` 이 `mock/workspace.json` 의 `functions` 를 한 번 세팅하고 **어디서도
  갱신하지 않았다.** 엔진에도 "이 소스의 함수 목록" 을 내는 API 가 없었다(`Structure` 는 단일
  스코프의 CFG 만). → 사용자가 자기 `.sol` 을 열어도 함수 목록은 mock 의 `withdraw`/`deposit`.
  **임의 컨트랙트로 데모·평가·케이스스터디가 전부 막혀 있었다.**

  **추가**: `engine_api.FunctionOutline`(contract/name/signature/startLine/endLine/source) +
  `EngineSession.outline()`. `analyze` 는 스코프와 무관하게 record 를 전부 적용하므로
  (스코프는 `structure()` 에서만 쓰인다) **세션 하나로 모든 컨트랙트·함수를 열거**할 수 있다.
  제외: interface 컨트랙트(`ca.interface_names`, 본문 없음) · 본문 노드 0개인 함수.
  오버로딩은 프론트 세션 키가 `"contract.func"` 라 이름당 첫 시그니처만 낸다.

  **★ span 계산의 함정 두 개(둘 다 실측으로 드러남)**:
  1. **`line_info` 의 open/close 카운트를 쓰면 안 된다** — slice 가 `} else {` 의 닫는 괄호를
     `closeBefore` 로 옮겨서 **어느 라인에도 close 로 기록되지 않는다**(실측: `else {` 가
     `open=1, close=0`). balance 가 1씩 부풀어 withdraw 의 끝이 22 대신 27 로 나왔다(다음 함수를 삼킴).
  2. **`full_code_lines` 도 그냥 세면 안 된다** — 엔진이 저장하는 건 *정규화된* 텍스트라
     `} else {` 가 `else {` 로 들어있다. → `_prev_records` 의 `closeBefore` 로 라인별 close 를
     복원해서 센다. (`engine_api.EngineSession._function_span`)

  **서버**: `POST /outline {source} → {functions, contracts}`. 스코프가 정해지기 *전에* 부르는
  유일한 호출이라 세션 키(`"contract.func"`)와 분리돼야 한다 → **throwaway 세션**으로 만들고
  버린다(스코프별 세션 오염 방지). 대신 **소스 해시로 캐시**(타이핑마다 전체 재파싱 방지).
  실측: 1차 316ms → 2차 20ms.

  **프론트**: `analyzeClient.outline()` + `store.refreshOutline()`.
  `openFile`/`setActiveFile`/`updateActiveContent` 가 `runAnalysis` 대신 `refreshOutline` 을 타고,
  그 안에서 목록 갱신 후 structure 분석을 이어간다. 보고 있던 contract/func 이 아직 있으면 유지,
  없으면 첫 번째로. **함수 0개거나 서버 오류면 기존 목록을 지우지 않는다**(타이핑 도중 목록이
  사라지는 게 더 나쁨). `npx tsc --noEmit` 통과.

  **검증**: 신규 `temp/test_outline.py` **10/10** — mock 소스에서 `Vault.withdraw(13-22)`·
  `deposit(24-26)`(기존 하드코딩 startLine 13/24 와 일치), interface 제외,
  **if-블록/이중중첩으로 끝나는 함수**(다음 함수 안 삼킴), **`} else {}`·do-while**(closeBefore),
  다중 컨트랙트, interface-only → 빈 목록.
  HTTP e2e: `/outline` 정확 + 캐시 동작 + `/analyze` 무영향(세션 오염 없음) + 루프 소스에서
  `back_edge` 가 프론트 계약까지 도달 확인. 기존 15종 포함 **188/188**(회귀 0).

  **잔여**: 앱 최초 마운트 시점에는 아직 mock 시드 목록을 쓴다(초기 파일이 mock 소스라 일치).
  파일 열기/전환/편집 중 하나라도 일어나면 갱신된다. 필요하면 App 마운트 훅 추가.

- **(45) 의존성 pkl 전량 재생성 + 엔진 버그 25건 수정 + 평가 자산 보강 (2026-09-18 ~ 09-19)**:

  **배경**: 평가 자산을 Guardian 에서 동기화(소스 69개 신규, RQ1 케이스 27개)한 뒤 pkl 을 **전량
  재생성**했다. 예전 pkl 은 `analyze_file` 이 visitor 예외를 출력만 하고 넘어간 탓에 **함수 CFG 에
  statement 가 하나도 없는 채로** 저장돼 있었고, 그런 함수를 호출하면 크래시도 경고도 없이
  **선언된 리턴변수의 기본값을 반환**한다(조용한 오분석).

  **결과 (135개 소스 재생성)**: `[ERR]` **552 → 246(−55%)** · `[ANTLR]` 212 → 192 ·
  본문 있는 함수 380 → 384 · 본문 빈 함수 39 → 30 · pkl 134개(regen1 과 차집합 0).
  복구: `UnsafeMath.divRoundingUp`(stmts 0→1) · `DyDxMath.getDx/getDy`(0→2) ·
  `getLiquidityForAmounts`(0→5) · `FullMath.mulDiv`(→25). ← **케이스 35_H_08/35_H_10/35_H_12 가 의존**

  **수정 6건 + 배선 1건** (상세·Guardian 포팅 명세 = **`(to_solidityguardian)_engine_fixes.md`**):
  | | 내용 | 효과 |
  |---|---|---|
  | F3 | `SolidityAnalyzer` assembly 분기가 `find_function_context` **튜플 언패킹 누락** → `unhashable type: 'list'`. 6개 호출부 중 이 하나만 빠짐 | 131건 |
  | F4 | `visitLiteralExp` 가 **16진 리터럴만 signed**(`'uint' if isdigit() else 'int'`) → `absTick & 0x1` 타입 불일치, `uint256 ratio = 0xfff...` 가 signed 로 오염 | 156건 |
  | F5 | `EnhancedYulVisitor._build_literal` 이 **`expr_type` 미설정 + `literal` 을 int 로** 전달. 후자는 `int(txt,0)` 에서 `TypeError` 라 `except ValueError` 로 안 잡힘 → 둘 다 고쳐야 함 | 51건 |
  | F6 | `Evaluation` 에 **`& | ^` 디스패치 누락**. `Domain/Interval.py` 엔 `bitwise()` 가 이미 있었음(도메인 O, 배선 X). 단항 `~` 는 이미 배선됨 | 105건 |
  | F7 | `Dependencies/main.py` 분석 순서 하드코딩(`_late`/`_con_order`) → **위상정렬**(상속·라이브러리호출·using 추출, 순환 시 예외) | 9건 |
  | F8 | pkl 이름/모드를 `new_libs[0]`(**순서 의존**)로 정함 → **주 선언 기준**(선언 1개면 그것/여러 개면 파일명 일치/아니면 예외). F7 적용 시 `SafeTransferLib`→`con_ERC20.pkl(0 functions)` 회귀로 드러남 | 회귀 |
  | F9 | 소스: `from` 은 예약어 → `_from` (5파일 7곳). **문자열 리터럴 안의 `from` 4곳은 보존** | ANTLR 20건 |

  **★ 진단 순서가 중요했다**: F6 만 넣으면 105건이 "Unsupported operator" → "타입 불일치" 로 바뀔 뿐이고,
  F5 의 `expr_type` 만 고치면 51건이 `TypeError` 로 바뀔 뿐이다. 각 층을 벗겨야 다음 층이 드러났다.

  **신규 도구**: `Dependencies/check_pkl.py` — pkl 을 로드해 **본문이 빈 채 저장된 함수**를 보고.
  재생성 로그의 `[ERR]` 가 못 잡는 결과물 쪽 손상을 확인한다.
  ⚠ 판정 기준이 거칠다(OZ 훅·modifier 를 손상으로 오분류) — 소스 대조로 개선 필요.

  **잔여 246건**: `None`(메시지 없음) 59 · `may not be declared` 52 · `cannot be declared as constant` 24 ·
  `No active function CFG` 류 38 · `not defined as struct/enum/alias` 17 · Shift 10 · 기타 46.
  파일별: TickMath 41 · Controller 31 · PRBMathSD59x18 30 · CommonLibrary 14.

  **(이어서) F10 — Yul 비교가 `BoolInterval` 을 냄**: Yul 엔 bool 이 없고 `lt/gt/slt/sgt/eq/iszero` 는
  **0/1 word** 다. `_BINARY_OPS` 가 `'gt'→'>'` 로만 매핑해 `shl(7, gt(r,C))` 이 Shift dispatch 에서
  거부 → `let f` 선언 실패 → 뒤 두 줄이 "'f' may not be declared" 로 연쇄(3줄 × 8회 = 40건).
  수정 = 비교 결과를 `cond ? 1 : 0` 삼항으로 감싸 uint word 화(`_as_word`). 삼항 평가기가
  `[1,1]`/`[0,0]` 이면 정확히 접으므로 **정밀도 손실 없음**. Yul 경로 한정(Solidity 에선 bool 이
  산술에 못 쓰이므로 승격하면 진짜 타입 오류를 덮는다).
  → `[ERR]` 246 → **224**. TickMath 41 → 19.
  **`TickMath.getSqrtRatioAtTick`(stmts 0→23)·`getTickAtSqrtRatio`(stmts 0→69) 본문 복구** —
  Guardian 주석이 지목했던 바로 그 함수들. 증가한 파일 0개.

  **영향 범위 확인**: 잔여 224건 중 **188건이 평가 케이스가 참조하는 파일**에 있다(76%).
  TickMath 19→3케이스(35_H_08/10/12) · Ticks 7→4케이스 · Controller 31→1 · PRBMathSD59x18 30→1 ·
  CommonLibrary 14→1. 인프라 다듬기가 아니라 평가에 직접 영향.

  **(이어서) F11 — 시프트 추상 의미론이 불건전했다 ★코어 · 전수 검증 (2026-09-18)**:
  TickMath L131 `int256 log_2 = (int256(msb) - 128) << 64;` 가 "양쪽 타입 일치" 요구로 거부됐다.
  시프트는 산술이 아니다 — 좌변=피시프트값/우변=시프트양으로 **역할이 다르고**, 시프트 양은
  부호 없는 타입이며 좌변은 signed 여도 된다. 타입 검사를 걷어내려다 기존 공식 자체를 점검했더니
  **불건전**이었다: `[xlo<<slo, xhi<<shi]` 는 음수 좌변에서 구간이 역전된다
  (`[-8,-8] << [0,3]` → `(-8,-64)`). w=4/5 **전수 검증** 결과 구 공식은
  **signed 49~53% · unsigned 41~44%** 의 조합에서 참값을 포함하지 못했다.
  수정 = 4코너 min/max + 오버플로 가능 시 **TOP**(포화 아님 — Solidity 시프트는 wrap 한다),
  `'<<'` 는 `s.max ≥ w` 면 즉시 TOP, `'>>'` 는 `s` 를 `w` 로 캡. 새 공식은 전수 검증 **불건전 0건**.

  **(이어서) F12 — 산술 결과가 타입 범위를 벗어나 비트수가 폭주 ★코어**:
  `PRBMathSD59x18.sol` 재생성이 **끝나지 않았다**. 처음엔 시프트를 의심했으나 F11 로 해결되지
  않았고, `faulthandler` 스택 덤프로 `Interval._mul ← multiply ← fixpoint` 를 잡았다.
  사용자 지적("mul 은 다 곱해보고 min/max 정하면 끝 아니냐")이 정확했다 — **연산자는 옳고**,
  문제는 `IntegerInterval(lo,hi,w)` 가 `w` 를 받기만 하고 **경계를 강제하지 않는** 것이었다.
  결과가 도메인을 탈출한 채 다음 연산의 피연산자가 되어 fixpoint 의 `x = x*x` 에서
  비트수가 매 반복 2배(실측 255 → 511 → 1021 → … → **65,281비트**).
  수정 = `_fit_signed`/`_fit_unsigned`(벗어나면 TOP) 를 signed add/subtract/multiply/exponentiate,
  unsigned add/multiply/exponentiate 에 적용 + `_pow_fits`(거듭제곱을 **계산하지 않고** 비트수로 판정).
  ⚠ **unsigned `subtract` 는 그대로 둔다** — "항상 음수면 bottom"(underflow revert → 도달불가) 이
  checked 의미론으로 load-bearing 이다.
  → 정지 해소, `[ERR]` 246 → **212**. PRBMathSD59x18 30→23, TickMath 19→14.

  **(이어서) F13 — Yul 비트 연산 타입 불일치 = 2의 보수 word 재해석 ★코어 (2026-09-19)**:
  `log_2 := or(log_2, shl(63, f))` (TickMath 14건 + PRBMathSD59x18 1건).
  **Yul 엔 타입이 없다** — 모든 값이 raw 256-bit word 이므로 위 코드는 합법인데,
  Solidity 선언(`int256 log_2`)의 타입이 Yul 블록까지 따라 들어와 F6 의 타입 검사에 걸렸다.
  그 검사는 **Solidity 경로에선 유지돼야 하므로**(`int & uint` 는 진짜 타입 오류) 검사를 없애는 게
  아니라 **경로를 구분**하는 것이 옳다 → `EnhancedYulVisitor` 가 컨텍스트에 `Yul` 접두를 달고
  (`YulBinaryOperationContext` 등 7곳), `Evaluation._is_yul_ctx` 로 감지해 `_reinterpret_as` 적용.
  부호 비트가 구간 안에서 안 변하면 값 범위가 동일해 **손실 없음**, 변하면 TOP(건전).
  → `[ERR]` 212 → **198**, TickMath **14 → 0**.
  ⚠ 자초한 사고: 이 헬퍼를 `class Evaluation` **본문 안에** 컬럼 0 `def` 로 넣어 클래스 본문을
  끊어먹었다 → `[ERR]` 212→**1303**(970건이 `'Evaluation' object has no attribute ...`).
  모듈 레벨 헬퍼는 반드시 `class` 선언 **앞**. 검증은 `ast` 로 메서드 수(55) 확인.

  **(이어서) F14 — Solidity 숫자 리터럴의 문맥 타이핑 ★코어**:
  F13 후 남은 마지막 1건 — `PRBMathSD59x18.sol:27` `result = (x>>1) + (y>>1) + (x & y & 1);`
  (`int256 x, y`). **Yul 이 아니라 Solidity 경로**다. 원인은 **F4 의 필연적 귀결**:
  Solidity 숫자 리터럴은 **타입 없는 유리수**이고 타입은 문맥(상대 피연산자)이 정하는데,
  F4 가 모든 숫자 리터럴을 uint256 으로 못박았다(그 자체는 옳다 — 음수 리터럴은 없다).
  빠진 것은 **사용 시점의 문맥 타입 부여**. 수정 = 비트 분기에서 타입이 엇갈릴 때 한쪽이
  숫자 리터럴이면 상대 타입으로 재해석(`_literal_retype`). 단일 상수라 범위에 들어가면
  **정확히 같은 값**이고, 벗어나면 폴백 없이 예외(실제 Solidity 도 컴파일 에러).
  산술(`+ - * / % **`)은 타입 검사 자체가 없어 실패 사례가 없으므로 **건드리지 않았다**.
  → `[ERR]` 198 → **197**, 비트/시프트 타입 불일치 **전부 0건**.
  `PRBMathSD59x18.avg` 본문 복구(본문 있는 함수 384→**385**, 빈 함수 30→**29**).

  **누적 (regen16 시점, 아래 F15/F16 뒤에 갱신)**: `[ERR]` **552 → 197(−64%)** · `[ANTLR]` 212 → 192 ·
  pkl 134(regen1 과 차집합 0) · 본문 있는 함수 380 → **385** · 빈 함수 39 → **29** ·
  각 단계에서 **증가한 파일 0개**.
  잔여 197: `None` 59(인터페이스, 원인 불명) · `cannot be declared as constant` 24 ·
  `No active function CFG` 류 39 · `not defined as struct/enum/alias` 17 ·
  `may not be declared` 16 · 기타 42. 파일별: Controller 31 · PRBMathSD59x18 22 · CommonLibrary 14.
  **비트/시프트 계열은 소진됐고, 남은 197건은 전부 선언·CFG 해석 계열이다.**

  **(이어서) F15 — 인터페이스 body 의 event 가 null 컨텍스트로 새어나감 (2026-09-19)**:
  `[ERR] L4 ctx=None: None` **59건**(인터페이스 15개)의 정체. 메시지가 `None` 인 이유는
  `str(KeyError(None))` 이 문자 그대로 `'None'` 이기 때문이었다.
  원인: `analyze_context` 의 인터페이스 분기가 `function ` 이 아니면 `return` 으로 "스킵" 하는데
  **그 스킵이 호출자에게 전달되지 않는다** — 앞에서 이미 `current_target_contract` 를 None 으로
  초기화한 뒤 그냥 돌아올 뿐이고, 드라이버는 `ctx is None` 을 확인하지 않아 그대로 파싱·visit 을
  계속한다. `event` 는 문법적으로 멀쩡하니 파싱에 성공 → `process_event_definition` 의
  `contract_cfgs[None]` → `KeyError: None`.
  수정 = contract/library 분기엔 이미 있는 `event` 처리를 인터페이스에도 맞춤.
  `else: return` 은 유지(전수 조사 결과 이 코퍼스 도달 0건 — 슬라이서가 여러 줄 함수 시그니처를
  한 레코드로 합치므로 연속 줄이 오지 않는다).
  → `[ERR]` 197 → **138**, `None` **59 → 0**, 증가한 파일 0개.
  ⚠ **정직하게**: 분석 결과는 안 바뀐다. `ccf.events` 는 아무도 읽지 않고(`process_emit_statement`
  는 `pass`), 본문 있는 함수 385 / 빈 함수 29 로 전후 동일. **가짜 경보 제거**이지 정확도 향상이 아니다.
  다만 "스킵 의도가 호출자에게 전달되지 않는" 구조 자체가 CLAUDE.md 가 경고하는 조용한 오분석
  패턴이라 고쳐두는 게 맞다.

  **(이어서) F16 — `bytesN` constant 전부 거부 + 문자열 리터럴 정확 인코딩 ★코어**:
  `Type 'bytes32' cannot be declared as constant: 'GOVERNANCE'` **24건**.
  원인 3층:
  (1) 허용 목록의 `"bytes"` 가 **동적** bytes 만 매치해 `bytes1..bytes32` 가 전부 거부.
      `BytesSet` 도메인과 `evaluate_literal_context` 의 bytesN 분기는 **이미 있었다** — 게이트만 막음.
      선언이 거부되면 `register_var` 까지 못 가 이 상수를 읽는 코드가 연쇄 실패한다.
  (2) F14 와 같은 원리 — 리터럴엔 타입이 없는데 선언 타입이 전달되지 않음. 실측 분포:
      문자열 19(`expr_type='string'`) · 16진 3(`'uint'`) · `keccak256(...)` 2(`FunctionCallContext`).
      ⚠ **bytesN 에만** 덮어쓸 것 — `uint256` 으로 덮으면 `evaluate_literal_context` 가
      `'uint256'` 을 모른다(실측). keccak 은 이미 `BytesSet.top(32)` 로 옳게 평가된다.
  (3) bytesN 분기에 문자열 리터럴 인코딩이 없어 ⊤ 로 떨어짐.

  **★ 건전성 검증이 이 수정의 핵심**: 상수의 구체 값 집합은 **싱글턴**이라 값이 정확하면
  `BytesSet({v})` 가 가장 좁은 건전한 추상화지만, **틀리면 불건전**(잘못된 definite 판정)이다.
  그래서 인코딩을 추측하지 않고 **실증**했다 —
  `Dataset/Web3Bugs/S3_2/contest_145_H_02/registry_ReverseRegistrar.sol:12` 의 실제 ENS 상수
  `bytes32 constant lookup = 0x30313233…6600…00` 과 `byte(and(addr,0xf), lookup)` 사용이
  "UTF-8 바이트를 **상위 바이트부터** 배치, 하위를 0 패딩"(left-aligned)을 증명한다
  (Yul `byte(n,x)` 는 MSB 기준). 엔진 계산값이 이 상수와 **비트 단위 일치**함을 확인.
  대상 19개 문자열 전수 검사: 이스케이프 0 · non-ASCII 0 · 길이 초과 0.
  미모델링 형태(이스케이프 / `hex""` / `unicode""` / 길이 초과)는 **raise** — ⊤ 로 덮으면
  정밀도만 잃는 게 아니라 잘못된 경로를 믿게 된다.

  **왜 ⊤ 가 아니라 싱글턴이어야 하나**: `BytesSet.equals` 는 `is_top` 이면 **무조건**
  `BoolInterval(0,1)`(Unknown → Warning). 이 상수들은
  `require(hasRole(ROLE_ADMIN, msg.sender))` · `onlyRoles2(Roles.GOVERNANCE, ...)` ·
  `currentUInts256[_KEEPER_REQUIRED_STAKED_BKD]`(**mapping key**) 로 쓰이므로
  ⊤ 이면 verdict 가 전부 Warning 이 되고 mapping 슬롯도 특정 못 한다. **verdict 품질** 문제다.
  → `[ERR]` 138 → **112**, `cannot be declared as constant` **24 → 0**, 증가한 파일 0개,
  빈 함수 29 → 28, `temp/` 회귀 7/10/7 통과.
  ⚠ 범위: `constant` 한정. 일반 state var(`bytes32 x = "plain"`)은 여전히 원문 str —
  별건의 잠재 문제로 남김(현재 오류 없이 동작 중이라 건드리면 값이 조용히 바뀐다).

  **(이어서) F17 — 상속 체인 1단계 탐색 + intent 문법의 `ceil`/`floor` 예약 (2026-09-19)**:
  `No active function CFG` 계열 **40건**의 정체. 원인이 **두 갈래**였다.
  ⚠ 먼저 확인한 것: **레코드 슬라이싱은 전부 의도대로였다** — 여러 줄 함수/modifier 시그니처,
  여러 줄 `require(...)` 가 모두 한 레코드로 정확히 병합된다. 변환 단계 문제가 아니다.

  **(A) 상속 체인 전이적 탐색 — Controller 20건**
  체인 `Controller is ... Authorization ...` → `Authorization is AuthorizationBase` →
  `modifier onlyGovernance` 는 **AuthorizationBase** 에 있다. 두 군데가 얕았다:
  ① `process_modifier_invocation` 이 `parent_contracts` 를 **1단계만** 순회하고
     `ContractCFG.get_function_cfg` 도 부모 체인을 안 탄다.
  ② `Dependencies/main._load_parent_pkls` 는 docstring 에 **"(재귀)"** 라 써 있지만 실제로는
     직계 부모만 로드했다(실측: Controller 분석 시 AuthorizationBase 누락).
  modifier 해석 실패는 **함수 정의 자체를 중단**시켜 본문 전체가 연쇄 실패한다
  (`Controller` 등록 함수 **0개**).
  수정 = `ContractAnalyzer._find_in_ancestors` (BFS; 조상 CFG 를 `parent_cfgs` + `contract_cfgs`
  양쪽에서 얻음) + `_load_parent_pkls` 워크리스트 재귀화.
  `con_Authorization.pkl` 이 `parent_cfgs['AuthorizationBase']` 를 **객체로 품고 있어서**
  조회만 고쳐도 도달한다 — 재귀화는 `contract_cfgs` 를 직접 보는 경로를 위한 독립 보완.
  ⚠ 탐색 순서는 **선언 순서**다. Solidity C3 는 `is A, B` 에서 B 가 더 파생이라 역순이어야
  한다 — 현 코퍼스엔 조상 간 이름 충돌이 없어 무해하지만 다이아몬드 상속에선 위험. **별건**.
  **부수 확정**: `onlyGovernance` CFG 는 정상이다(nodes=7 + `cond_expr`). `require` 가 조건
  노드로 모델링되므로 `statements=0` 이 맞다 → **`check_pkl.py` 판정 기준 오류가 확정**됐다.

  **(B) intent 문법이 `ceil`/`floor` 를 예약어로 만듦 — PRBMath 19건**
  `[ANTLR] no viable alternative at input 'functionceil'`.
  `Parser/Solidity.g4:323-324` 의 `'ceil'`/`'floor'` **인라인 리터럴**이 ANTLR 암묵 토큰이 되고,
  그 토큰은 `Identifier` 보다 **우선**한다 → **모든 Solidity 소스에서 식별자로 못 쓴다**.
  IntentChecker 술어 문법이 일반 Solidity 파싱을 깨뜨린 것이다.
  충돌 규모(리포 전체 `.sol` **3,984개** 스캔): `floor` 24 · `ceil` 9 · `array` 1 ·
  나머지 17개 예약어(`PercentOf` `changed` `Before` `After` `Entry` `Exit` …) **0건**.
  **해법 = 소스 rename (사용자 결정)**: `ceil`→`_ceil`, `floor`→`_floor`.
  **정의 4줄, 2파일**뿐(`PRBMathSD59x18.sol:31,107` · `PRBMathUD60x18.sol:20,58`),
  호출자 0곳, RQ1 케이스 JSON 영향 0건. `evaluation/RQ1/.../dependencies/` 사본은 **보존**
  (`Dependencies/main.py` 는 `Dependencies/` 만 스캔하고, 루트 `load_dependencies()` 는 그
  디렉토리를 `interface\s+(\w+)` 정규식 이름 수집에만 쓴다 — 파싱하지 않음).
  ⚠ **문법은 고치지 않는다(사용자 결정)** — intent 문법은 IntentChecker 논문에 실린 것이다.
  따라서 **도구 한계로 남는다**: 사용자가 에디터에 `function floor(...)` 를 치면 파싱이 깨진다.
  `from`(F9) 과 성격이 다르다 — `from` 은 Solidity 자체 예약어라 원본이 잘못이었지만
  `ceil`/`floor` 는 **유효한 Solidity** 이고 우리 문법이 잘못이다. rename 은 증상 대응이다.

  → `[ERR]` 112 → **76**, `No active function CFG` 계열 **40 → 1**, `[ANTLR]` 192 → **188**,
  증가한 파일 0개, 본문 있는 함수 385 → **391**.
  Controller 28→11 · PRBMathSD59x18 22→4 · PRBMathUD60x18 5→4.
  함수 등록 복구: `con_112_Controller` **0→8개**(본문 6) ·
  `lib_PRBMathSD59x18` 22개(본문 21) · `lib_PRBMathUD60x18` 21개(본문 20).
  `temp/` 회귀 7/10/7 통과.
  ⚠ 빈 함수 28→30(+2)는 회귀가 아니라 **함수가 6개 더 생겨서**다(새 Controller modifier 들이
  위 잘못된 판정에 걸림). Controller 의 `may not be declared` 가 늘어난 것도, 전엔 본문이
  **아예 분석되지 않아** 안 보이던 진짜 선언 문제가 드러난 것이다(파일 총계는 28→11).

  **(이어서) F18 — Phase 1 위상정렬 누락 + file-level enum 미수집 (2026-09-19)**:
  `Type 'X' is not defined as struct, enum, or type alias` **17건**을 분해하니 **네 갈래**였다 —
  (A) 의존성 자산 누락 9 · (B) Phase 1 위상정렬 누락 2 · (C) file-level enum 5 · (D) `Roles` 동명이인 1.
  이 중 엔진 결함인 (B)(C) 를 고쳤다.

  **(B) F7 이 interfaces 를 빠뜨렸다**: Phase 2(libraries)/Phase 3(contracts) 는 `_topo_order`
  를 쓰는데 **Phase 1 만 `sorted(rglob)` 알파벳순**이었다. `IPooledCreditLine is
  ...Declarations is ...Enums` 인데 알파벳순은 정확히 **반대**라, 부모 분석 시점에 조상 pkl 이
  없어 `parent_cfgs` 가 **빈 채로** 저장됐다(실측: 인터페이스 pkl 전부 `parent_cfgs=[]`).
  `EnhancedSolidityVisitor._find_in_chain` 은 `parent_cfgs` 를 재귀로 도는 **올바른 코드가 이미
  있었는데** 돌 것이 비어서 무용지물이었다. 수정 = Phase 1 도 `_topo_order`
  (IFC_DIR 73개 전수 확인: 주 선언이 모두 interface 라 모드 불일치 위험 없음).
  → 부모가 있는 interface pkl **14개 중 0개 → 12개**의 `parent_cfgs` 가 채워졌다
  (나머지 2개는 부모 `.sol` 자체가 없는 (A) 케이스).

  **(C) file-level enum 은 수집·주입·해석 3단이 모두 없었다**: `enum AssetClass {...}` ·
  `enum Status {...}` 는 파일 최상위 enum(Solidity 0.8+ 허용)이다.
  `SolidityAnalyzer.file_level_enums` **필드도 소비처도 이미 있는데**
  (`Evaluation.py:1090`, `Refine.py:190`) ① `Dependencies/main.py` Phase 0 에 수집이 없고
  (루트 `main.py`·`engine_api.py` 는 regex 로 수집 — **pkl 경로만 누락**)
  ② `analyze_file` 주입도 없고 ③ `EnhancedSolidityVisitor` 타입 해석 체인에 분기도 없었다
  (`file_level_structs` 분기는 있음; `Evaluation.py:1090` 은 *식별자 평가* 전용이라
  *타입 선언* 에는 안 쓰인다).
  수정 = `scan_file_level_enums` 신규 + 주입 + 해석 분기.
  ⚠ typeCategory 만 세우면 하류의 `ccf.enumDefs.get(...)` 에서 `undefined enum` 으로 터지므로
  해석 분기에서 `EnumDefinition` 을 만들어 `contract_cfg.enumDefs` 에 등록한다
  (검증: regen20 에서 `undefined enum` **0건** — 에러를 옮기기만 한 게 아니다).

  → `[ERR]` 76 → **69**, `not defined as struct/enum/alias` **17 → 10**
  (해소 7 = B 2 + C 5, 남은 10 = A 9 + D 1 — 예상과 정확히 일치).
  증가한 파일 0개, pkl 134, `[ANTLR]` 188 동일, 본문 있는 함수 391 동일.
  `temp/` 회귀 7/10/7 통과.

  **(이어서) F19~F21 + 평가 자산 보강 (2026-09-19)**:

  **자산 보강** — 누락 인터페이스 **9종**을 `Web3Bugs/contracts/` 에서
  `Dependencies/interfaces/` 로 복사(`IVMochi` `IUSDM` `IFeePool` `ILiquidator`(42 mochi-core) ·
  `IInflationManager`(112 backd) · `IVaultRegistry` `IVaultFactory`(58 mellow-vaults) ·
  `IERC721` `IERC165`(OZ)). `IERC721` 의 `from` 4곳은 `_from`(F9 규칙).
  → `not defined as struct/enum/alias` **17 → 1**, pkl 134 → 143.
  ⚠ **블록 주석이 슬라이서를 깨뜨린다(신규 발견)**: OZ NatSpec `/** */` 이 뒤따르는 선언과
  **한 레코드로 합쳐져** `analyze_context` 의 `startswith(...)` 분류가 전부 빗나간다
  (IERC721 = `[ERR]` 12 + `[ANTLR]` 25). 문법엔 `COMMENT -> channel(HIDDEN)` 이 있으니
  **파서가 아니라 슬라이서/분류기 문제**다. 기존 144개 중 블록 주석이 있던 건 복사한 3개뿐이라
  여태 안 드러났다. 그 3개에서 제거해 우회했으나 **사용자가 에디터에 NatSpec 을 치면 재발**한다.

  **전처리 툴킷 확보** — `SolidityGuardian/temp/` 에서 `IntentKeeper/tools/` 로 9개
  (`preprocess_contraction.py` 8단계, `rename_reserved_identifiers.py`, `generate_case_jsons.py`,
  `PROCESS_GUIDE{,_v2}.md`, `code_modification_issues.md` 등). 사용자가 기억하던 그 전처리다 —
  `expand_single_line_blocks`(`if (c) stmt;` → 블록), `find_block_comment_ranges`,
  `rename_reserved_params`(`from`→`_from`, `float`→`FloatStruct`), `collapse_enum_members` 등.
  드라이런 결과 **기존 코퍼스는 이미 전처리를 거친 상태**(예약어 0 · bare scope 0)였고,
  잔여 2단계(enum 합치기 4파일 · single-line if 확장 1파일)를 적용했으나 오류 변화 없음.
  `PROCESS_GUIDE_v2.md` 에 **"Dependencies/main.py pkl 생성 시 분석 순서"** 이슈가 이미
  문서화돼 있었다 — F7/F18/F19 가 정확히 그 항목이다.

  **F19 — 타입 참조 pkl 로드 + 위상정렬 반영 (도구)**:
  `contract RoleAware { Roles public immutable roles; }` 처럼 **상속하지 않고 타입으로만 쓰는**
  컨트랙트가 해석 안 됨. 원인 두 겹 — ① `_refs_in` 이 타입 참조를 안 봐서 순서가 뒤집히고
  (`RoleAware` 13 < `contracts/Roles.sol` 14) ② 분석된 contract CFG 가 다음 파일에 주입되지 않음
  (인터페이스엔 `ca.interface_names` 가 있지만 **contract 엔 그런 레지스트리가 없다**).
  수정 = `_type_refs_in`/`_load_pkl_for`(후보 **정확히 1개일 때만**)/`_load_type_ref_pkls` +
  `_topo_order` 안에서만 `_refs_in | _type_refs_in`. 순환 위험은 3개 Phase 전수 검증(없음).
  → **단독 효과 0건.** 진짜 원인은 F20 이었고, regen 이 안 변한 것이 F20 을 찾게 된 단서였다.

  **F20 — `make_contract_cfg` 가 동명 LibraryCFG 를 재사용 ★**:
  ```python
  if contract_name in an.contract_cfgs:
      return an.contract_cfgs[contract_name]   # 종류를 안 따짐
  ```
  `analyze_file` 의 호환성 주입(`contract_cfgs.update(_global_library_cfgs)`)이 **다른 프로젝트의
  동명 라이브러리**를 먼저 넣어두면 contract CFG 를 만들 때 그 LibraryCFG 를 그대로 돌려주고,
  contract 함수들이 그 안에 쌓여 pkl 로 저장된다. 실측: `contracts/112/Roles.sol`(library, 순번 0)
  때문에 `con_Roles.pkl` 이 **LibraryCFG 인데 내용은 contract 함수 4개**.
  `ContractCFG`/`LibraryCFG` 는 상속 관계 없는 형제라 모든 `isinstance` 검사를 통과 못 한다 —
  타입 해석이 계속 실패한 진짜 이유. 수정 = 재사용을 `isinstance(_existing, ContractCFG)` 로 한정.
  → `[ERR]` 54 → **49**, `RoleAware.sol` **5 → 0**, `con_Roles.pkl` ContractCFG 로 정상화,
  본문 있는 함수 392 → **396**, 빈 함수 29 → **25**.
  ⚠ **소스 rename 은 불필요했다** — 이름 충돌이 아니라 객체 종류 검사 누락이었다.
  평가 자산을 건드리지 않고 해결.

  **F21 — 배열 원소 생성에서 중첩배열/mapping/enum 누락 ★코어**:
  `CommonLibrary.splitAmounts` **14건**이 `require(weights[i].length == n)` 한 줄에서 연쇄.
  배열 원소를 만드는 **두 분기가 base 타입을 손으로 나열**하는데 `uint/int/bool/address/struct`
  만 있고 **중첩배열·mapping·enum 이 빠져** 마지막 `else` 로 떨어져 **문자열**을 돌려줬다.
  세 번째 지점은 별개 — **리터럴 인덱스가 `get_or_create_element` 를 우회**해
  길이 미상 파라미터의 `w[0]` 이 auto-push 없이 IndexError.
  **올바른 구현은 이미 있었다**: `_create_new_array_element`(중첩 Array/Mapping/Enum 생성) ·
  `_create_element_virtual` · `get_or_create_element`. `DebugInitializer` 는 이미
  `isDynamicArray` 분기 패턴을 쓰고 `Evaluation.py:510` 도 헬퍼를 쓴다 — **세 지점만 누락**이었다.
  수정 = 세 곳을 헬퍼로 위임. 원소 값은 `_create_default_value` 의 **⊤** 라 건전하고,
  문자열을 돌려주던 기존 동작보다 **엄격하게 정확**하다(문자열은 추상값이 아니다).
  정적 배열 범위 검사와 정상 경로는 그대로.
  → `[ERR]` 49 → **35**, `CommonLibrary.sol` **14 → 0**.

  **누적 (regen26, 2026-09-19)**: `[ERR]` **552 → 35(−94%)** · `[ANTLR]` 212 → 187 ·
  pkl 134 → **143** · 본문 있는 함수 380 → **396** · 빈 함수 39 → **25** ·
  각 단계에서 **증가한 파일 0개** · `temp/` 회귀 7/10/7 통과.
  잔여 35: `Unsupported expression context` 6 · `Member not in struct` 6 ·
  `No CFG node found after line N` 6 · `Unable to find contract CFG for None` 4 ·
  `object has no attribute` 3 · `Cannot resolve mapping key` 3 · 기타 7.
  파일별: Ticks 7 · Float 4 · PRBMathSD59x18 4 · PRBMathUD60x18 4 · Controller 4 ·
  ERC1155Upgradeable 3.

  **(이어서) F22·F23 — 지역 struct 정의 부착 + index-access 멤버접근 키의 매핑 조회 (2026-09-19)**:
  `Member 'X' not in struct 'X'` 6건(Ticks 4 + AddressProviderMeta 2).
  `Ticks.Tick storage previous = ticks[current.previousTick];` 다음 줄에서 터졌다.

  **F22 (기여 0, 그러나 실재하는 결함)**: `_create_variable_object` 의 2-A(배열)·2-D(매핑)는
  `get_full_struct_enum_defs()` 로 정의를 넘기는데 **2-B(struct)·2-C(enum) 만 아무것도 안 했다**.
  정의를 붙이도록 고치고 2-D 의 인라인 중복도 헬퍼로 교체.
  ⚠ regen 은 35→35 로 **변화 없음** — 목표였던 Ticks 는 `process_variable_declaration:1341` 의
  `v = VariableEnv.deep_clone_variable(resolved, var_name)` 가 **방금 만든 v 를 버리고
  초기화식이 준 객체를 쓰기** 때문이었다. 초기화식 **없는** 지역 struct 선언에는 여전히 필요하므로 남긴다.
  "효과 0" 이라는 사실이 F23 을 찾게 한 단서였다(F19 때와 같다).

  **F23 ★코어**: `evaluate_index_access_context` 는 인덱스 접근을 **인덱스 식 평가에 위임**하고
  `callerObject=매핑` 을 넘겨 인덱스 식 핸들러가 매핑 조회까지 마치도록 설계돼 있다.
  리터럴 키·식별자 키는 그 처리를 하는데 **멤버 접근 키만 빠져 있었다** — 매핑을 버리고
  멤버 값만 돌려줬다. 실행 추적으로 확인: `ticks[current.previousTick]` 평가 중
  `get_or_create`/`_create_value_virtual` 이 **한 번도 호출되지 않는다**.
  그 int24 값이 `resolved` 가 되어 `isinstance(resolved, StructVariable)` 분기를 못 타
  빈 struct 가 남았다. 수정 = 이미 20곳 이상에서 쓰이는 `_mapping_lookup_if_needed` 를
  struct 멤버 접근 반환부 3곳에 붙임. 이 헬퍼는 callerObject 가 매핑이 아니면 **그대로 통과**해
  기존 경로에 영향이 없고, 매핑이면 `get_or_create` → `_make_value` 로 **정의가 붙은 struct** 를 만든다.
  → `[ERR]` 35 → **31**, `Member not in struct` **6 → 2**, Ticks 7→3.
  ⚠ AddressProviderMeta 2건은 **그대로 남았다**(첫 보고의 `2→0` 은 상위 N개 집계에 가린 오기 — 정정),
  증가한 파일 0개, 회귀 7/10/7 통과.

  ★ **F16·F21·F22·F23 가 모두 같은 패턴이다** — 올바른 구현이 이미 도메인/헬퍼에 있는데
  특정 경로에서만 쓰지 않는다. 남은 오류를 볼 때 **"이 경로가 기존 헬퍼를 쓰고 있나"** 를
  먼저 확인하는 것이 빠르다.

  **누적 (regen28)**: `[ERR]` **552 → 31(−94%)** · `[ANTLR]` 212 → 187 · pkl 143 ·
  본문 있는 함수 380 → 396 · 빈 함수 39 → 25 · 각 단계에서 증가한 파일 0개.
  잔여 31 파일별: Float 4 · PRBMathSD59x18 4 · PRBMathUD60x18 4 · Controller 4 · Ticks 3 ·
  ERC1155Upgradeable 3 · IMochiProfile 2 · IMochiVault 2 · 기타 5.


  **(이어서) F24 — 존재하지 않는 컨텍스트 문자열 `IndexAccessExpContext` ★조용한 오분석**:
  `Cannot resolve mapping key` 3건(Ticks). 같은 모양인데 어떤 줄은 되고 어떤 줄은 안 됐다.
  문법 라벨이 `# IndexAccess`(Solidity.g4:596) 라 생성 클래스는 **`IndexAccessContext` 하나뿐**이고
  `IndexAccessExpContext` 는 어디에도 없다(비슷한 라벨 주의: `# IntentIndexAccess` L385 intent 절,
  `# IReturnIndexAccess` L296 @IReturn — visitor 도 셋을 구분한다).
  따라서 `Update.py:327` 의 키 추출 블록이 **한 번도 실행된 적이 없고**, 매핑 쓰기는 오직
  `엔트리가 하나면 그걸 사용` 폴백으로만 동작했다. L66 은 우연히 맞았고 L85 는 엔트리가 늘어 실패.
  ⚠ **오류 3건보다 위험한 것**: 폴백이 성공할 때도 키를 검증하지 않아
  `ticks[A]` 에 쓰는 코드가 `ticks[B]` 를 **조용히** 수정할 수 있었다.
  `Refine.py` 2곳도 같은 오타이고 그건 "심볼릭 인덱스면 refine 건너뛰기" **건전성 가드**였다.
  수정 = 3곳 문자열 교정.
  → `[ERR]` 31 → **28**, `Cannot resolve mapping key` **3 → 0**, `Ticks.sol` **3 → 0**(오류 0),
  증가한 파일 0개, 회귀 7/10/7 통과.
  ⚠ 폴백 ②는 아직 남아 있다 — 도달 빈도를 재고 제거를 검토할 것.

  **누적 (regen29)**: `[ERR]` **552 → 28(−95%)** · `[ANTLR]` 212 → 187 · pkl 143 ·
  본문 있는 함수 396 · 빈 함수 25 · 각 단계에서 증가한 파일 0개.
  잔여 28 파일별: Float 4 · PRBMathSD59x18 4 · PRBMathUD60x18 4 · Controller 4 ·
  ERC1155Upgradeable 3 · IMochiProfile 2 · IMochiVault 2 · AddressProviderMeta 2 · 기타 3.


  **(이어서) F25 — 전처리에 '빈 블록 `{ }` 확장' 단계가 없었다 (도구, 부분 해소)**:
  OZ storage gap 이 **함수 바로 뒤**에 올 때 `ctx=simpleStatement` 로 오분류되는 3건.
  원인은 `find_function_context` 가 위로 거슬러 올라가며 "남은 첫 `{`" 를 찾을 때
  **한 줄에 `{`/`}` 가 같이 있으면 `open>0` 에서 곧바로 멈추고 같은 줄의 `close` 를 무시**하는 것.
  → 이미 닫힌 함수를 "열려 있다" 고 착각한다(실측: 7개 대상 중 틀리는 3건이 오류 3건과 일치).
  전처리에 빈 블록 확장 분기를 추가하고 소스 8곳에 적용.
  ★ `} else {` / `} else if (...) {` 류는 **절대 제외** — `}` 가 먼저 와서 이전 블록을 닫는
  것이고 슬라이서 `closeBefore` 가 전담한다. 드라이런으로 대상 10건 / 제외 101건 전수 확인.
  → `[ERR]` 28 → **26**, `[ANTLR]` 187 → **183**, 증가 0, 회귀 7/10/7 통과.
  ⚠ **부분 해소**(3건 중 2건). 남은 1건은 여러 줄 시그니처 + `{` 단독 줄이라 형태가 다르다.
  ✅ **→ F27(#46)에서 엔진 자체를 고쳤다.** F25 는 이 버그의 한 증상을 소스 전처리로 우회한 것.
  당시 적어둔 "순증감(net)" 안은 **불충분**했다 — `} else {` 와 `function f() {}` 가 둘 다
  `net == 0` 이라 구별되지 않는다. F27 은 줄 텍스트를 오른쪽→왼쪽으로 훑어 해결했다.
  ⚠ 범위 초과 1건: `expand_single_line_blocks()` 통째 호출로 RQ1 자산 12개가 `if` 확장까지
  변형됨 → `git checkout` 원복. 전처리는 **필요한 단계만 직접 호출**할 것.

  **누적 (regen30)**: `[ERR]` **552 → 26(−95%)** · `[ANTLR]` 212 → 183 · pkl 143 ·
  본문 있는 함수 396 · 빈 함수 25 · 각 단계에서 증가한 파일 0개.
  잔여 26 파일별: Float 4 · PRBMathSD59x18 4 · PRBMathUD60x18 4 · Controller 3 ·
  ERC1155Upgradeable 3 · IMochiProfile 2 · IMochiVault 2 · 기타 4.

  **백로그(별건)**: ① custom error 미구현(파서 재생성 필요, `Dependencies/` 영향 0) ·
  ② **블록 주석이 슬라이서를 깨뜨림(신규)** · ③ intent 문법 `ceil`/`floor` 예약(문법 불변 결정) ·
  ④ C3 선형화 미준수 · ⑤ `check_pkl.py` 판정 기준 오류 · ⑥ Yul 커버리지(PRBMath* 가
  `[ANTLR]` 187 중 157 차지) · ⑦ `scan_*` 3종 비재귀 · ⑧ 배열 읽기 경로에 `.elements[idx]`
  직접 접근이 `Evaluation` 4곳 / `Update` 3곳 남음.

  **⚠ 작업 규칙 위반 기록(2026-09-19)**: F15·F16 1차를 **승인 없이 적용**했다.
  "판단은 너가 해줘"(순서 판단 위임)를 승인 생략으로 잘못 읽은 것이다. 사용자 지적 후
  F16 1차는 되돌리고 재설계 → 승인 → 재적용했다. 이후 모든 수정은 **보고 → 승인 → 적용**.
  되돌리는 과정에서도 사고가 있었다 — `s.index("            et = ...")`(12칸)가
  `process_state_variable` 안 16칸 줄의 **부분문자열**에 먼저 매치돼 84줄이 삭제됐다
  (`process_constant_variable` 소실). `git diff HEAD` 로 손상 훅을 특정해 HEAD 구간을 재삽입 복구.
  **앵커는 줄 시작 포함 + 유일성 검증**으로 잡을 것.

  **문서 방침(사용자 지시)**: 이후 엔진 수정은 **`(to_solidityguardian)_engine_fixes.md`** 에 포팅 명세로
  기록한다. Guardian 은 **읽기 전용**(심사 중 제출본) — 옮길지/언제 옮길지는 사용자가 결정한다.
  F3·F4·F5·F6·F7·F8·F9 는 Guardian 에도 **동일하게 존재함을 읽어서 확인**했다(2026-09-18).
  F10~F14 는 Guardian 미확인 또는 선행 항목(F4·F6) 포팅이 전제다 — 문서의 `Guardian 상태` 참조.

- **(46) 엔진 버그 3건 추가 수정 — F26·F27·F28 (2026-09-30)**:
  decision #45 의 잔여 오류를 이어서 정리. **보고 → 승인 → 적용** 순서를 지켰고,
  검증은 전부 **리포를 건드리지 않는 드라이런**(출력 디렉토리를 스크래치로 돌림)으로 먼저 했다.
  상세·Guardian 포팅 명세 = **`(to_solidityguardian)_engine_fixes.md`** F26~F28.

  **F26 ★코어 · 조용한 오분석 — `reinterpret_from` 이 함수 ENTRY 의 env 를 덮어쓴다**:
  이 엔진은 미정의 함수 호출을 `pending_calls[callee] = [(caller_fcfg, cur_blk)]` 로 등록해뒀다가
  그 함수가 선언되면 호출자를 그 지점부터 재해석한다(`reinterpret_from`). 그런데
  `_compute_in(n)` 은 **언제나** predecessor join 으로 in-env 를 만든다 — "seed 는 언제나
  함수 중간의 statement 블록" 이라는 **옛 전제(= callee 가 caller 보다 먼저 선언된다)** 의 잔재다.
  미정의 호출이 **함수의 첫 문장**이면 등록되는 `cur_blk` 이 **ENTRY 자신**이라 전제가 깨진다.
  그때 ENTRY 의 in 은 predecessor 가 없으면 `{}`, `splice_modifier` 가 modifier 체인을
  ENTRY **앞에** 붙여놨으면 modifier 자신의 얇은 env 가 되어 **파라미터·state 변수가 통째로 소실**된다.
  ★ **modifier 는 원인이 아니다** — modifier 없는 합성 케이스에서도 재현했다
  (`seed=['ENTRY'], preds=[[]]` → `'_total' ... may not be declared`).
  ⚠ 바깥에서 `n.variables = copy(new_out)` 로 ENTRY 에 **영구 기록**되므로, 예외가 안 나면
  오류 없이 "state 를 모르는 함수" 가 pkl 로 저장된다(#20·F24 와 같은 부류).
  **수정**: entry env 는 dataflow 가 아니라 주어지는 값 — ENTRY 에서 `fcfg.related_variables` 의
  **빠진 키만** 채운다(predecessor 값 우선: modifier 가 바꾼 `_initializing`·`isTopLevelCall` 보존).
  근거는 같은 파일 `interpret_function_cfg` 가 **이미 그 규약을 쓴다**는 것
  (`start_block.variables = copy(fcfg.related_variables)`).
  **버린 대안**: ① callee 먼저 재정렬 — 상호재귀는 위상순서가 없고(합성 `Mutual.sol` 실측)
  **에디터는 재정렬 자체가 불가**(편집 이벤트를 받는다) ② modifier predecessor 제외 —
  제외하면 `{}` 가 되어 modifier 없는 케이스와 같은 상태 ③ modifier 를 CFG 에서 분리 —
  state 변경·require refine·`_` 감싸기를 다 잃는다 ④ seed 를 successor 로 이동 —
  전파 경로로 ENTRY 가 다시 큐에 들어와 불완전.

  **F27 ★ — 괄호 상쇄 알고리즘이 틀렸다 (복붙 사본 2개)**:
  위로 거슬러 올라가며 "닫힌 만큼 상쇄하고 남은 첫 `{`" 를 찾는 루프가, `}` 를 만나 진입하는
  **스킵 모드 안에서 만난 `}` 를 세지 않았다.** 블록이 연달아 닫히면 두 번째부터 증발한다.
  개수(`line_info`)로는 **한 줄 안의 순서**를 못 가리는데 그게 결정적이다 —
  `} else {`(이 줄이 여는 줄) vs `function f() {}`(상쇄). 위로 올라가는 탐색이므로
  줄 안에서도 **오른쪽 → 왼쪽**으로 문자를 훑으면 자연히 맞는다.
  공용 헬퍼 `_enclosing_open_brace_line` 로 합치고 `find_parent_context`·`find_function_context`
  둘 다 사용. ★ **둘 다 고쳐야 한다** — 하나만 고치면 오류 형태만 바뀐다(실측).
  ★ **최대 효과는 `find_parent_context` 쪽**: `assembly` 블록 내부 라인의 ctx 가
  `{'assembly': 2, 'None': 73}` → `{'assembly': 75}` 로 바뀌어, Yul 코드가 Solidity 파서로
  가던 것이 멈췄다 → **`[ANTLR]` 183 → 26**. 백로그 ⑥ "Yul 커버리지 157건" 의 대부분은
  Yul 문제가 아니라 **이 괄호 버그**였다.

  **F28 — 파일 최상위 `enum` 이 file-level 경로에 없다**:
  `SolidityAnalyzer._handle_file_level` 은 type alias / struct / struct member 를 가로채
  CA 로 위임하지 않는 **전용 경로**인데 **enum 만 대응물이 전부 없었다**
  (`visitInteractiveStructDefinition` 의 `current_target_contract is None` 분기도 enum 엔 없음).
  → CA 로 위임 → raise → `analyze_context` 중단 → ctx 가 `"enum"` 인 채 남아 드라이버가
  그대로 visit → `contract_cfgs.get(None)` 에서 또 터짐(선언당 2단 × 2줄 × 2파일 = 8건).
  **수정 = struct 와 대칭** (`_handle_file_level` 분기 2개 + `_CTX_MAP` 2줄 +
  `process_file_level_enum_*` + visitor 분기 2곳). **문법 변경·파서 재생성 불필요** —
  `interactiveEnumDefinition : 'enum' identifier '{' '}'` 가 슬라이서의 가짜 닫는 중괄호와
  정확히 맞고 `interactiveEnumUnit` 도 이미 있다.
  ★ 진짜 이득은 **에디터 경로**다 — `sa.file_level_enums` 는 지금까지 배치의 regex 사전스캔
  (F18)에서만 채워졌다. 이제 스캔 없이도 채워진다(실측: `AssetClass` 8멤버 · `Status` 5멤버).
  즉 **에디터에 파일 최상위 enum 을 치면 지금까지는 깨졌다.**

  **누적**: `[ERR]` **24 → 17** · `[UPDATE_CODE ERR]` **4 → 0** · `[ANTLR]` **183 → 26** ·
  pkl 143 · 함수 964 → **965**(`con_ERC1155Upgradeable._setURI` 복구) · statement 1267 → 1268 ·
  **statement 감소 0건 · 사라진 함수 0개**. `temp/` 회귀 **19종 전부 통과**
  (`test_engine_fixes` 33 · `test_snapshot_grammar` 25 · `test_diff_records` 16 ·
  `test_intent_attach` 15 · `test_back_edge`/`test_bottom_propagation` 각 13 · … failed 0).
  일부가 `UnicodeEncodeError` 로 죽던 것은 **cp949 콘솔 인코딩** 문제이고
  `PYTHONIOENCODING=utf-8` 이면 전부 통과한다(엔진 무관). `test_http`/`test_vault` 는 서버 필요.

  **검증 방법(재사용 권장)**: 기준선을 얻을 때 리포를 되돌리지 않고, 백업본에서 해당 메서드
  소스만 `ast` 로 떼어 live 모듈 globals 에 exec 해 **메모리에서만 원복**했다
  (`scratchpad/revert.py`, `usercustomize.py` + `PYTHONPATH` 로 하위 프로세스에도 적용).
  기준선이 ERR=24·ANTLR=183 로 정확히 재현됐다.
  ⚠ **드라이런 사고(교훈)**: "저장 무력화" 를 `pickle.dump = no-op` 으로 했더니
  `analyze_file` 의 `open(out,'wb')` 가 먼저 truncate 해 **pkl 143개가 0바이트**가 됐다
  (`git checkout` 으로 정확 복원). 저장을 막으려면 **출력 디렉토리(`OBJ_DIR`)를 옮겨야** 한다.
  또 드라이런 2개가 **같은 스크래치 출력 디렉토리**를 공유해 서로를 덮어썼다 — 실행마다 고유 경로로.

  **잔여 17건**: Float 4(★ 슬라이서가 **식 중괄호**를 블록으로 오인 — `res = FloatStruct({` 가
  가짜 `}` 로 두 동강) · PRBMathSD59x18 4 · PRBMathUD60x18 4(Yul `switch`/`case` 6 +
  for-init 복합대입 2) · Controller 3(오버로드 인자개수 미고려 1 · `IAction` 파일 결손 1 ·
  `delete` 매핑 1) · AddressProviderMeta 2(**F29 제안됨, 미적용**).
  ※ decision #45 의 26 중 2건(`Qualified type`)은 **콜드스타트 전용**이었다(interface 가
  library 보다 먼저 분석돼 첫 회차에만 `lib_*.pkl` 이 없다) — 그래서 기준선이 24다.

  **F29 ✅적용 — `Engine._interpret_var_decl` 이 struct 정의를 안 붙인다**:
  `Meta memory meta;` 선언 직후 `reinterpret_from` 이 predecessor 에서 env 를 다시 계산하므로
  선언문이 **재실행**되는데, 그 재실행 경로(`Engine._interpret_var_decl`)가
  `ContractAnalyzer._create_variable_object` 의 **인라인 사본**이면서 `struct_defs` 를 안 붙인다.
  객체 수명 실측: `[1] CA 가 members=['freezable','frozen'] 로 생성 → [2] 블록에 심음 →
  [3] reinterpret_from 진입(pred 에 meta 없음 — 선언 전이니 정상) → [4] 재실행이 members=[] 로
  덮어씀 → [5] 그게 남음`. ★ **`meta` 가 사라지는 게 아니라 `meta.members` 가 빈다.**
  "CFG 에 등록된 expression 을 보면 되지 않나" = **이미 그렇게 하고 있다**([4]가
  `stmt.type_obj` 를 읽는다). 문제는 "보지 않음" 이 아니라 **"보고 만드는 방법이 틀림"**.
  → 인라인 사본을 지우고 `_create_variable_object` 를 부른다(배열·매핑도 같이 고쳐진다).
  대안(선언 statement 에 프로토타입을 저장해 clone)은 **증분 편집에 불리**하다 —
  struct 정의가 바뀌면 프로토타입이 낡는다. `type_obj` 재구성은 현재 정의를 집어온다.

  **백로그 신규 3건**: ⑨ **재귀 가드가 아예 없다** — `function f() { f(); }` 나 상호재귀가
  종료 조건 없이 돌아 `RecursionError`. **두 리포 모두 없고 Guardian git 295 커밋 전수 조회
  (`git log -S`)에서도 0건** — 사용자가 기억한 "등록해두는 방식" 은 `pending_calls`(같은
  `dict[str, list]` 모양이지만 용도는 선언 순서 해결)로 보인다. 더 나쁜 것: `_reset_node_vars`
  가 그 함수 CFG 의 모든 노드 env 를 비우므로 재진입 시 바깥 워크리스트가 순회 중인 노드가
  밀린다. 삽입 지점은 `interpret_function_cfg` 진입부 하나. F26 이 만든 결함이 아니다
  (직접 재귀는 F26 이전에도 동일하게 터짐 — 합성 `SelfRec.sol` 로 확인).
  ⑩ `end_file_level_struct()`/`end_file_level_enum()` 이 **아무 데서도 호출되지 않는다**.
  ⑪ `_create_variable_object` 의 `ccf` 인자가 미사용 — cross-contract 하강 시
  `current_target_contract` 가 스왑되지 않아 호출자 기준으로 struct 정의를 찾는다.

  **(이어서) F30~F35 — 잔여 17 → 6건 (2026-09-30)**:
  상세·Guardian 포팅 명세 = **`(to_solidityguardian)_engine_fixes.md`** F30~F35.

  **F30 ★제품 경로 — 슬라이서가 식(expression) 중괄호를 블록 헤더로 오인**:
  `_open_blk = r"\{\s*$"` 가 "`{` 로 끝나면 블록 헤더" 로만 판정해, named struct literal
  `res = FloatStruct({ ... });` 에 **가짜 `}`** 를 붙여 문장을 두 동강 냈다(Float 4건).
  합쳐 한 줄로 주면 파서는 정상 처리한다 — 순수 슬라이서 결함.
  수정: 그 `{` 시점에 **괄호가 열려 있으면 식 중괄호**(문자열 리터럴은 건너뜀). 다중 줄은
  **누적 텍스트** 기준(`foo(` ⏎ `Bar({` 중첩 대비).
  오탐 전수 검증: `{` 로 끝나는 줄 46,214개 중 괄호 열린 것 189개 → **186개가 struct 리터럴**,
  3개는 **소스 자체 문법 오류**(닫는 괄호 누락). 오탐 0.
  ⚠ `Dependencies/main.py` 의 slice_solidity 는 배치 전용이 아니라 **engine_server 가 쓰는 제품 경로**다.
  ⚠ 슬라이서가 **3벌**이고 갈라져 있다 — `soltotestjson.py`(RQ1 케이스 생성)는 **미수정, 필요**;
    `Libraries/main.py` 는 **아무도 import 안 함 = 삭제 후보**.

  **F31 — `for` 의 init 이 식일 때 크래시**:
  ANTLR 은 **각 라벨 대안의 직접 자식만** 접근자로 만든다.
  `simpleStatement : ... | expressionStatement # EContext` 이므로 `EContextContext` 에는
  `.expressionStatement()` 뿐이고 `.expression()` 이 **없다**. 비지터가 한 단계를 건너뛰어
  `for (y >>= 1; ...)` / `for (i = 0; ...)` 가 통째로 실패했다(코퍼스 34건 — Dependencies 2 + **Dataset 32**).
  수정: 두 단계를 파고들지 않고 **`visitExpressionStatement` 에 위임**(사용자 제안).
  ⚠ **이 수정이 숨어 있던 오류 3건을 드러냈다**(PRBMathSD59x18.powu L340/342/343) — 원인은
    F31 이 아니라 **백로그 ⑫ ⊥ write-back**. pkl statement 감소는 0건.

  **F32 — assembly 안의 중첩 Yul 블록이 닫는 `}` 줄을 선할당하지 않는다**:
  `{` 로 끝나는 줄은 바깥에서 `헤더\n}` 2줄 레코드가 되고, 그 "가짜 `}`" 가 분석기 라인
  공간에서 **닫는 브레이스 자리를 미리 확보**해 함수 EXIT 를 계속 뒤로 민다. 그런데
  **assembly 모드는 모든 줄을 단일 줄 레코드로** 냈다 → 여러 줄 `default { ... }` 에서
  EXIT 가 삽입 지점보다 **앞**에 남아 assembly **바깥**의 뒤따르는 문장들이 터졌다
  (`No CFG node found after line N` 6건).
  최소 재현으로 확정: `default { r := 2 }` 한 줄이면 0건, 3줄로 쪼개면 실패.
  수정: assembly 모드에서도 `{` 로 끝나는 줄을 2줄 레코드로(사용자 제안 "더 잘게 쪼개기").
  한 줄에서 여닫는 `case 1 { r := 1 }` 는 브레이스가 상쇄되므로 그대로 둔다.

  **F33 — `delete m[k]` 가 resolve-only 규약을 어긴다 ★코어**:
  `resolve_lhs_expr` 는 "객체만 찾는다" 며 `update_left_var(expr, None, None, ...)` 를 부른다.
  형제 분기 6곳은 전부 `if r_val is None: return ...` 가드를 갖는데 **2곳만 빠져 있었다**
  (member-access 키 / identifier 키). 코퍼스엔 1건만 보였지만 `delete m[k]` 는 **전 형태가
  깨져 있었다**(실측 3형태 → 수정 후 0).

  **F34 — 상속 함수 오버로드가 인자 개수를 안 본다 (미수정, 원인 확정)**:
  arity 재검색 로직은 있는데 `functions_source = contract_cfg.functions.get(name)` 가
  **현재 컨트랙트만** 본다. `_prepare` 는 부모 `Preparable` 에 4개 오버로드로 있어
  `find_function_in_hierarchy` 로 찾히지만 `functions_source` 가 비어 재검색이 동작하지 않는다.
  수정 방향: `find_function_in_hierarchy` 가 **소유 CFG 의 overload 집합**을 함께 돌려주게.

  **F35 자산 보강 — `IAction` 결손**: `Dependencies/` 에 `IAction.sol` 이 **아예 없었다**.
  `Web3Bugs/contracts/112/backd/interfaces/actions/IAction.sol`(25줄)에서 가져와
  관례대로 **블록 주석 제거** 후 `Dependencies/interfaces/IAction.sol` 로 추가.

  **누적 (2026-09-30 최종)**: `[ERR]` **24 → 6(−75%, 552 기준 −99%)** ·
  `[UPDATE_CODE ERR]` 4 → **0** · `[ANTLR]` 183 → **12** · pkl 143 → **144**(`ifc_IAction`) ·
  함수 964 → **974** · statement 1267 → **1278** ·
  **각 단계에서 statement 감소 0건 · 사라진 함수 0개**.
  회귀 `temp/` **16종 188 checks 전부 통과**.
  단계별: 24 → 22(F26) → 21(F27) → 17(F28) → 13(F30·F31·F33) → 7(F32) → 6(F35).

  **잔여 6건**: PRBMathSD59x18 3(**백로그 ⑫** ⊥ write-back) ·
  AddressProviderMeta 2(**F29** 진단·수정안 완료, 🕐 사용자 검토 중) ·
  112/Controller 1(**F34** 원인 확정, 구체안 미정).

  **★ 백로그 ⑫ — 내부 함수 호출이 호출자 env 를 ⊥ 로 오염시킨다 (신규, 건전성)**:
  `interpret_function_cfg` 는 ① callee 에 **caller env 를 통째로 얹고**(callee 파라미터만 제외)
  ② callee exit env 를 **caller 에 되쓴다** — **⊥ 검사가 없다**.
  실측(`PRBMathSD59x18.powu`): `uint256 absX = uint256(abs(x));` **한 줄이 파라미터 `y` 를 ⊥ 로
  만든다**(`y` 는 그 줄에 없다). **함수 ENTRY 노드까지 오염**된다. 그 결과 루프 조건의
  **양쪽 분기가 도달불가**로 판정되고, `fixpoint` 의 exit-env 계산이 `exit_env=None` →
  `for_exit.variables = {}` → 뒤 문장이 "미선언" 으로 터진다(잔여 3건).
  **오류 3건보다 파급이 크다** — 오염된 env 는 대부분 **오류 없이** pkl 로 저장된다.
  사용자 가설("크로스 컨트랙트 함수 리턴이 제대로 안 되는가")이 맞았고, 실제로는
  **같은 라이브러리 내부 호출**에서도 난다.
  조사 순서: **(다)** `abs`(`unchecked { require(x > MIN); result = x < 0 ? -x : x; }`) 안에서
  ⊥ 이 생기는 원인 → (가) write-back ⊥ 필터 → (나) callee 로 넘기는 env 를 state+인자로 좁히기.

  **★ 백로그 ⑬ — Yul 제어흐름 미구현 + 순차 적용 = 건전하지 않다 (신규)**:
  `visitYulSwitchStatement`/`If`/`For` 가 전부 `return None` 인데 슬라이서가 줄 단위로 자르므로
  `case`/`default` 가 독립 문장으로 **순차 적용(마지막이 이김)** 된다.
  실측 반례: `switch x / case 1 { r := 100 } / case 2 { r := 200 } / default { r := 7 }`
  → 참값 `r ∈ {100,200,7}` 인데 분석기는 **`r = [7,7]`**. `@Post r == 7` 이 `x == 1` 에서
  실제 위반인데 **Satisfied** 로 나온다.
  **진행 방향 = (B)**(사용자 결정): assembly 블록을 **한 트리**로 Yul 비지터에 넘겨
  분기 CFG + join 을 만든다. 사용자가 지적한 "`case 1 {...}` 는 사실 `case 1 {`/`}` + 본문
  2개" 라는 분해가 바로 그 CFG 구성이다(슬라이서에서 쪼개면 경계만 생기고 join 이 없다).
  대가: 증분 편집 단위가 블록 전체 · assembly 내부 라인-레벨 intent 불가(사용자 허용).

  **검증 방법**: 매 단계 **리포를 건드리지 않는 드라이런**(`OBJ_DIR` 만 스크래치로) → pkl
  statement 대조 → `temp/` 회귀 → 그 다음에 실제 재생성. 기준선은 백업본 메서드를
  `ast` 로 떼어 live 모듈에 exec 하는 **메모리 상 원복**으로 얻었다(`scratchpad/revert.py`).

  **(이어서) F36·F37 — 잔여 6 → 0건, `[ERR]` 552 → 0 (2026-09-30)**:

  **F36 ★코어 — 내부 함수 호출이 호출자 env 를 ⊥ 로 오염시킨다**:
  `interpret_function_cfg` 가 ① callee 에 **caller env 를 통째로** 얹고(callee 파라미터만 제외)
  ② callee exit env 를 **caller 에 되썼다** — ⊥ 검사도, 공유 범위 제한도 없었다. 실측:
  `PRBMathSD59x18.powu` 의 `uint256 absX = uint256(abs(x));` **한 줄이 파라미터 `y` 를 ⊥ 로
  만든다**(`y` 는 그 줄에 없다). **함수 ENTRY 노드까지 오염**되고, 루프 조건의 양쪽 분기가
  도달불가로 판정돼 `for_exit.variables = {}` → 뒤 문장이 "미선언" 으로 터졌다.
  ★ **"덮어쓰기" 자체는 필요하다** — callee 의 `related_variables` 는 **선언 시점 스냅샷**이라
  caller 가 바꾼 state 를 못 본다(Solidity 에서 state 는 같은 storage). Guardian `46c497e`
  (2026-04-02)가 "없는 것만 채우기"→"덮어쓰기" 로 바꾼 이유이고, **그 커밋 주석에
  `caller env의 state variable / global을 callee에 공유` 라고 의도가 적혀 있다** —
  즉 **의도는 처음부터 state+global 한정이었고 필터만 구현되지 않았다.**
  수정: `_is_shared_storage`(이름에 `.` → 전역 · `scope == 'state'` → state)로 한정하고
  진입·복귀에 **대칭** 적용. 판정 기준은 `Variables.scope` 가 이미 제공한다
  (`state` / `None`+전역 / `local`=지역·파라미터·named return).
  ⚠ `interpret_function_cfg_for_debug` 는 `caller_env=None` 으로만 호출돼 사장 코드 — 미변경.

  **F37 ★코어 — 오버로드 선택이 인자 타입을 보지 않는다 (F34 흡수)**:
  표면 증상은 `112/Controller:54` 의 arity 오류지만, **더 중요한 건 조용히 틀리던 3곳**:
  `Preparable:47 _prepare(key, value, _MIN_DELAY)`(value: **address**) ·
  `Preparable:83 _setConfig(key, newValue)`(**address**) · `Fixed18:114 UFixed18Lib._from(a)`
  (a: Fixed18=**int256**) — 전부 uint 버전으로 해석돼 `pendingAddresses` 대신
  `pendingUInts256` 를 건드리고 있었다.
  원인 둘: **(1)** `functions_source` 가 현재 컨트랙트만 봐서 상속 오버로드 집합이 비었다(=F34)
  **(2)** arity 만으로는 구별 불가한 오버로드가 있다 — 전수 조사 결과 pkl **953개 함수 이름 중
  오버로드 18개, 그중 인자 개수로 구별 불가 6개**(`_prepare`·`_setConfig`·`Fixed18Lib._from`·
  `UFixed18Lib._from`·`FixedPointMath.add`·`.sub`).
  ★ **왜 타입을 몰랐나**: `Domain/IR.py:74` 에 `expr_type` 슬롯이 **설계돼 있는데** 비지터가
  **리터럴과 디버그 합성 표현식에만** 채운다(식별자·멤버·인덱스·호출·산술은 전부 `None`).
  이 결손이 F14·F16·F34 가 **각각 따로 우회로를 깔게** 만든 공통 원인이다.
  수정: `_static_type_of` 를 **순수 질의**로 신설(평가 없음 → 부작용 0, IR 미변경 → 기존
  리터럴 경로 동작 변화 0). 식별자·캐스트(type alias 해소)·멤버·인덱스·호출 리턴·산술·삼항을
  재귀 처리하고 **리터럴은 None**(Solidity 에서 숫자 리터럴은 독립 타입이 없다).
  그 위에 `_select_overload`: **arity → 선언 타입 정확 일치 → 암시적 변환**, 그래도 애매하면
  **raise**. 유효한 Solidity 에는 타입 애매함이 없으므로 그 상황은 (a) 우리 타입 바인딩
  결손이거나 (b) 소스가 유효하지 않다(solc 도 ambiguity 에러) — 둘 다 드러나야 한다.
  ★ **raise 가 즉시 값을 했다**: 1차 구현에서 `FixedPointMath.add(self, fromU256(value))` 의
  2번째 인자(**함수 호출**)를 처리하지 못해 `[ERR]` 2 + **statement 감소 2건**이 났다.
  조용히 첫 후보를 집었다면 아무 신호도 없었을 것이다. 그 과정에서 호출 IR 모양도 정정 —
  `context='FunctionCallContext'` 이고 함수 이름은 **`expr.function.identifier`** 에 있다
  (`evaluate_function_call_context` 첫 줄의 `IdentifierExpContext` 는 변형이다).

  **F30 을 `soltotestjson.py` 에도 반영**(RQ1 케이스 생성 경로). `Libraries/main.py` 의
  세 번째 슬라이서 사본은 **아무도 import 하지 않아** 삭제 후보로 남긴다.

  **최종 (2026-09-30)**: `[ERR]` **552 → 0** · `[UPDATE_CODE ERR]` 0 · `[ANTLR]` 212 → **12** ·
  pkl 143 → **144** · 함수 964 → **974** · statement 1267 → **1283** ·
  **각 단계에서 statement 감소 0건 · 사라진 함수 0개** · 회귀 `temp/` 16종 188 checks 통과.
  단계: 24 → 22(F26) → 21(F27) → 17(F28) → 13(F30·F31·F33) → 7(F32) → 6(F35) → 3(F29·F36) → **0**(F37).

  **⚠ `[ERR] 0` 이 "다 고쳤다" 는 뜻이 아니다.** `[ERR]` 은 **던져진 예외**만 센다.
  조용히 틀리거나 미구현인 것이 남아 있다:

  **F39 (신규, 미적용) — 음수 `int` 상수의 부호가 사라진다 ★건전성**:
  `int256 constant MIN_SD59x18 = -578...968;` 이 `UnsignedIntegerInterval([+578...968])` 로
  저장된다(부호 소실 + 도메인도 Unsigned). 그래서 `abs` 의 `require(x > MIN_SD59x18)` 이
  `x > +578...968` 이 되는데 `int256` 최대값은 `578...967` 이라 **항상 거짓** →
  **`abs` 가 통째로 ⊥(도달불가)** 로 판정된다. **오류는 안 난다.**
  ★ F36 은 그 ⊥ 의 **전파**를 막았을 뿐이고, ⊥ 자체의 원인은 이것이다.
  최소 재현: `int256 constant NEG = -5;` → `UnsignedIntegerInterval([2²⁵⁶-5])` ·
  `POS = 7` → `UnsignedIntegerInterval([7])`(값은 맞지만 도메인 틀림).
  원인: `-5` 는 `UnaryPrefixOp('-')` + `LiteralExp(5)` 인데 리터럴에 **선언 타입이 전달되지
  않아** uint 로 평가된 뒤 단항 마이너스가 2의 보수로 감긴다. F16 주석이 이미 경고했던 지점.
  수정안(프로토타입 검증 완료): `process_constant_variable` 에서 `et.startswith('int')` 일 때
  리터럴(단항 마이너스면 그 피연산자)에 `expr_type='int'` 를 실어준다 → 3케이스 전부 정확.
  ⚠ `process_state_variable`·지역변수 선언·대입도 확인 필요. **F38 의 부분집합**이다.

  **F40 (신규, 미적용) — 중첩 Yul 블록이 Solidity 파서로 간다**: `[ANTLR]` 12건 중 **8건**.
  `find_parent_context` 는 **직근 부모**의 종류를 돌려주는데, `if iszero(...) {` / `case 32 {`
  안의 줄은 부모가 `"if"`/`"case"` 라 `analyze_context` 의 `parent == "assembly"` 검사를
  통과하지 못한다 → Solidity 파서로 간다(`revert` 의 기대 토큰 집합이 `interactiveSourceUnit`
  인 것이 증거). 수정: 괄호 체인을 **위로 걸어올라가** `assembly` 를 만나면 assembly 로 판정
  (함수/컨트랙트 경계에서 중단). `_enclosing_open_brace_line` 반복 적용.
  나머지 `[ANTLR]` 4건: `ERC1155Upgradeable` 3건은 `uint256[] memory array` — **`array` 가
  intent 문법 예약어**(`ceil`/`floor` 부류) → 소스 rename `_array`;
  `TickMath` 1건은 `else r = ratio << (127 - msb);` — **중괄호 없는 else** → 전처리 확장 필요.

  **F38 (별건) — `expr_type` 을 비지터가 채우고 F14·F16 우회로 제거**: F37 의 `_static_type_of`
  가 순수 질의라 기존 동작을 안 건드리지만, 근본은 IR 에 타입을 채우는 것이다. 선행 조건:
  `evaluate_literal_context` 의 `ety` 분기가 정확 타입(`uint256`·`bytes32`)도 받게 고쳐야 한다
  (지금은 `'uint256'` 을 주면 `Unsupported literal expr_type` 로 터진다 — F16 실측).

  **⑬ Yul 제어흐름 (별건, 방향 결정됨)**: `visitYulSwitchStatement`/`If`/`For` 가 `return None`
  인데 슬라이서가 줄 단위로 자르므로 `case`/`default` 가 **순차 적용(마지막이 이김)** 된다.
  반례: `switch x / case 1 {r:=100} / case 2 {r:=200} / default {r:=7}` → 참값
  `r ∈ {100,200,7}` 인데 분석기는 **`r=[7,7]`** (unsound).
  **진행 방향(사용자)**: 블록 전체를 한 트리로 받고(=(B)), **Yul 루프도 기존 Solidity 루프와
  같은 구조**로 — `for_join/for_cond/for_body/for_exit` 를 만들고 body 가 닫히면
  `Engine.fixpoint(head)` 를 돌린다(기존 fixpoint 재사용). `switch` 는 조건 노드 + case별
  분기 블록 + join, `if` 는 기존 `if_condition/if_true/if_false/if_join` 과 동일.
  사용자가 지적한 "`case 1 {...}` 는 사실 `case 1 {`/`}` + 본문 2개" 라는 분해가 그 CFG 구성이다.

  **(이어서) F39·F40 — `[ANTLR]` 12 → 4, 숨은 건전성 결함 1건 (2026-09-30)**:
  둘 다 **`[ERR]` 에 나타나지 않던** 결함이다. 상세 = `(to_solidityguardian)_engine_fixes.md` F39·F40.

  **F39 ★건전성 — 음수 `int` 상수/변수의 부호가 사라진다**:
  `int256 constant MIN_SD59x18 = -578...968;` 이 `UnsignedIntegerInterval([+578...968])` 로
  저장돼, `abs` 의 `require(x > MIN_SD59x18)` 이 `x > +578...968`(int256 최대값 `578...967`
  초과) 이 되어 **항상 거짓** → `abs` 가 통째로 ⊥(도달불가)로 판정됐다. **오류는 안 났다.**
  원인: `visitLiteralExp` 가 숫자 리터럴에 **항상** `expr_type='uint'` 를 찍는다(그 자체는
  옳다 — Solidity 숫자 리터럴은 부호가 없고 음수는 단항 `-` 다). 문제는 **선언 문맥이
  다시 타이핑해주지 않는** 것 — `-5` 는 `UnaryPrefixOp('-')` + `LiteralExp(5)` 라 리터럴이
  uint 로 평가된 뒤 단항 마이너스가 **2의 보수로 감긴다**.
  전수 재현: **상수 · 상태변수 · 지역선언 세 경로 전부** 깨져 있었고 대입받는 변수로 전파.
  양수도 값은 맞지만 **도메인이 Unsigned** 로 틀렸다.
  수정: 공용 헬퍼 `_retype_literal_to_declared(init_expr, type_obj)` 신설 —
  단항 +/- 래퍼를 벗겨 안쪽 리터럴에 `expr_type='int'` + `type_length` 를 실어주고,
  세 호출부(`process_state_variable`/`process_constant_variable`/`process_variable_declaration`)
  에서 부른다. 세 곳에 사본을 두지 않은 이유는 이번 세션에 6번 반복된 패턴(F16·F21·F22·F23·
  F29·F30) 때문이다.
  ⚠ `'int256'` 이 아니라 `'int'` 를 쓰는 이유: `evaluate_literal_context` 의 `ety` 분기는
  거친 이름만 받고 `'uint256'` 을 주면 `Unsupported literal expr_type` 로 터진다(F16 실측).
  비트폭은 `type_length` 로 따로 전달한다.
  ★ **F36 과의 관계**: F36 은 이 ⊥ 의 **전파**를 막았고 F39 는 **⊥ 자체의 원인**을 없앴다.
  둘 다 필요하다. ★ **F38 의 부분집합**이다.

  **F40 — 중첩 Yul 블록 안의 줄이 Solidity 파서로 간다**:
  `analyze_context` 의 assembly 감지가 `find_parent_context(...) == "assembly"` 즉
  **직근 부모만** 봤다. 그래서 `if iszero(...) {` / `case 32 {` 안의 줄은 부모가
  `"if"`/`"case"` 라 검사를 통과하지 못하고 **Solidity 파서**로 갔다 — 결정적 증거는
  `revert(0, returnDataSize)` 의 ANTLR 기대 토큰 집합이 `interactiveSourceUnit`(컨트랙트
  레벨)이었다는 것. `[ANTLR]` 12건 중 **8건**(SafeTransferLib 6 + PRBMath* 2).
  수정: `is_inside_assembly(line)` 신설 — F27 의 `_enclosing_open_brace_line` 을
  **반복 적용**해 괄호 체인을 위로 걸어올라가고, 함수/컨트랙트/struct 경계에서 중단.
  새 괄호 로직이 없다.
  → `[ANTLR]` 12 → **4**. `lib_SafeTransferLib.didLastOptionalReturnCallSucceed`
  statement **1 → 4**, `PRBMath*.log10` 각 1 → 2. **감소 0건.**
  ★ F32 는 "닫는 `}` 줄 선할당"(CFG 구성)을 고쳤고 F40 은 **컨텍스트 분류**를 고쳤다 — 별개 문제였다.

  **남은 `[ANTLR]` 4건은 엔진이 아니다**: `ERC1155Upgradeable` 3건 = `uint256[] memory array`
  의 **`array` 가 intent 문법 예약어**(`ceil`/`floor` 부류) → 소스 rename `_array` ;
  `TickMath` 1건 = `else r = ratio << (127 - msb);` **중괄호 없는 else** → 전처리 확장.
  "Yul 커버리지 157건" 으로 분류돼 있던 것이 F27·F32·F40 으로 전부 걷히고 나니 남은 것은
  **예약어 충돌 + 전처리 결손**이었다.

  **최종 (2026-09-30)**: `[ERR]` **552 → 0** · `[UPDATE_CODE ERR]` 0 · `[ANTLR]` 212 → **4** ·
  pkl 143 → **144** · 함수 964 → **974** · statement 1267 → **1288** ·
  **각 단계에서 statement 감소 0건 · 사라진 함수 0개** · 회귀 `temp/` 16종 188 checks 통과.

  **(이어서) F41·F42 — `[ANTLR]` 4 → 0 (2026-09-30)**:
  남은 `[ANTLR]` 은 엔진이 아니라 **예약어 충돌 + 전처리 결손**이었다.

  **F41 — `array` 가 intent 문법의 예약어**: `Parser/Solidity.g4:337` 의 디버그 값 문법
  `| 'array' '[' ... ']' # DebugIntArray` 에 리터럴로 박혀 있어 ANTLR 이 암묵적 토큰으로
  만들고 그것이 `Identifier` 보다 우선한다 → `array` 는 **식별자가 될 수 없다**
  (`ceil`/`floor` 와 같은 부류, F17(B)).
  전수 조사: 문법의 리터럴 키워드 중 흔한 식별자 후보(`array`·`ceil`·`floor`·`interval`·
  `percentOf`·`symbolicAddress`)를 Dependencies+RQ1+Dataset 전체에서 조회 → **식별자로
  쓰인 것은 `ERC1155Upgradeable.sol:100-102` 3줄뿐**, 나머지 히트는 **전부 주석 안**.
  → 관례대로 `array` → `_array`. **문법 불변**(IntentChecker 제출본).

  **F42 — 중괄호 없는 `else`**: `else r = ratio << (127 - msb);` 처럼 한 줄에 else + 문장이
  있으면 `extraneous input 'else'`. 슬라이서/분류기는 else 가 **블록을 여는 것**을 전제한다.
  → 전처리에 `_expand_single_line_else` 추가(`else stmt;` → `else {`/`stmt;`/`}`).
  ★ `} else {` / `else {` / `else if` 는 명시 배제. 경계 케이스 6종 실측 확인.
  ★ 선행 `}` 는 **보존**한다 — 슬라이서의 `closeBefore` 경로를 그대로 타야 한다.
  전수 조사: 대상 85건 / 제외된 `else` 줄 2,500여 건 — **오탐 0**. 그중
  **Dependencies+RQ1 자산에는 `TickMath.sol:129` 한 건뿐**이라 그 파일에만 적용
  (Dataset 84건 미적용 — 별도 판단).
  ⚠ **`expand_single_line_blocks()` 통째 호출 금지** — F25 에서 그게 `if`/`unchecked` 확장까지
  돌려 RQ1 자산 12개를 의도 없이 변형시킨 사고가 있었다. `_expand_single_line_else` 만 직접 호출.
  ⚠ 쓰기 시 줄끝이 LF 로 바뀌어 CRLF 복원. `git status -- '*.sol'` 로 **의도한 2파일만**
  변경된 것을 확인.
  ⚠ 미처리: 중괄호 없는 **`else if <stmt>;`** (Dataset 16건, Dependencies+RQ1 0건).

  **최종 (2026-09-30)**: `[ERR]` **552 → 0** · `[UPDATE_CODE ERR]` **4 → 0** ·
  `[ANTLR]` **212 → 0** · pkl 143 → **144** · 함수 964 → **974** · statement 1267 → **1288** ·
  **각 단계에서 statement 감소 0건 · 사라진 함수 0개** · 회귀 `temp/` 16종 188 checks 통과.
  ⚠ **카운터가 전부 0 이라는 것이 "다 고쳤다" 는 뜻은 아니다** — `[ERR]` 은 던져진 예외만,
  `[ANTLR]` 은 파싱 실패만 센다. 조용히 틀리거나 미구현인 것: ⑬ Yul 제어흐름(unsound) ·
  ⑨ 재귀 가드 없음 · F38 `expr_type` 구조적 채우기 · ⑪ `ccf` 미사용 · navigation N1~N7.

- **(47) 논문 §Source-Level Debugging 절 초안 ↔ 구현 대조 (2026-09-30)**:
  ChatGPT 와 작성 중인 §Source-Level Debugging over Abstract Program States 개요를
  실제 구현(`frontend/src/debug/useNavigation.ts` 212줄 = navigation 전부,
  `frontend/src/types/result.ts` = 계약, `engine_api.py`)과 대조했다. **8개 항목 중 3개 불일치.**

  ✅ **일치**: ① navigation 이 abstract interpreter 를 재호출하지 않음(파일 최상단 주석이
  근거) ② join 노드 상태 = feasible predecessor 들의 join(`CFGNode.join_from` 이 기여
  predecessor id 를 실어줌) ③ 루프 본문 상태 = fixpoint 불변식(`StateLocals.tsx` 가
  "루프 불변식(모든 iteration)" 라벨을 명시적으로 붙임) ④ S/W/V/Unreachable 네 판정 실제 산출.

  ★ **불일치 1 — forward 분기**: 개요는 "multiple feasible successor면 사용자 선택",
  구현은 `forwardPick = live[0] ?? ss[0]` 로 **자동 첫째**. 분기 표시/선택 UI 없음.
  게다가 `live[0] ?? ss[0]` 폴백 때문에 **전부 dead 여도 이동**한다.
  → **결정(사용자)**: 선택 대화상자가 아니라 **backward 와 대칭으로 feasible successor 전부
  표시**(커서는 첫째, 나머지 하이라이트). 사용자에게 경로를 고르게 하면 오히려 concrete
  path 선택으로 되돌아간다. = N1.

  ★ **불일치 2 — loop navigation**: 개요의 "Step Forward 가 iteration 으로 이동하지 않음" 은
  **상태 표시에는 참, navigation 에는 거짓**이다. `pred` 는 `kind !== 'back_edge'` 로 거르는데
  **`succ` 는 거르지 않아** `stepForward`/`continueRun` 이 루프를 다시 돈다(값은 fixpoint 라
  안 바뀌지만 커서가 iteration 을 도는 것처럼 보인다). 같은 파일의 applyTick 경로는
  `succOf` 에서 거른다 — **한 파일 안 불일치**. #43 의 남은 절반.
  → **결정(사용자)**: `succ` 에 필터를 켜고, 루프 헤더/본문에서 Step Forward 는
  **루프 이후 첫 statement로 착지**(닫는 `}` = `loop_exit` 가 아니라 — 그쪽은
  `FORBIDDEN_TARGET_FLAGS` 에 있고 pre-state 가 merge 대상이라 모호). 필터만 켜면 루프
  꼬리에서 커서가 멈추므로 **fold 규칙 동반 필수**. = N2.
  ★ **loopDelta 는 엔진에 이미 있다** — `Engine.fixpoint` 가
  `diff_changed(join_baseline_env, fixpoint_evaluation_node_vars)` 로 **값이 바뀐 변수만**
  추출해 루프 헤더 라인에 `{"kind":"loopDelta","vars":…}` 로 기록한다. 그런데
  `engine_api.py` 가 `ledger` 를 **전혀 참조하지 않아** 웹 프론트로 오지 않는다.
  메커니즘은 있고 **배선만 없다**. = N3.

  ★ **불일치 3 — Step Into/Out**: **전부 stub**(`into`=forward 1칸, `out`=backward 1칸,
  주석에 "mock"). 구조적으로 막힌 곳 셋 —
  ① callee 상태가 계산 후 버려진다(`_saved_node_vars` 복원; freeze 는 타겟 fcfg 만)
  ② `NodeFlag` 에 `'call'`/`'opaque'` 가 없어 프론트가 호출 지점을 모른다
  ③ `AnalysisResult.cfg` 가 단일 CFG 라 callee CFG 를 담을 자리가 없다.
  → **결정(사용자)**: 구현한다(재귀 가드는 보류하고 진행). = N4.
  ⚠ 지원 범위: internal ✅ / 상속 ✅ / library ✅ / **dependency pkl 있는 cross-contract 는
  조건부** — 호출 대상 주소를 추상 해석이 특정할 수 있어야 한다(`IERC20(addr)` 의 `addr`
  가 ⊤면 어느 구현인지 모른다). 개요의 "dependency CFG 존재" 는 **필요조건이지 충분조건이 아니다.**
  / interface-only external ❌(`opaque`; `@IReturn` 이면 caller 쪽 후속 해석만).

  △ **부분 불일치**: ⑤ debugger state 에 **call-context 가 없다**(call stack 자체가 없음).
  intent 판정은 `CFGNode` 가 보유하지 않고 `verdict: VerdictItem[]` 별도 목록이며
  `line`+`node` 로 느슨하게 연결된다(정식화 안 하면 무해). `TopVar.provenance` 는
  `"현재 엔진엔 없음 → null"` — **⊤ 출처 태깅 미구현**이라 §8 에 쓰면 안 된다.
  ⑥ backward 는 "선택" 이 아니라 **동시 하이라이트**이고 **navigation history 는 없다**
  (개요의 "structural vs history" 구분 불가).
  ⑦ `Continue` 는 CFG traversal 이지만 **`next/prev bp` 는 소스 라인 번호 정렬 점프**로
  CFG 간선을 전혀 안 본다 → **결정(사용자): CFG order 로 통일**(= N6).
  `continueRun` 이 `isForbiddenTarget` 를 안 봐 merge/loop_exit 에 멈출 수 있다(= N7).
  ⑧ `FalseRegions` 는 **출력공간**이며 **Warning + 단일 During 절**일 때만 존재 —
  입력 파라미터 구간으로 읽히면 안 된다(입력공간 역전파는 Plan B 몫).

  → navigation 구현 항목 **N1~N7** 은 `PROGRESS2.md §A.2` 에 기록.

**다음 우선순위:** ~~6 리스트-batch~~ ✅ · ~~7 라인 좌표계~~ ✅ · ~~10 Apply 버튼~~ ✅ ·
~~8·9 compound+IReturn 스캐너~~ ✅(decision #34) · ~~13·14 pre-state~~ ✅(#33) · ~~forward dead-branch~~ ✅(#35) ·
~~세션 스코프/유령 값~~ ✅(#36 a·b) · ~~#37 라인 주석 게이트~~ ✅ · ~~#36(c) delete 스코프 재검증~~ ✅ ·
~~프론트 diagnostics 표시~~ ✅(#38) · ~~ANTLR 버전 정합~~ ✅(#39) →
① **기능 #1 = intent 위치 debug 입력 자동 생성**(decision #40, 주력 주제) →
   ~~(A1) falseRegions 배관~~ ✅ · ~~(A2) Unreachable 판정(during)~~ ✅ →
   (A2-post) Post intent 도달불가 [남음] · (B) 분할 탐색기 설계문서→구현 [승인 후] →
② 브라우저 재확인(증분 편집 부활 + 6·7·10·8·9·#35 육안) → ③ L2(decision #31, #40 과 통합될 수 있음) →
④ try/catch pre-state(#33 잔여) → ⑤ **IntentKeeper 전용 실험 스크립트**(RQ1 대체, 주석 .sol 대신
`EngineSession` 에 구조화 intent/debug 값 투입 — 개발 얼추 끝난 뒤로 미룸, 사용자 결정).
(#34 b1/b2 는 인위적 코너라 후순위 격하)

- **(48) 루프가 한 번도 제대로 돌지 않고 있었다 — F43·F44 + N1~N3·N5~N7 (2026-10-01)**:
  N3(`loopDelta` 웹 배선)을 하려고 `while (i < n)` 테스트를 돌렸더니 `loopDelta` 가 없고
  **루프 본문 노드 state 가 전부 `{}`** 였다. 추적 결과 추상도메인의 비교 정제가 틀려 있었다.

  ★ **F43 — `refine_intervals_for_comparison` 의 `<`/`>` 가 반대쪽 경계를 쓴다**
  (`Interpreter/Semantics/Refine.py`). `a < b` 의 올바른 정제는 `a ≤ B.max−1`, `b ≥ A.min+1`
  인데 코드는 `B.min−1`, `A.max+1` 을 썼다. **우변이 리터럴이면 `B.min == B.max` 라 우연히
  맞아서** `x < 100` 류는 전부 정상이었고, 양변이 변수인 비교만 깨졌다:
  `i=[0,0] < n=[0,10]` → `clamp_max(A, -1)` → **⊥** → true 분기 infeasible → 루프 본문
  영구 미진입. 방향은 **과소근사(too narrow) = unsound** 이며 예외가 아니라 조용히 틀린다
  (`[ERR]` 0 · `[ANTLR]` 0 과 아무 문제 없이 공존했다).
  `<=`/`>=` 는 `<`/`>` ∪ `==` 합성이라 자동으로 같이 고쳐졌다.

  ★ **F44 — `loopDelta` 가 narrowing 이전 스냅샷을 썼다** (`Interpreter/Engine.py`).
  `fixpoint_evaluation_node_vars` 는 widening 루프 안에서만 갱신되므로 수렴 전 값이고
  widening 의 `INFINITY` 가 남아 `[0,inf]` 로 직렬화됐다 → 최종 상태인 `join_node.variables`
  를 쓰도록 바꿔 노드 state 와 일치시켰다.

  **검증**: 구조 무변화(pkl 함수 974→974, statement 1288→1288, 사라진 함수 0, statement 감소 0,
  `[ERR]`/`[ANTLR]`/`[UPDATE_CODE ERR]` 전부 0) → **pkl 재생성 불필요**(pkl 은 CFG 구조만 담는다).
  `temp/` 19종 119 checks 전부 pass, FAIL 0. 바뀐 출력은 전부 개선 —
  `test_delete_cfg` 의 `b: [None,None]`(**거짓 ⊥**) → `b: [2,2]`,
  `test_back_edge` 의 `s: [100,100]`(루프 미진입 잔존값) → 건전한 widening 값 + loopDelta 등장.

  **영향 범위**: 양변 비-리터럴 비교가 Dependencies 135 · **RQ1 499** · Dataset 6896 건이고,
  **RQ1 루프 헤더 92개 중 82개**가 비-리터럴 경계다. 즉 **RQ1 의 루프는 사실상 전부 본문을
  해석하지 않은 채 평가되어 왔다** — 논문 평가 수치에 직접 영향이 있다.

  ★ **F43 이 가린 결함 3건이 새로 드러났다**(전부 **미수정 · 승인 대기**, 상세는
  `(to_solidityguardian)_engine_fixes.md` ⑭⑮⑯⑰):
  - ⑭ `build_for_statement` 가 `pre` 의 기존 후속을 잇지 않는다 → `for` 가 다른 블록 안에서
    열리면 루프를 **우회하는 엣지**가 남고 `for_exit_*` 가 **sink** 가 된다.
    `build_while_statement` 는 `old_succs` 를 전부 재배선해 올바르다 — **빌더 간 불일치**.
  - ⑮ `traverse_loop_nodes` 가 **모든** `loop_exit_node` 에서 절단 → 중첩 루프에서 내부 exit 이
    바깥 루프 본문을 잘라 **바깥 fixpoint 가 1회만 돈다**.
  - ⑯ widening 이 타입 최댓값이 아니라 `INFINITY` 를 넣어 같은 변수가 `[0,inf]` 와
    `[0,2^256-1]` 로 섞여 보인다. 표시 문제만이 아니라 F43 의 `!= INFINITY` 가드가
    **정제를 건너뛰게** 만든다. ★ 프론트 문자열 치환 같은 우회는 하지 않았다(폴백 금지).
  - ⑰ `for_init` 의 `src_line` 이 `-1` → step forward 가 코드 창에 줄 없는 노드에 착지한다.

  ★ **navigation N1·N2·N3·N5·N6·N7 완료** (decision #47 에서 도출). 상세 = `PROGRESS2.md §A.2`.
  N2 의 설계 요점: **`stepForward` 는 루프를 접지만(`loop_delta` 로 효과 표시) `continueRun`/
  `next bp` 는 본문을 통과한다** — concrete debugger 에서 Step Over 는 호출을 건너뛰지만
  Continue 는 그 안의 breakpoint 에 멈추는 것과 같은 관계다. 본문 안 breakpoint 는
  "루프 불변식" 을 보는 정당한 지점이므로 놓치게 하면 안 된다.
  back_edge 를 거르는 대가로 생기는 "루프 꼬리에서 커서 갇힘" 은 `exitEnclosingLoop`
  (꼬리의 back_edge → join → 그 루프를 접음)로 해소했다.

  **남은 것**: N4(Step Into/Out), 브라우저 육안 확인, ⑭⑮⑯⑰ 승인·적용.
  ⚠ ⑭⑮ 를 고치면 **중첩 루프를 쓰는 RQ1 케이스 결과가 또 바뀐다** — 평가 재측정은 그 뒤에.

- **(49) 중첩 루프가 아예 동작하지 않았다 — F45·F46·F47·F48 (2026-10-01)**:
  decision #48(F43) 로 루프가 처음 돌기 시작하자 **그 뒤에 숨어 있던 결함 4건**이 드러났다.
  전부 보고 → 사용자 결정 → 적용 순서를 지켰다. 상세 = `(to_solidityguardian)_engine_fixes.md`.

  ★ **F45(⑯) widening 이 `INFINITY` 를 넣었다** (`Domain/Interval.py`). 산술은 타입 최댓값으로
  맞추므로 **같은 ⊤ 가 경로에 따라 `[0,inf]` / `[0,2^256-1]` 로 갈렸다.** 표시만의 문제가
  아니라 F43 의 `!= INFINITY` 가드가 `inf` 를 보면 **정제를 건너뛴다**.
  ★ 코드 스스로 이미 타입 경계 쪽이었다 — `IntegerInterval.top()` 이 타입 경계를 쓰고 주석에
  "이론적 ±∞ 대신 실제 비트 범위" 라고 적혀 있다. **`widen()` 만 예외**였다.
  **사용자 결정: (b) 타입 있는 구간은 `inf` 를 갖지 않는다.** 부수로
  `UnsignedIntegerInterval.is_top()`(widen 값이 False), `IntegerInterval.is_top()`(top() 이
  False)의 **기존 불일치 2건이 같이 고쳐졌다**(둘 다 호출자 0개인 죽은 코드였다).
  참조 전수 **3파일 17곳**뿐이라 범위가 작았다.

  ★ **F46(⑮) `traverse_loop_nodes` 가 모든 `loop_exit_node` 에서 절단했다** (`Engine.py`).
  중첩이면 **내부 루프의 exit** 이 바깥 본문을 잘라 `for_incr_*` 와 back edge 가
  `loop_nodes` 에서 빠지고 **바깥 fixpoint 가 1회만 돌았다**. 자기 헤더의 False 엣지가
  가리키는 노드만 절단점으로 바꿨다.
  ※ `n` 이 `[0,5]`→`[0,0]` 으로 오염된 것은 **그 자체로는 올바른 정제**였다
  (`i >= n`, `i=[0,0]` → `n=0`). 틀린 `i` 가 올바른 규칙을 타고 번진 것.

  ★ **flat vs hierarchical 을 실측해 flat 을 택했다** (사용자 판단: loop_delta 는 최외곽 기준).
  hierarchical(Bourdoncle recursive — 안쪽을 통째로 수렴시켜 한 걸음으로 쓰는 표준 기법)은
  바깥 카운터를 `[0,5]` 로 정확히 잡지만 **2중 루프 47.4s(flat 4.1s, 14배)** 이고
  **3중 루프는 크래시**했다(증분 빌드 중간 상태에서 재귀 fixpoint 가 미완성 env 를 건드림:
  `This 'j' is may be elementary variable but may not be declared`). 이 엔진은 본문 한 줄마다
  전체를 다시 돌리므로 비용이 (줄 수)×(바깥×안쪽)으로 곱해진다 — "quick" 정체성에 반한다.
  ⚠ **flat 의 대가**: 중첩 시 바깥 루프 카운터가 ⊤ 로 남는다. **건전**(과대근사)하지만 정밀도
  손실이다. 원인은 안쪽 back edge 가 **이미 widen 된 바깥 변수를 안쪽 join 으로 되돌려** 넣어
  바깥 조건 refine 이 매 바퀴 무효화되는 것. `N_MAX` 를 20배 늘려도 안 바뀐다(예산 문제 아님).
  "widening 을 루프-수정 변수에만" 가설은 **기각** — 구간 도메인의 widening 은 componentwise 가
  정의 그 자체이고, `new_max = INFINITY if max < current.max else max` 라서 **값이 안 커지면
  애초에 widen 되지 않는다**(사용자 지적이 맞았다).

  ★ **F47(⑭) `build_for_statement` 가 `pre` 의 기존 후속을 잇지 않았다**
  (`DynamicCFGBuilder.py`). `EXIT` 이라는 **이름**의 노드 하나만 옮기고 나머지는 그대로 뒀다.
  최상위 `for` 은 가짜 닫는 중괄호 덕에 `pre` 의 후속이 항상 `EXIT` 이라 우연히 맞았다.
  `for` 이 블록 안에서 열리면 ① 헌 엣지가 남아 **루프를 우회**하고 ② `for_exit_*` 가
  **sink** 가 됐다 → `for`-in-`if` 에서 루프는 `acc=⊤` 를 계산했는데 뒷 문장은 **`acc=[7,7]`**.
  `build_while_statement` 는 처음부터 `old_succs` 를 전부 재배선해 올바랐다 — **빌더 간 불일치**.

  ★ **F47 이 드러낸 잠복 결함 — join 지연 처리 무한루프** (`Engine.py:_run_worklist`).
  ⑭ 적용 직후 `for`-in-`if` 가 **10분 넘게 멈췄다**(`faulthandler` 로 지점 특정).
  루프 헤더 처리가 `exit_node` 를 `visited` 에 넣지 않아, 복원된 `for_exit → if_join` 엣지
  때문에 `if_join` 이 영원히 "미방문 선행" 으로 보고 재큐잉했다. `work` 에 같은 join 이 두 개
  쌓여 서로 `len(work) > 0` 을 만족시켜 빠져나오지 못한다. → `visited.add(exit_node)`.
  ⑭ 이전에는 그 엣지가 **끊겨 있어서** 가려져 있었다.

  ★ **F48(⑰) `for_init` 의 `src_line` 이 `-1`** (`ContractAnalyzer.py`). 실제 실행되는 문장인데
  라인이 없어 Step Forward 가 **코드 창에 줄 없는 노드**에 착지했다. 헤더 라인을 부여했다
  (사용자 결정: "반복문이 시작되는 위치 자체가 단위"). `line_info["cfg_nodes"]` 에는 넣지
  않았다 — 그쪽은 intent 부착·삭제 수술의 "라인 → 대표 노드" 매핑이라 cond 하나만 유지한다.

  **검증(매 단계)**: `temp/` **119 checks pass, FAIL 0**. pkl 드라이런 함수 974→974,
  statement 1288→1288, 사라진 함수 0, 감소 0, `[ERR]`/`[ANTLR]`/`[UPDATE_CODE ERR]` 전부 0
  → **pkl 재생성 불필요**. 중첩 6형태(`for`/`while`/`if` 조합) 전부 dangling 0 · 우회 0 ·
  `acc=⊤`(루프 효과가 뒷 문장에 도달). N2 궤적에서 `for_init_8(L-1)` → `(L8)`.
  ※ `temp/` 중 5종(`test_bottom_propagation`·`test_http`·`test_outline`·`test_vault`·
  `test_engine_fixes`)은 **원래부터** nonzero exit 이다(출력이 수정 전과 바이트 동일함을 확인).

  ★ **loop_delta 는 최외곽 루프 기준** (사용자 결정). N2 의 step forward 가 최외곽 루프를
  통째로 접고 그 **뒤**로 착지하므로 안쪽 루프는 navigation 착지점이 되지 않는다.
  ⚠ 그냥 "안 생긴다" 가 아니라 **걸러야** 했다 — 안쪽 delta 는 증분 빌드 도중(아직 바깥 루프
  본문에 편입되기 전) `reinterpret_from` 이 안쪽 헤더를 loop head 로 만나 `fixpoint(안쪽)` 을
  직접 불렀을 때 ledger 에 기록되고 그 뒤 갱신되지 않아 **낡은 스냅샷**으로 남는다.
  `engine_api._nested_loop_heads` 를 추가해 **직렬화 시점에 매번 다시 판정**한다(ledger 가
  낡아도 영향 없음). 판정 규칙은 `Engine.traverse_loop_nodes`(F46)와 동일하다.

  **남은 것**: N4(Step Into/Out), 브라우저 육안 확인. ⚠ **RQ1 등 평가 재측정은 지금 해야 한다**
  — F43·F45·F46·F47 이 모두 루프 결과를 바꿨다.

- **(50) 외부 호출 처리 정리 — F49 + N4 범위 확정 (2026-10-01)**:
  N4(Step Into/Out) 설계 중 "구체 컨트랙트 호출은 코드를 아는데 왜 하강하지 않나" 를 파고들었다.

  ★ **핵심 사실**: `Vault(a).f()` 는 **런타임 타입 검사가 없는 순수 캐스트**다. 컴파일 결과는
  주소 `a` 로의 external call 이고 **`a` 에 배포된 임의의 코드가 실행**된다 → **callee 코드는
  타입 시스템이 결정하지 않는다**. 상태변수 `Vault v` 도 20바이트 주소 슬롯이라 같다. 즉
  구체 컨트랙트 캐스트와 인터페이스 캐스트는 **의미론적으로 동일**하다(사용자 지적).
  반면 **mutability 는 결정되고 EVM 이 강제**한다 — 선언이 view/pure 면 solc 가 STATICCALL 을
  내므로 상태 변경은 revert 한다. **그래서 "부작용 없음이 보장된 외부 호출의 반환값만 사용자
  가정으로 받는다"(= `@IReturn`)는 구조가 원리적으로 맞다.**

  ★ **내가 한 번 틀렸다가 정정한 것 두 개**:
  ① "dependency pkl 있고 주소가 풀리면 cross-contract 하강 조건부 가능" → **하강 구현 자체가
     없다**. 그리고 애초에 주소로 정해지므로 "타입이 final 하면 코드가 하나" 도 틀렸다.
  ② "코드를 아니까 분석해야 한다" → 캐스트가 런타임 보장을 주지 않으므로 **코드를 아는 게
     아니다**. 분석으로 가려면 cross-contract 모드(state 변수 비공유, `msg.sender`/`msg.value`
     재바인딩, 백로그 ⑪)가 필요하고 RQ1 8곳 verdict 가 바뀌는데, callee storage 가 통제 불가라
     얻는 건 ⊤ 투성이 상태뿐이다. → **기각**.
  ③ "multi-contract 를 상속+library+`@IReturn` 로 정의하는 게 정직하겠다" 는 **깎아내린 표현**
     이었다. 그 세 가지가 Solidity 의 multi-contract 를 제대로 덮는다. 실측으로 확인:
     internal · abstract contract 상속 · library 는 **하강해서 정확한 값**을 낸다.

  ★ **F49 (적용)**: `@IReturn` 의 **캐스트 경로가 변수 경로와 비대칭**이었다. `v.f()` 와
  `IFoo(a).f()` 는 슬롯이 생기는데 `Vault(a).f()` 만 안 생겨 ⊤ 로 떨어지고 제어 불가였다
  (슬롯 도출 `classify` + 어노테이션 처리 `process_ireturn_cast` 양쪽). 둘 다 "알려진 interface
  또는 (라이브러리 아닌) 알려진 contract" 로 넓혔다. 추가로 **슬롯 도출에 view/pure 게이트**를
  넣었다 — 예전엔 비-view 외부 호출도 슬롯으로 제시하고 값을 넣는 순간 거부했다(fail-open:
  타입·함수 미해소면 제외하지 않음).
  ⚠ **논문 미기재 결정(사용자)** — 구현에만 둔다.
  ⚠ 평가 영향 **0**: 캐스트 형태의 구체 컨트랙트 호출은 Dependencies 0건 / RQ1 0건
  (캐스트 72건 전부 인터페이스). RQ1 의 구체 컨트랙트 접촉 8곳은 전부 변수 형태라 이미 됐다.

  ★ **`address.call` 류는 무조건 ⊤** (사용자 결정, 현재 동작과 일치). 어느 함수가 호출되는지
  조차 모르고 대개 `fallback` 인데 fallback 은 보통 view/pure 가 아니다.
  `Evaluation.py:1842-1860` 이 `_cast_interface` 검사보다 앞에서 `BoolInterval.top()` 을 낸다.

  ★ **N4 범위 확정**: Step Into 가능 = **internal · 상속(`super`/`Parent.f()`/abstract) ·
  library · `this.f()`** (넷 다 `interpret_function_cfg` 를 탄다). 주소 간접 외부 호출은 전부
  opaque 이고, 이유 문구는 "미구현" 이 아니라 **"callee 코드가 타입으로 정해지지 않음 —
  `@IReturn` 으로 반환값 지정"** 으로 쓴다.
  ⚠ 논문 서술: `PROGRESS2.md` 의 "dependency CFG 가 있으면 들어갈 수 있다" 는 **거짓**이다.

  **N4 단계 계획**: S1 엔진(callee `{node → state}` 보존) → S2 계약(`NodeFlag += call/opaque`,
  `CalleeRef`, `CFGNode.callees`, `CfgWithStates.callees`) → S3 직렬화(식 트리 스캔으로 호출
  지점 탐지) → S4 프론트(`cursor` → Frame 스택, stepInto/stepOut, 깊이 상한 + 재귀 표시,
  callee 소스). 미결정 2건: callee 스냅샷 키(호출 지점 기준 권고) · callee 소스 조달 방식.

- **(51) 도구 비교 평가 설계 + expressible 48 원본 컴파일 게이트 (2026-10-05)**:
  학위논문 RQ1(latency) 의 비교 대상을 SolQDebug 때의 **Remix 단독**에서 바꾼다.
  검토 후 사용자 확정: **정량 = Hardhat + Foundry**, **정성 = Remix + Solidity Debugger Pro**.

  ★ **Remix 를 정량에서 내린 근거** (변명이 아니라 측정 전제의 문제). SolQDebug 의
  `SolDebug/Evaluation/RQ1_Latency/remix/remix_benchmark.py:570` 은 상태를 심으려고
  `setter_function = f"set_{var_name}"` 를 호출한다 — 즉 **대상 소스에 setter 를 주입**해야 했다.
  single-contract 에선 감당됐지만 48케이스는 `@StateVar` 가 **87개**(평균 4.4, 최대 WANGMI 17)이고,
  Remix VM 에는 `setStorageAt` 류 RPC 도 `block.timestamp` 조작도 없다. Remix 쪽만 피측정 코드가
  변형되면 동일 전제가 깨진다. 반면 hardhat/anvil 은 `hardhat_setStorageAt`·`evm_setNextBlockTimestamp`
  로 **소스를 안 건드리고** 상태를 심고, 슬롯 계산은 solc `storageLayout` + 매핑 `keccak256(key . slot)`
  으로 기계화된다. → 심사에서 "SolQDebug 때는 Remix 정량이었는데 왜 뺐나" 가 반드시 나오므로
  이 문장을 준비해 둔다. Remix 는 시간이 남으면 **소스 수정 없이 측정 가능한 케이스에서만** 정량 시도
  (사용자: "일단 remix 보류"). 공수의 실체는 측정이 아니라 **2263줄 Selenium 스크립트의 DOM 복구**다.
  Solidity Debugger Pro 는 공식 repo 에 CLI/headless/DAP 진입점이 없고 라이선스도 불명 — 기능 비교로만.

  ★ **측정 비대칭을 물려받지 않는다**: `solqdebug_benchmark.py` 는 edit trace **전체** wall clock,
  `remix_benchmark.py` 는 `debug_open + jump_to_end` 만 total 에 넣었다(compile/deploy/state/exec 는
  따로 기록만 했다). **서로 다른 작업 단위**이고 그대로 쓰면 심사에서 찔린다.
  주지표 후보 = **증분 재측정 latency**(한 줄 고친 뒤 값을 다시 볼 때까지 — 우리 = `update`+`evaluate`
  1회 / 상대 = recompile→redeploy→state 재주입→re-tx→re-open). ⚠ **지표는 미확정**, 사용자와 계속 논의
  (`PROGRESS2.md §B.1`). 또 하나 주의: 우리 `[TIMING]`(`main.py:202`)은 `simulate_inputs` 만 감싸고
  `load_dependencies()` 는 import 시점(`main.py:125`)에 끝나므로 **dependency 분석 시간은 이미 측정 밖**이다
  (IntentChecker 전제와 일관). 비교 도구에도 "의존성은 컴파일·배포 완료" 를 대칭으로 적용한다.

  ★ **데이터셋 = IntentChecker expressible 48** (사용자 확정).
  `SolidityGuardian/evaluation/RQ3/rq3_comparison_table.csv` 가 정확히 48행(web3bugs 41 + numscout 7)이고
  Guardian `run_all.py` 주석의 "원래 20 + RQ3 확장 28" 과 일치한다. IntentKeeper `evaluation/RQ1/cases/`
  55개 디렉토리 중 **어노테이션이 있는 것이 정확히 48개**라 자산이 이미 맞춰져 있었다.
  ⚠ 48개의 디버깅 요소 프로파일은 **루프가 12건(25%)뿐**이다 — F43~F47 이 바꾼 루프·중첩루프와
  N2/N3(loop fold) 를 n=12 로 말해야 한다. 요소별 **보강셋**(intent 불필요, `@StateVar`/타겟 함수만
  필요하므로 케이스당 비용이 절반 이하)을 별도로 뽑는 안을 사용자에게 제안해 둔 상태다.

  ★ **48/48 컴파일 통과** — `evaluation/Dataset/reassemble.py --build` (Foundry 1.8.4, 프로젝트 **33개**;
  w35 가 4케이스, w5·w52·w62·w70 이 각 3케이스를 공유하므로 셋업 비용은 케이스당이 아니다).
  원칙: **소스 .sol 내용은 한 바이트도 바꾸지 않는다** — 디렉토리 배치·remapping·solc 선택만으로 푼다.
  (private→internal 5건은 Step 2 harness 단계로 분리.) 파일 출처 집계:
  target 39 / contest 전용 풀 35 / 범용 풀 116 / flatten 힌트 59 / 복원 2 / npm 3 / sibling 6.

  ★ **그 과정에서 드러난 평가 자산 결함 (논문에 적어야 할 것)**:
  - **numscout 7개의 "원본"이 원본이 아니었다.** `target_contracts_original/numscout_*.{sol,json}` 은
    IntentChecker 가 주석 제거·함수 탈락·한줄블록 전개한 형태라 solc 가 못 읽는다
    (Nokon 62줄 vs Etherscan 271줄, WANGMI 447 vs 1084 — 레코드를 **에디터 삽입 순서로 재생**해도
    similarity 0.23/0.16). Etherscan 검증 원문 `Dataset/Numscout/Original/` 로 교체했다(5개).
    **SwordCrowdsale 은 원문이 어디에도 없어** `flyinointment/greedy_contract/SwordCrowdsale_simple.sol`
    (35줄, `refundMoney` 와 버그는 보존)로 대체 — 논문에 **"simplified reproduction"** 명시 필요.
  - **복원 인터페이스 3개** (`evaluation/Dataset/Dependencies/reconstructed/`, 헤더에 출처 기재):
    `IWETH`·`IMarginTrading`(w3 — code4rena 스냅샷에 `contracts/interfaces/` 가 없고 marginswap 조직
    repo 는 전부 404), `IBentoBoxV1`(w113 — `@sushiswap/bentobox-sdk` 가 npm 에 .sol 을 **전혀** 안 올린다).
    멤버는 호출부·`override` 구현체에서 복원했다. 인터페이스는 바이트코드를 내지 않고 셀렉터는
    시그니처로만 결정되므로 **타겟 바이트코드 무관**.
  - **원 저장소에서 받은 contest 전용 버전 4개** (`Dependencies/fetched/`, URL 은 `FETCHED.txt`):
    `5_iSYNTH`(풀에는 0.8.9 판만 있어 0.8.3 과 충돌), `51_MathUtils`·`51_LPToken`·`51_ISwap`.
  - **Guardian `Dependencies/{contracts,interfaces,libraries}` 사본은 컴파일 소스로 쓸 수 없다** —
    pkl 분석용으로 **import 가 제거/축약된 파일**이 섞여 있다(`51/LPToken.sol` 에 import 0개,
    `IBentoBoxV1.sol` 이 9줄). resolve 우선순위를 내려 최종 사용 **0건**으로 만들었다.
  - **solc 핀 1건**: w42 의 `// SPDX-License-Identifier: (c) Mochi.Fi, 2021` 을 최신 solc 가 거부
    → 0.8.4. 나머지 32개는 auto-detect. 멀티버전은 forge·hardhat 둘 다 네이티브 지원이라 문제 아님.
  - **순환 import 2건**: w36·w65 의 `IBasket ↔ IAuction ↔ IFactory` 에서 solc 가 처리 순서에 따라
    전이 심볼(`IFactory.Proposal`)을 놓친다. upstream 과 **바이트 동일**함을 확인했고, 원 프로젝트가
    hardhat 으로 저장소 전체를 한 번에 컴파일해 통과했으므로 contest 파일 전체를 함께 넣어 해결.
  - ★ **`cases48.csv` 표기 오류 2건** (데이터셋 표 수정 필요):
    **w77_H_01** 은 contract 열이 `Exchange` 인데 타겟 함수 `calculateLiquidityTokenQtyForSingleAssetEntry`
    는 같은 파일의 **`library MathLib`** 안에 있다(그래서 `Exchange.sol` 이 `../libraries/MathLib.sol` 로
    자기 자신을 import). **w16_H_04** 는 타겟 파일 실체가 `lib/LibBalances.sol`(`library Balances`)인데
    컨트랙트명으로 파일명이 바뀌어 저장돼 같은 소스가 두 경로에 놓였다. 둘 다 파일명이 아니라
    **내용 비교(alias)** 로 자동 교정하게 했다 — `reassemble_report.json` 의 `vpath_override` 에 남는다.

  **구조 정리**: `evaluation/Dataset/{cases48.csv, target_original/, target_contraction/, Dependencies/,
  npm/, projects/, reassemble.py, reassemble_report.json}` + `evaluation/{forge,hardhat}/`.
  `evaluation/RQ1/target_contracts_{contraction,original}` 은 Dataset 으로 옮기고 삭제했으며
  `main.py:111-112` 의 interface 스캔 경로를 그에 맞게 바꿨다(interfaces 179 등록 확인).
  `.gitignore` += `evaluation/forge/bin/`(255MB 바이너리, `install.ps1` 로 재설치)·`npm/`·`projects/`.
  ⚠ `tools/generate_case_jsons.py`·`tools/preprocess_contraction.py` 는 **이전부터** 존재하지 않는
  `tools/target_contracts_contraction` 을 가리키고 있다(이번 이동과 무관한 기존 결함). 손대지 않았다.

  **다음**: ① hardhat 게이트(같은 `projects/` 를 hardhat 2.29.1 로도 컴파일) → ② Step 2 상태 주입
  (`storageLayout` → `setStorageAt`, `@GlobalVar` 10건 → `setNextBlockTimestamp`, `@IReturn` 8건/4케이스
  → mock 배포, internal 12건 → 상속 harness, **private 5건 → `internal` 패치 + 메타데이터 제거
  바이트코드 diff 0 확인**).

**Guardian 동기화 방침 변경(2026-07-18)**: 더 이상 동기화하지 않기로 함(사용자) → 코어
(`Analyzer/ Domain/ Interpreter/ Libraries/ Parser/ Utils/ Dependencies/` + `main.py`)를 **IntentKeeper 에서
직접 고쳐도 무방**. #37 이 이 방침 덕에 우회안(A) 대신 근본 수정(C)으로 갈 수 있었다.
**단, 2026-09-16 에 1회성 역방향 포트가 있었다(decision #41)** — Guardian 논문 리비전에서만 고쳐진 snapshot
문법·엔진 버그를 선별해 가져온 것이며, 상시 동기화 재개가 아니다. 이후로도 기본은 IntentKeeper 직접 수정.

**세션 노트**: 서버 세션은 메모리에 `contract.func` 키로 영속 → **테스트 컨트랙트가 프론트 세션 오염** 가능
(재시작으로 클리어). 콘솔 한글 mojibake 는 cp949 출력 문제일 뿐 JSON 은 UTF-8 정상.

---

## 부록-6. 실행/환경 메모

- 엔진 실행: **반드시 `.venv\Scripts\python.exe`** (글로벌 Python310 networkx 2.5.1 깨짐).
  ⚠ **과거 검증 커맨드였던 `evaluation\RQ1\run_all.py` → 20/20 VIOLATED 는 더 이상 유효하지 않다**
  (decision #37 로 0/20 SATISFIED 회귀, 의도된 것). IntentKeeper 전용 스크립트로 대체 예정 — 그전까지
  **버그-탐지 e2e 회귀 스위트는 여전히 없다.** 현재 있는 것은 `temp/` 의 단위·구조 회귀 13종
  (증분편집 9종 60 checks + #41 신규 4종 85 checks) 뿐 — 이들은 "CFG·states·문법이 안 깨졌나"를 보지,
  "실제 버그를 탐지하나"를 보지 않는다.
- ANTLR: 생성 CLI `antlr4` 4.13.2 / 런타임 `antlr4-python3-runtime==4.13.2` (2026-07-18 정합).
  파서 재생성 = `Parser/` 에서 `antlr4 -Dlanguage=Python3 -visitor Solidity.g4`.
- `load_dependencies`는 3개 디렉토리 interface 스캔 필요(`evaluation/Dataset/target_contraction`, `evaluation/Dataset/Dependencies/pool`, `Dataset/Numscout/contraction`). **decision #51 에서 앞 두 경로가 `evaluation/RQ1/...` 에서 옮겨졌다** — 없으면 interface 호출 해석이 조용히 실패한다.
- 프론트: `cd frontend && npm install` (1회), `npm run dev`. 타입체크 `npx tsc --noEmit`.
- 작업 규칙(CLAUDE.md): 객관적·비판적 검토 / 제안 도구(코어 수정 전 승인) / 모호하면 질의.
