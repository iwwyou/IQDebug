# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

프로젝트명 = IntentKeeper(repo) / **IQDebug**(툴). SolQDebug + IntentChecker 엔진을 확장한
intent-aware quick debugger. 상세 진행상황·설계결정 로그는 `PROGRESS.md`가 단일 진실원이다.

## 작업 원칙 (사용자가 명시한 규칙)

1. **항상 객관적이고 비판적으로 검토한다.**
   사용자의 의견·아이디어를 무비판적으로 동조하지 않는다. 좋은 점은 좋다고 말하되,
   약하거나 문제가 있는 부분은 분명히 지적하고 더 나은 대안을 제시한다.

2. **나는 제안 도구이며 최종 판단은 항상 사용자가 한다.**
   코드를 임의로 바로 수정하거나 변경을 적용하지 않는다. 어떤 수정·생성·삭제든
   실행 전에 사용자에게 먼저 물어보고 승인을 받는다.

3. **모호하면 먼저 묻는다.**
   요청이나 질의 중 모르겠거나 헷갈리는 부분, 의도가 불분명한 부분이 있으면
   추측해서 진행하지 말고 먼저 사용자에게 질의 의도를 확인한다.

4. **코어 수정은 승인 후에만** (PROGRESS.md decision #11, 항상 유효).
   코어 = `Interpreter/`(Engine + Semantics), `Domain/`, `Utils/CFG.py`,
   `Analyzer/GuardianVerificationEngine.py`, 파서·visitor. 회귀 가드가 약하므로
   (아래 "테스트" 참고) 코어 변경은 반드시 사전 승인 + 수동 검증을 거친다.

## 실행 (Windows / PowerShell)

**반드시 `.venv\Scripts\python.exe`로 실행한다.** 글로벌 Python310은 networkx 2.5.1이라 CFG 백엔드가 깨진다.

```powershell
# 단일 케이스(레코드 JSON) 실행 — 라인별 분석 출력. 의존성은 프로젝트 deps 루트를 환경변수로 지정
$env:IQ_DEPS_ROOT = "evaluation\Dataset\deps\w5"
.venv\Scripts\python.exe main.py evaluation\intent_validation\cases_rebuilt\web3bugs_5_H_07.json
# 같은 케이스를 verdict 요약만 출력 (main.simulate_inputs(silent=True) 래퍼)
.venv\Scripts\python.exe run_case.py <case.json>
# 48 케이스 전체 (cases_rebuilt + 케이스별 프로젝트 deps 자동 지정)
.venv\Scripts\python.exe evaluation\refactor\baseline_cases.py --tag <tag>

# 프론트가 붙는 엔진 HTTP 서버 (uvicorn, 127.0.0.1:8000)
.venv\Scripts\python.exe engine_server.py

# 의존성: 프로젝트 단위 레지스트리 (2026-10-23). 원본 소스 파일 하나 = 단위 하나(전처리본 deps/<p>/sources/*.sol)
#   → deps/<p>/builds/build_NNN/{manifest.json, units/*.pkl}, deps/<p>/CURRENT 가 현재 빌드를 가리킨다.
.venv\Scripts\python.exe evaluation\Dataset\build_project_deps.py [--only w62]      # 전처리 + 전체 빌드
.venv\Scripts\python.exe Dependencies\registry.py --root evaluation\Dataset\deps\<p>  # sources/ 재빌드
.venv\Scripts\python.exe Dependencies\registry.py --root <deps> --add X.sol --remove <단위id>  # 증분(R4)

# 파서 재생성 (Parser/ 에서; CLI antlr4 4.13.2 = runtime 버전과 반드시 일치)
antlr4 -Dlanguage=Python3 -visitor Solidity.g4
```

프론트엔드:

```powershell
cd frontend; npm install   # 1회
npm run dev                # Vite 5173 (점유 시 5174)
npx tsc --noEmit           # 타입체크
```

## 테스트

정식 테스트 프레임워크(pytest 등)가 없다. 회귀 스크립트는 `temp/`(gitignored)의 단독 실행 파일이며
각자 `sys.path` 를 조정하고 자체 `check()` 로 pass/fail 을 센다:
`.venv\Scripts\python.exe temp\test_session_flow.py` (analyze→update→evaluate→verify e2e),
`temp\test_update.py`·`test_delete_cfg.py`(증분 편집), `temp\test_transaction.py`(증분 트랜잭션),
`temp\test_registry.py`(의존성 레지스트리), `temp\test_try_catch.py`, `temp\test_blank_during.py`(빈 줄 @During 지점), `temp\test_sparse_array.py`(희소 배열), `temp\test_set_intents.py`(intent → 슬롯, 서버), `temp	est_slot_roundtrip.py`(슬롯 키 ↔ 입력 문법, 48 케이스), `temp	est_ireturn_cast.py`(IReturn 캐스트 조회),
`temp\smoke_engine_api.py`(스모크).

⚠ **과거 회귀 게이트였던 `evaluation\RQ1\run_all.py` → 20/20 VIOLATED 는 더 이상 유효하지 않다**
(decision #37 문법 변경으로 의도된 0/20 회귀). 현재 e2e 회귀 스위트는 사실상 없으므로,
코어를 건드리는 변경은 `temp/` 스크립트로 직접 확인해야 한다.

## 아키텍처

### 분석 파이프라인 (한 줄씩 증분 소비)

`.sol` 소스 → `Dependencies.main.slice_solidity()` 가 **record**(`{code, startLine, endLine, event,
closeBefore?}`) 리스트로 슬라이스 → `SolidityAnalyzer.update_code()` 가 라인 저장소·시프트 관리 →
라인마다 현재 컨텍스트(`get_current_context_type()`)에 맞춰 ANTLR 부분 파싱
(`Utils/Helper.ParserHelpers`; assembly 안이면 `Utils/YulHelper`) → `EnhancedSolidityVisitor`
(Yul은 `EnhancedYulVisitor`)가 `ContractAnalyzer` 를 구동 → `DynamicCFGBuilder` 가 `Utils/CFG.FunctionCFG`
를 **점진적으로** 구성 → `Interpreter/Engine.py` 가 CFG 위에서 abstract interpretation(fixpoint) 수행 →
`Analyzer/GuardianVerificationEngine.py` 가 @During/@Post intent 를 검증 → 결과는
`RecordManager.ledger` 와 `ContractAnalyzer.analysis_per_line` 에 적재.

핵심은 "파일 단위 컴파일"이 아니라 **에디터 이벤트(add/modify/delete) 단위의 증분 분석**이라는 점이다.
CFG·환경이 라인 이벤트마다 수술되므로, 라인 좌표·`closeBefore`·블록 join 처리가 전체 정합성을 좌우한다.

### 두 개의 진입 경로

1. **배치/CLI**: `main.py` (전역 `sa`/`contract_analyzer`/`batch_mgr` 싱글턴 + `load_dependencies()`) →
   `simulate_inputs(records)`. `run_case.py` 가 이를 감싸 verdict 요약을 낸다. 어노테이션이 소스에
   섞인 레거시 경로이며 `// @Debugging BEGIN/END` 로 디버그 배치를 구분한다.
2. **서버/API**: `engine_api.EngineSession` (analyze / update / structure / evaluate / verify) 를
   `engine_server.py`(FastAPI)가 `/analyze /set_intents /evaluate /verify /analyze_once` 로 노출 →
   `frontend/src/engine/analyzeClient.ts`. 이쪽이 현재 개발 주력이다.

### engine_api 레이어 계약

- `analyze` = 코드 변경 트리거 → `Structure`(CFG 구조 + 빈 debug 슬롯 + diagnostics)
- `evaluate` = debug 값 변경 트리거 → `CfgWithStates`(fixpoint states, frozen 스냅샷)
- `verify` = intent 변경 트리거 → `list[VerdictItem]` (보존된 states 위 standalone 검증)
- 체인 종속: analyze → evaluate → verify. dataclass 들은 `frontend/src/types/result.ts` 와 1:1 계약이므로
  한쪽만 바꾸면 조용히 깨진다.

### 어노테이션 두 종류 (혼동 주의)

- **debug 값**(`@StateVar/@Param/@GlobalVar/@IReturn`) = 슬롯 채우기. (`@Param` 은 2026-10-20 `@LocalVar` 를 대체 —
  기존 case JSON 의 `// @LocalVar` 는 드라이버에서 변환한다.) 엔진이 슬롯을 도출
  (`engine_api._derive_debug_slots`), 사용자는 값만 넣는다. `DebugBatchManager.flush()` 가 한 번에
  해석하고 **restore 로 값을 지운다** — 그래서 `engine_api` 는 restore 전에 states 를 freeze 해야 한다.
- **intent**(`@During/@Post`) = 코드의 영구 일부. `process_during/process_post` 로 CFG 노드에 부착.
- 문법은 `Parser/Solidity.g4`. **decision #37(2026-07-18) 이후 어노테이션 규칙에 `'//'` 접두어가 없다**
  (`@During x > 0`). `//` 는 일반 `LINE_COMMENT`(HIDDEN) 전용이 되어 실제 `.sol`(SPDX 주석 등)이 파싱된다.

### 추상 도메인

`Domain/`: `Interval.py`(Unsigned/Signed/Bool interval), `AddressSet.py`(symbolic address),
`BytesSet.py`, `Variable.py`(Mapping/Array/Struct/Enum 변수), `IR.py`(Expression), `Type.py`.
transfer function 은 `Interpreter/Semantics/`(`Evaluation`=식 평가, `Update`=대입/상태갱신,
`Refine`=조건 refine, `DebugInitializer`=debug 값 → 추상값 주입)에 분리돼 있다.

### 증분 편집 (B1)

`incremental.py` 는 **순수 함수** diff 레이어다 — 엔진을 import 하지 않고 prev/new record 시퀀스만
비교해 `EditOp` 를 낸다. 적용은 `EngineSession.update()` 담당(SyntaxValid 게이트 → delete=
`delete_cfg_range` SESE 수술, 조건 modify=in-place, simple modify=delete+add). 좌표계가
**slice 정규화 라인 공간**이라 Monaco 라인과 별개라는 점이 설계의 핵심이다.

## 함정 (겪어본 것들)

- `EngineSession` 에 `analyze` 를 두 번 호출하면 record 가 이중 적용돼 세션이 손상된다 →
  `engine_server._build` 는 항상 새 세션을 만든다.
- `sess.structure()` 를 스코프 없이 호출하면 `_resolve_fcfg(None, None)` 폴백이 **컨트랙트의 마지막
  함수**를 집어 엉뚱한 CFG/슬롯을 돌려준다. 항상 `contract`, `func` 를 명시할 것.
- 의존성 로드는 `Dependencies.main.load_project_deps(sa, ca, deps_root, exclude)` 하나다(본체 `Dependencies/registry.load`;
  CLI `main.load_dependencies` = env `IQ_DEPS_ROOT`/`IQ_DEPS_EXCLUDE`, 서버 `EngineSession(deps_root, exclude)` 가 공용).
  `exclude` = 타깃이 선언하는 이름 — 그 선언 집합과 **같은** 단위를 타깃 파일이 통째로 대신하고 그 의존 폐포만 올린다
  (집합이 다르면 오류). deps 루트를 안 주면 의존성 없이 돈다 — 타깃이 의존성을 참조하면 분석 중 명시적 오류가 난다.
- 의존성 빌드(`Dependencies/registry.py`)는 순서에 의존하지 않는다: 단위 의존(본문에 나오는 다른 단위의 선언 이름) SCC
  순서로 분석하고, 의존 폐포의 결과가 바뀌면 다시 분석하는 고정점. 단위 상태 = Pending(미해석 이름 `UnresolvedName`)/
  Invalid(그 밖 오류·이름 충돌)/Complete — `manifest.json` 에서 확인. pkl 의 CFG 참조는 이름으로 저장돼 로드 때
  선언당 객체 하나로 연결된다. 같은 이름을 두 단위가 선언하면 둘 다 Invalid(프로젝트 전역 이름 공간).
  같은 파일 안의 전방 참조는 아직 전처리 재배치(`reorder_members`·`reorder_top_level`)로 피한다(F1 (b) 전까지).
- 분석 실패가 예외가 아니라 **조용한 오분석**으로 나타나는 경우가 많다(pkl 로드 `except: pass`,
  SyntaxValid 게이트 거부 등). 결과가 이상하면 diagnostics 부터 확인할 것.
