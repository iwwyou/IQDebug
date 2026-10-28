# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

프로젝트 = **IQDebug (ISSTA '27 제출용)**. 엔진·프론트는 `IntentKeeper`(학위논문 세션)에서
2026-10-26 복사해 왔고, 목표는 **interpretation-driven debugger 의 operation 의미론**을 중심으로 한 논문이다.
배경 문서는 `paper/SEMANTICS_AUDIT.md`(의미론 정식화 대상·서술↔구현 대조)와
`paper/ISSTA_PLAN.md`(기여 경계·RQ 초안·계측·데이터셋).

**이 저장소를 두 세션이 병행한다** (2026-10-26 사용자 지정).
- **논문 세션** — 조판·본문. `paper/` 만 수정한다. 지침은 `paper/CLAUDE.md` 를 먼저 읽을 것.
- **구현 세션** — 탐색 모듈 추출·계측·실험 하네스·데이터셋. 엔진·프론트·`evaluation/` 을 수정한다.

`paper/` 의 두 배경 문서(`SEMANTICS_AUDIT.md`·`ISSTA_PLAN.md`)는 양쪽이 공유하는 계약이다.
`paper/` 는 전체가 `.gitignore` 되어 있다(`paper/.gitignore`).

⚠ **`C:\Users\isjeon\PycharmProjects\pythonProject\IntentKeeper` 는 읽기 전용이다.** 학위논문 세션이
계속 사용 중이므로 그 경로의 파일을 수정·생성·삭제하지 않는다(참고용으로만 읽는다).
`...\pythonProject\SolidityGuardian`(IntentChecker 투고본) 역시 **어떤 경우에도 수정 금지**.

## 작업 원칙 (사용자가 명시한 규칙)

1. **항상 객관적이고 비판적으로 검토한다.** 사용자의 의견·아이디어에 무비판적으로 동조하지 않는다.
   약하거나 문제가 있는 부분은 분명히 지적하고 더 나은 대안을 제시한다.
2. **제안 도구이며 최종 판단은 사용자가 한다.** 코드를 임의로 수정하지 않는다 — 수정·생성·삭제는
   실행 전에 먼저 묻고 승인을 받는다. (논문 작업 문서 작성은 예외로 취급해 왔다.)
3. **모호하면 먼저 묻는다.**
4. **코어 수정은 승인 후에만.** 코어 = `Interpreter/`(Engine + Semantics), `Domain/`, `Utils/CFG.py`,
   `Analyzer/GuardianVerificationEngine.py`, 파서·visitor. 회귀 가드가 약하다(아래 테스트).

## 실행 (Windows / PowerShell)

**반드시 `.venv\Scripts\python.exe` 로 실행한다** (Python 3.10.11, networkx ≥3.4 — 글로벌 Python310 은
networkx 2.5.1 이라 CFG 백엔드가 깨진다). 의존성은 2026-10-26 `requirements.txt` 로 설치 완료.

```powershell
.venv\Scripts\python.exe engine_server.py          # 엔진 HTTP 서버 (uvicorn 127.0.0.1:8000)
.venv\Scripts\python.exe temp\smoke_engine_api.py  # 스모크 (analyze→evaluate→verify)
antlr4 -Dlanguage=Python3 -visitor Solidity.g4     # Parser/ 에서 재생성 (CLI 4.13.2 = runtime 일치 필수)
```

프론트엔드 (`npm install` 완료, `npx tsc --noEmit` 통과 확인):

```powershell
cd frontend; npm run dev     # Vite 5173
npx tsc --noEmit             # 타입체크
```

레거시 CLI(`main.py`, `run_case.py`)는 **주석 기반** 경로(SolQDebug/IntentChecker 방식)로, 비교·회귀용으로만
남겨 두었다. 옛 데이터셋(`evaluation/Dataset/deps`, `cases_rebuilt` 등)은 가져오지 않았으므로 케이스 실행
스크립트는 그대로 돌지 않는다.

## 테스트

정식 프레임워크 없음. `temp/` 의 단독 실행 스크립트가 회귀 가드다 (각자 `sys.path` 조정 + 자체 `check()`).
2026-10-26 새 프로젝트에서 확인한 상태:

- 통과: `test_session_flow`, `test_update`, `test_delete_cfg`, `test_try_catch`, `test_sparse_array`,
  `test_set_intents`, `test_ireturn_cast`, `test_blank_during`, `smoke_engine_api`(의존성 로드 단계 제외),
  `test_overload_target`(2026-10-28 추가 — overload 타깃 지정)
- ⚠ 여러 스크립트가 `⊤`·`—` 를 출력해 **cp949 콘솔에서 UnicodeEncodeError 로 죽는다**(단언 자체는 통과).
  `PYTHONIOENCODING=utf-8` 로 실행할 것.
- 부분 실패(데이터셋 의존): `test_transaction` T8 — 옛 데이터셋 파일(`evaluation/Dataset/target_contraction_auto/*.sol`)이
  없어서 실패한다. T1–T7 은 통과.
- `test_slot_roundtrip.py` 는 옛 48 케이스에 묶여 있어 새 데이터셋 기준으로 다시 써야 한다.
  `test_http`/`test_vault` 는 서버 실행이 필요하다.

## 아키텍처

### 분석 파이프라인 (한 줄씩 증분 소비)

`.sol` → `Dependencies.main.slice_solidity()` 가 **record**(`{code, startLine, endLine, event, closeBefore?}`)
로 슬라이스 → `SolidityAnalyzer.update_code()` (라인 저장소·시프트) → 라인마다 현재 컨텍스트에 맞춰 ANTLR
부분 파싱(`Utils/Helper.ParserHelpers`; assembly 는 `Utils/YulHelper`) → `EnhancedSolidityVisitor`
(Yul 은 `EnhancedYulVisitor`) → `ContractAnalyzer` → `DynamicCFGBuilder` 가 `Utils/CFG.FunctionCFG` 를
**점진적으로** 구성 → `Interpreter/Engine.py` 가 CFG 위에서 abstract interpretation(worklist fixpoint) →
(선택) `Analyzer/GuardianVerificationEngine.py` 가 @During/@Post intent 검증 → 결과는
`RecordManager.ledger` + `ContractAnalyzer.analysis_per_line`.

핵심은 파일 단위 컴파일이 아니라 **에디터 이벤트(add/modify/delete) 단위 증분 분석**이라는 점이다.

### 트리거 분리 (ISSTA 논문의 구조적 전제)

| 호출 | 트리거 | 결과 |
|---|---|---|
| `analyze` / `analyze_source` | 코드 (최초) | `Structure` = CFG 구조 + 빈 debug 슬롯 + diagnostics |
| `update(new_source)` | 코드 편집 | SyntaxValid 게이트 → record diff(`incremental.py`, 순수 함수) → CFG 수술. 미지원 = `IncrementalSurgeryError`(폴백 없음) |
| `set_intents` | intent 변경 | CFG 유지, 슬롯만 재도출 |
| `evaluate(debug_values)` | 디버깅 입력 | fixpoint 후 states 채워진 CFG(frozen) + callee CFG + ⊤ 변수 |
| `verify(intents)` | intent | 보존된 states 위 standalone 검증 |
| navigation (프론트) | 사용자 operation | **엔진 호출 0** — 받은 CFG-with-states 위 커서 이동 |

dataclass 들은 `frontend/src/types/result.ts` 와 1:1 계약이므로 한쪽만 바꾸면 조용히 깨진다.

### 탐색 (논문의 주 대상)

`frontend/src/debug/navGraph.ts`(순수 규칙: 착지 노드·feasibility·land closure) +
`useNavigation.ts`(설정 전이: 프레임 스택·breakpoint·Continue). 규칙 요약은 `useNavigation.ts` 머리말,
정식화 대상과 서술↔구현 차이는 `paper/SEMANTICS_AUDIT.md`.

### 어노테이션 두 종류 (혼동 주의)

- **debug 값**(`@StateVar/@Param/@GlobalVar/@IReturn`) = 슬롯 채우기. 엔진이 슬롯을 도출
  (`engine_api._derive_debug_slots`), 사용자는 값만 넣는다. `DebugBatchManager.flush()` 가 해석 후
  **restore 로 값을 지우므로** `engine_api` 는 restore 전에 states 를 freeze 해야 한다.
- **intent**(`@During/@Post`) = 코드의 영구 일부. 문법은 `Parser/Solidity.g4`이고 decision #37 이후
  어노테이션에 `'//'` 접두어가 없다.

### 추상 도메인·transfer function

`Domain/`(Interval, AddressSet, BytesSet, Variable, IR, Type, FunctionRef, Annotation),
`Interpreter/Semantics/`(Evaluation=식, Update=대입·상태갱신, Refine=조건 pruning, Storage=접근 경로,
DebugInitializer=입력→추상값, YulEval).

## 함정 (겪어본 것들)

- `EngineSession` 에 `analyze` 를 두 번 호출하면 record 가 이중 적용돼 세션이 손상된다 →
  `engine_server._build` 는 항상 새 세션을 만든다.
- `sess.structure()` 를 스코프 없이 호출하면 폴백이 **컨트랙트의 마지막 함수**를 집는다. 항상
  `contract`, `func` 를 명시할 것.
- 분석 실패가 예외가 아니라 **조용한 오분석**으로 나타나는 경우가 많다 → 결과가 이상하면 diagnostics 부터.
- 의존성 로드는 `Dependencies.main.load_project_deps(sa, ca, deps_root, exclude)` 하나다. 새 데이터셋용
  deps 는 아직 빌드하지 않았다(`evaluation/Dataset/build_project_deps.py` 의 `SOURCE_EDITS`·경로 상수를
  새 구조에 맞게 채워야 한다).

## 참고 문서

- `paper/SEMANTICS_AUDIT.md`, `paper/ISSTA_PLAN.md` — 이번 논문 작업 문서.
- `docs_ref/` — IntentKeeper 에서 가져온 사본: `CLAUDE.md`(원본), `REFACTOR_PLAN.md`(§1 설계결정 D1–D11,
  §7 future work·§7.5 환경 구조 설계안), `PROGRESS.md`(단일 진실원 로그),
  `(to_solidityguardian)_engine_fixes.md`(엔진 수정 이력 F1–F74), `PROMPT_frontend_impl.md`·`PROMPT_frontend_paper.md`.
- 학위논문 원문(읽기 전용): `...\IntentKeeper\paper\Phd_thesis\main.tex` — Design 864–3099행
  (디버깅 설계 2957–3099), Evaluation 3102–4583행. ACM 템플릿은 `...\IntentKeeper\paper\acmart-primary\`.
- 데이터셋: `evaluation/DAppSCAN/repo`(sparse checkout — `DAppSCAN-source/contracts` + `SWCsource`),
  pragma 스캔 `evaluation/DAppSCAN/scan_pragma.py` → `dappscan_ge08.json`.
