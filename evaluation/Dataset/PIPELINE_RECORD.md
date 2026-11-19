# 데이터셋 파이프라인 기록 — 입력 · 로직 · 결과 (2026-11-19, 외부 검토용)

> 이 문서는 데이터셋을 만드는 두 스크립트의 **입력, 판정 로직, 실제로 나온 결과**를 검토자가 코드와 대조할 수 있게 적은 것이다.
> 대상은 `evaluation/DAppSCAN/select_targets.py`(파일 선정)와 `evaluation/Dataset/select_functions.py`(평가 대상 파일·함수 판정)다.
> 선정 이력과 이전 외부 검토 반영 내용은 `evaluation/DAppSCAN/DATASET_RECORD.md`에 있고, 실험 설정 결정은 `evaluation/ROADMAP.md` §0에 있다.

## 0. 한눈에

```
DAppSCAN 원천 .sol 21,457
  └─ select_targets.py (정규식·렉서 기반, 컴파일러 미사용)
       → 옵션 2 745 → 옵션 2′ 569 (주 집합) → 2′−suspected 551 (민감도)
  └─ select_functions.py (solc 타입 AST 기반)
       569 → F1 폐포 → F2 중복 제거 → F3 컴파일 → F4 본문 있는 함수 → F5 선정 함수
       → 평가 대상 파일 · 함수
```

**재현 확인(2026-11-17).** `select_targets.py`를 같은 원천에 다시 돌렸다. 745·569·551 세 목록과 21,457 파일 각각의 탈락 단계가 기존 `targets_v5.json`과 완전히 같았다.
원천은 `evaluation/DAppSCAN/repo`의 DAppSCAN 저장소이고, 커밋은 `66a56619c44770e05c2db600fa6468115ff0dcd5`(2025-03-25)다.

**숫자 혼선의 원인(2026-11-17 확인).** 지금까지 숫자는 여러 스크립트에서 서로 다른 집합과 규칙으로 나왔다.
- 1,941·1,900 은 `target_analysis.py`가 569 위에서 parse-only AST 로 낸 수다.
- 486 은 `build_deps.py --dedup-only`가 **551** 위에서 낸 수다. 이것은 기록상 "민감도" 집합인데 출발점으로 잘못 쓰였다.
- 482·480 은 손으로 판정한 수라 스크립트가 없다.

`select_functions.py`는 이것을 569 에서 출발하는 하나의 스크립트로 대체한다.

---

## 1. `select_targets.py` — 파일 선정

### 1.1 입력

1. `contracts_root` = `evaluation/DAppSCAN/repo/DAppSCAN-source/contracts`. 감사 보고서 폴더마다 감사 대상 코드 사본이 들어 있다.
2. `--tracked` = `git ls-files -z` 출력. 수집한 `.sol` 목록이 git 추적 목록과 다르면 중단한다(파일 시스템 누락 검사).
3. `--xlsx`(선택) = DAppSCAN 의 감사 링크 표. provenance 태그에만 쓰고 선정에는 영향이 없다. 2026-11-17 재실행은 이 인자 없이 했고 결과가 같았다.
4. 내장 상수:
   - solc 출시 목록 0.4.0–0.8.38(기준일 2026-10-28)
   - 태그 규칙(디렉터리 이름 집합, 파일명 정규식)
   - OpenZeppelin 고유 revert 문구 30 개
   - 제외 스냅샷 manifest 2 개

### 1.2 로직 (파일마다)

1. **렉싱.** 원문에서 두 사본을 만든다. `bare` 는 주석만 공백으로 바꾸고, `mask` 는 주석과 문자열 내용까지 공백으로 바꾼다. 길이와 줄 위치는 원문과 같다.
   이후 키워드 탐색은 `mask` 에서, 문자열 값이 필요한 판정(import 경로, 지문)은 `bare` 에서 한다.
2. **스냅샷 경계.** 한 감사 폴더에 여러 저장소·커밋이 섞여 있으므로 파일마다 "스냅샷" 을 정한다.
   - 상위 디렉터리를 깊이 2 부터 내려가며 마커 파일(`package.json`, `foundry.toml`, `hardhat.config.*`, `truffle-config.js`, `remappings.txt`, `.git` 등)이 있는 첫 디렉터리를 쓴다.
   - 없으면 경로 앞 두 성분(감사 폴더/그 아래 첫 디렉터리)을 쓴다(`depth2_fallback`).
   - 실측으로 21,457 중 **21,238 이 fallback**, 219 만 마커로 정해졌다. 경계는 대부분 "감사 폴더 아래 첫 디렉터리" 다.
3. **pragma.** 파일 안 모든 `pragma solidity` 를 semver 제약으로 해석해 **교집합**을 허용 버전 집합으로 둔다.
   - 해석하는 비교자는 `^ ~ >= <= > < =` 와 `||` 다.
   - 설명되지 않는 토큰, 빈 OR 항, 따옴표 버전은 거부한다(fail-closed).
4. **태그(경로 성분 기준).**
   - `external_dependency`: `node_modules`, `@scope` 디렉터리, 알려진 패키지 이름, 스냅샷 직하의 `lib`·`vendor`·`deps` 등.
   - `test_or_mock`: `test`·`mock` 디렉터리, `.t.sol`, `Test*`·`Mock*` 파일명.
   - `verification`: `certora`, `spec`, `echidna` 등.
   - `migration`.
   - `merged_source`: 근거가 셋 중 하나일 때 붙는다. 원문에 `// File:` 마커가 2 개 이상 있거나, 경로·파일명에 flatten 표기가 있거나, **import 가 0 이고 최상위 선언이 3 개 이상이며 OpenZeppelin 고유 revert 문구가 2 개 이상**인 경우다(라이브러리 본문이 복사됐다는 근거).
   - `suspected_flattened`: import 0 이고 최상위 선언 3 개 이상인데 위 근거가 없다. **제외하지 않고 태그만 단다.**
   - `canonical_names`: 표준 계약과 이름만 같다. 태그만 단다.
   - `project_lib_dir`: 프로젝트 자작 `lib/`. 태그만 단다.
5. **함수 수.** `mask` 에서 assembly 블록을 지운 뒤 `function 이름(` 다음에 `;` 보다 `{` 가 먼저 오는 것을 센다(정규식 근사).
   constructor·receive·fallback 은 따로 센다.
6. **1차 탈락(폐포 전).** 다음 순서로 처음 걸리는 사유 하나를 기록한다.
   pragma 구문 이상 → pragma 없음 → 허용 집합이 빔 → 0.8 미만을 허용 → 제외 태그(`external_dependency`, `test_or_mock`, `verification`, `migration`, `merged_source`) → 본문 있는 함수 0.
7. **import 해석.** 같은 스냅샷 안에서만 해석한다. 순서는 상대 경로 → 스냅샷의 remapping(`remappings.txt`, `foundry.toml`) → 스냅샷 루트 기준 → 같은 디렉터리다.
   파일 이름 접미사로 추측하지 않고, 다른 스냅샷으로 해석하지 않는다.
   실측 결과는 relative 38,122, snapshot_relative 108, same_dir 10, relative_missing 1,093, unresolved 11,300(그중 `@scope` 패키지 9,465)이다.
8. **폐포 검사(후보마다).** 전이적 import 폐포에 대해 다음 순서로 검사한다.
   미해결 import 0 → 폐포 모든 파일의 pragma 유효 → 전부 0.8 이상 → **폐포 전체에 공통 solc 버전이 있음** → 그 교집합의 최소가 0.8 이상.
9. **집합 구성.**
   - 옵션 2 = 폐포 검사 통과 전부.
   - 옵션 2′ = 옵션 2 에서 `EXCLUDED_SNAPSHOTS` 2 개를 뺀 것. 두 스냅샷은 감사 폴더 안에 통째로 들어 있던 OpenZeppelin 라이브러리 저장소 자체다.
     `Chainsulting-Global Rockstar/openzeppelin-contracts-upgradeable-master`(104 파일)와 `Chainsulting-SWAPP Protocol-project2/openzeppelin-contracts-4.2.0`(72 파일)이며, 감사 대상 프로토콜이 아니라 범용 프레임워크라서 뺀다.
   - 2′−suspected = 옵션 2′ 에서 `suspected_flattened` 태그 파일을 뺀 것. 민감도 분석용이다.

### 1.3 결과 (funnel)

1. 수집: 21,457 (git 추적 목록과 일치).
2. 1차 탈락:
   - 0.8 미만 허용 17,097 · pragma 없음 138 · pragma 구문 이상 7 → 남은 4,215.
   - 태그 1,331 → 남은 2,884. 태그별로는 test/mock 775, external dependency 470, merged source 119, verification 26, migration 19 이다.
   - 본문 있는 함수 0 이 1,125(그중 특수 함수만 있는 파일 50) → **후보 1,759**.
3. 폐포 탈락: 미해결 import 989, 폐포에 0.8 미만 25, 공통 버전 없음 0 → **옵션 2 = 745**.
4. OpenZeppelin 저장소 2 개(104 + 72) 제외 → **옵션 2′ = 569** (102 스냅샷, 95 감사 폴더).
5. `suspected_flattened` 18 제외 → 551. 빠진 18 개는 veMULTI `ve.sol`, Compound Governance, Hacken BSB Staking 3 종, QuillAudits 토큰들, Keep3r StakingRewards 2 개, Fei GovernorAlpha 등이다. 대부분 실제 프로토콜의 단일 파일 계약이다.

### 1.4 검토할 점 (이 세션의 판단)

1. **스냅샷 경계가 대부분 추정이다.** 99% 가 "감사 폴더 아래 첫 디렉터리" 규칙이다. 그 아래에 저장소가 여러 개 있으면 경계가 넓어진다.
   import 해석이 그 경계를 넘지 않으므로, 넓어진 경계 안에서 같은 이름의 다른 파일로 잘못 해석될 여지가 있다.
2. **함수 수와 pragma 는 정규식이다.** 컴파일러를 쓰지 않는다. 최종 함수 판정은 §2 의 타입 AST 로 다시 하므로 여기서의 함수 수는 "0 인가" 만 의미가 있다.
3. **미해결 import 989 가 가장 큰 탈락이다.** 외부 패키지(`@openzeppelin/...`)를 복원하지 않는다는 결정(DATASET_RECORD §3.2) 때문이다. 이 결정은 표본을 "외부 패키지 없이 자급하는 파일" 로 편향시킨다.
4. **`suspected_flattened` 제외는 근거가 없다.** 규칙상 태그만 다는 유형인데 551 이 이를 뺐다. 주 집합은 569 다.
5. **OpenZeppelin 제외는 manifest(이름 목록)다.** 규칙이 아니라 손으로 지정한 것이라 판단 근거(범용 프레임워크 저장소 자체)를 기록으로 남긴다.

---

## 2. `select_functions.py` — 평가 대상 파일·함수

### 2.1 입력

1. `targets_v5.json` 의 파일 목록. 기본은 `selected_option2prime`(569)이고, `--fileset` 으로 바꿀 수 있다.
2. 원천 소스(위와 같음), `evaluation/solc/solc-0.8.*.exe`(설치 13 개, sha256 검증은 `fetch_solc.py`).
3. 재사용 모듈:
   - `verify_normalize.load_closure`: 폐포
   - `normalize_source.normalize`: 정규화
   - `compile_survey`: solc 선택, 컴파일, 호출 지점 분류

### 2.2 로직

**파일 단계** (F1–F2 는 순차 실행이라 결정적이다. F3 이후는 타깃마다 독립이라 병렬이어도 결과가 같다.)

1. **F1 폐포.** `load_closure` 로 import 폐포를 구한다.
   이 해석기는 스냅샷이 아니라 감사 폴더 안에서 찾고, 비상대 경로는 파일 이름 접미사로 찾는다. 선정 해석기와 다르지만, 2026-11-17 에 측정해 보니 569 전부에서 두 해석기의 폐포가 같았다.
   해석에 실패하면 `F1_closure_unresolved` 다.
2. **F2 중복 제거.** 키 = (타깃 내용, 폐포의 다른 파일들의 (타깃 기준 상대경로, 내용) 집합). 내용은 주석을 지운 뒤의 **토큰열**(문자열 리터럴은 안의 공백까지 한 토큰으로 보존) sha256 이다.
   (2026-11-19 외부 검토 반영. 이전 판은 공백을 전부 지워 `"a b"`≡`"ab"`·`uint a`≡`uinta` 였고, 폐포 파일의 경로를 키에 넣지 않았다.)
   같은 키면 경로 정렬 순으로 첫 파일을 남기고, 나머지는 `F2_duplicate` 로 둔다.
   `build_deps.py` 는 타깃 내용만 비교했다. 그래서 폐포가 다른 4 그룹까지 하나로 합쳐졌는데, 이번 규칙에서는 이 4 그룹이 분리된다.
3. **F3 컴파일.**
   - solc 는 폐포 모든 파일의 pragma 교집합 중 설치된 최대 버전이다. optimizer off, `bytecodeHash = none` 이고, **정규화 소스**를 컴파일한다.
   - 실패하면 `F3_compile_fail` 이고 보완 규칙은 없다. 원본이 컴파일되는지와, 원본과 정규화본의 바이트코드가 같은지도 기록한다.
4. **F4 후보 함수.** 타깃 파일 AST 의 `FunctionDefinition` 중 `kind` 가 `function`(또는 free function)이고 본문이 있는 것을 고른다.
   constructor·receive·fallback·modifier 는 넣지 않는다. 후보가 0 이면 `F4_no_body_function`(stub) 이다.
5. **F5 선정 함수.** 후보 중 아래 규칙으로 선정된 함수가 0 이면 `F5_no_selected_function` 이다. 하나 이상이면 `selected` 다.

**함수 단계** (타입 있는 AST, 엔진 실행 전 고정)

1. **계층.** T1 = 일반 계약(`contractKind == contract`)의 `public`/`external` 함수. T2 = 그 밖(internal/private, library 함수, free function). abstract 계약 소속은 태그로 남긴다.
2. **구조 계층.** 함수 본문(modifier 제외)에 다음 중 하나가 있으면 들어간다.
   - 비-builtin 호출(`compile_survey.classify_call` 이 분류하는 호출: 내부·상속·library·`this`·외부·저수준·`new`·함수 포인터, 그리고 어셈블리 `call`/`delegatecall`)
   - 분기(`if`, 삼항, `try`, Yul `if`/`switch`)
   - 루프(`for`, `while`, `do-while`, Yul `for`)

   builtin 은 `require`, `keccak256`, `abi.*`, 형 변환, struct 생성자, `emit`, 배열 `push` 등이다.
   예전 규칙(`target_analysis.py`)과 같은 의미지만 parse-only 가 아니라 타입 AST 로 판정한다.
3. **연산 계층.** 구조가 없는 함수 중 다음 중 하나를 만족하면 들어간다.
   - (A) 셀 문장이 3 개 이상이고(`emit`·placeholder 제외. 선언, `require`, `return`, 대입은 센다), **입력 의존** 산술 연산이 하나 이상 있다.
   - (B) 한 문장 안에 산술 연산자(`+ - * / % ** << >>`, 복합 대입, `++ --`, 단항 `-`)가 2 개 이상 있거나, 두 피연산자가 **모두 입력 의존**인 비선형 연산(`* / % ** << >>`, `*= /= %=` 등)이 있다.
   - **컴파일 시점 상수 부분식은 산술로 세지 않는다**(2026-11-19 사용자 결정). 리터럴 · constant 상태변수 · `type(T).min/max` 만으로 된 식(`-2**127`, `10**18`)이 해당한다.
     엔진에서도 단일 값이라 구간 연산을 거치지 않기 때문이다. (A)·(B) 모두에 적용되고, 뺀 수는 `arith_const_ops` 특징으로 남긴다.
     이 규칙으로 빠진 함수는 7 개다. SafeCast `toInt8–128` 5 개(`require(value >= -2**127 && value < 2**127)` — 상수는 비교 기준값일 뿐 어떤 변수로도 흐르지 않는다)와
     `setTransferLimitAndLimitStatus` 2 개(`transferLimit = trsLimit * (10**18)` — 입력이 지나가는 연산은 `*` 하나)다.
   - **입력 의존**의 정의: 파라미터, 상수가 아닌 상태변수(immutable 포함), `msg`/`block`/`tx` 멤버, `address(this).balance`, 그리고 이것들에서 대입받은 지역변수. 구조가 없는 함수는 직선형이므로 문장 순서대로 한 번 전파한다.
     단순 대입 `=` 은 rhs 에 맞춰 의존을 **덮어쓴다**(입력 무관 값을 대입하면 파라미터·상태변수라도 그 뒤로는 비의존). 복합 대입은 기존 의존에 rhs 의존을 더한다.
     튜플 대입은 rhs 가 의존이면 lhs Identifier 를 모두 의존으로 두고(보수적), 그 수를 `taint_unhandled_assign` 특징으로 남긴다.
     (2026-11-19 외부 검토 반영. 이전 판은 `x = a; x = 0;` 뒤에도 x 를 의존으로 봤다.)
   - 구조가 없는 함수에는 비-builtin 호출이 없다. 그래서 "내부 callee 를 펼친 뒤 셀지" 는 따질 필요가 없다.
4. **T2 얇은 래퍼 제외**(2026-11-19 사용자 결정). T2 이고 구조 계층에 들어온 함수 중 다음을 모두 만족하면 단독 타깃에서 뺀다(`below_criterion:t2_thin_wrapper`).
   - 분기·루프·어셈블리가 없고, (상수를 뺀) 산술이 0 이고, 셀 문장이 2 개 이하다.
   - 본문의 비-builtin 호출이 **전부 본문 있는 정의로 하강하는 CG1** 이다(`calls_cg1_with_body == nonbuiltin_calls`). 예: `mulWadDown(x, y) { return mulDivDown(x, y, WAD); }`.
   근거: 계산이 callee 에 있고 callee 는 따로 선정된다. 외부·저수준 호출(`token.call(...)` + `require`)이나 어셈블리 호출이 있으면 그 호출이 곧 내용이므로 래퍼로 보지 않는다
   (첫 판은 이 조건 없이 385 개를 뺐다가 `TransferHelper.safeTransfer` 류까지 빠져 좁혔다).
   빠진 래퍼도 코드·폐포에서 지우지 않는다. 호출자의 실행 안에서는 그대로 하강·관찰된다(callee 관찰만). T1 에는 적용하지 않는다(T1 은 진입점이라 래퍼라도 단독 실행 단위다).
5. **선정.** 구조 계층 ∨ 연산 계층(위 래퍼 제외 후). 나머지는 제외 사유를 남긴다. view/pure 이면 `below_criterion:view_pure`, modifier 가 있으면 `below_criterion:modifier_guarded`, 그 밖이면 `below_criterion:other` 이고, 이 순서로 처음 걸리는 사유를 쓴다.
   이름은 "디버깅 가치 없음"이 아니라 "사전 복잡도 기준 미달"을 뜻한다(2026-11-19, 이전 이름 `trivial_*`). 제외된 함수도 엔진은 정상 분석하며, 다른 타깃의 callee 로는 그대로 실행된다.
6. **태그.**
   - 식별자: `definition_id`(스냅샷|파일|계약|AST src 바이트 범위)
   - 호출 지점 CG 집계: `compile_survey.survey_function`. 내부 callee·modifier 본문까지 따라간다.
   - `cg3_effect`: CG3 에서 컨트랙트 타입 view 호출을 뺀 수(ROADMAP §0.A-6)
   - `has_delegatecall`
   - `is_root`: 폐포 안 어떤 함수·modifier 본문도 CG1 로 이 정의를 부르지 않는다. 외부 호출과 virtual 디스패치는 간선으로 치지 않는다.
   - `requires_storage_binding`: storage 참조 파라미터가 있음
   - 파라미터 이름·타입·위치
   - modifier 이름과, 그중 접근 통제처럼 보이는 이름의 수(이름 기반)
7. **원시 특징 보존.** 문장 종류별 수, 산술 연산 수, 입력 의존 산술 수, 문장당 최대 산술 수, 비선형 수, 분기·루프·호출 수를 함수마다 저장한다. 그래서 (A)/(B) 기준을 바꿔도 다시 컴파일하지 않고 재집계할 수 있다.

### 2.3 결과

2026-11-19 에 569 전부를 실행했다(약 2 분, `--jobs 6`). 아래는 §2.2 의 최종 규칙(상수 산술 제외, T2 얇은 래퍼 제외 포함) 결과다.
같은 날 첫 판(두 규칙 없음)의 수치는 이 절 끝 "첫 판 대비" 에 남긴다.

**파일 funnel**

1. 입력 569. F1 폐포 해석 실패 0.
2. F2 중복 62 개(40 그룹) 제외 → 507.
3. F3 컴파일 실패 5 개 → **502 컴파일**. 실패한 5 개는 모두 원본도 같은 설정에서 실패한다.
   - token_audit: `ERC20` 누락.
   - Compound cToken: 0.8.38 에서 override 명시 TypeError.
   - eden governance 1 개, Keep3r StakingRewardsV3 2 개: optimizer off 에서 Yul 코드 생성 오류(stack too deep 류). Keep3r 두 개는 551 에서 빠졌던 `suspected_flattened` 파일이다.
4. 컴파일된 502 개 전부 원본과 정규화본의 바이트코드가 같다.
5. F4 본문 있는 함수 0: 0 개.
6. F5 선정 함수 0: **87 개** → **평가 대상 파일 415**.
   이 87 개는 `Context.sol`, `Ownable.sol`, `ERC165.sol`, 인터페이스 형태의 빈 구현처럼 기준 미달 함수만 있는 파일과, 선정 함수가 T2 래퍼뿐이던 파일(`Pausable.sol` 등)이다.
7. 평가 대상 파일의 구성: T1 만 132, T1·T2 모두 138, T2 만 145.

**함수**

1. 컴파일된 502 파일의 후보 함수는 4,360 개다(T1 2,502 · T2 1,858).
2. **선정 2,653 개.**
   - T1: 구조 1,587 + 연산 21 = 1,608.
   - T2: 구조 880 + 연산 165 = 1,045. 이 중 폐포 안 CG1 호출자가 있는 것 581.
   - 연산 계층 186 개를 규칙별로 나누면 (A)만 93, (B)만 67, 둘 다 26 이다.
3. 제외 1,707 개.
   - T1: view/pure 476, modifier 있음 304, 기타 114.
   - T2: view/pure 320, modifier 3, 기타 175, **얇은 래퍼 315**(library 179, storage 참조 파라미터 102, 폐포 안 호출자 있음 83).
4. 선정 함수의 태그 분포.
   - `cg3_effect` 있음 782
   - `delegatecall` 있음 47
   - `is_root` 1,892
   - storage 참조 파라미터 117
5. (A) 문장 계수의 민감도(연산 계층 수).
   - 선언·`require` 를 모두 셈: 186
   - `require` 를 빼면: 133
   - 선언을 빼면: 151
   - 둘 다 빼면: 110
6. 스냅샷 편중. 선정 함수는 87 스냅샷에 있고, 상위 1·5·10 스냅샷이 각각 14.3%·49.5%·62.8% 를 차지한다(최대 MahaDAO). 함수 단위(micro)와 스냅샷 단위(macro)를 함께 보고한다.

**첫 판 대비 (같은 날, 두 규칙 적용 전)**

1. 첫 판: 평가 대상 파일 428, 선정 2,975(T1 1,610 · T2 1,365), 연산 계층 193.
2. 상수 산술 제외로 7 개(T1 2 · T2 5), T2 래퍼 제외로 315 개가 빠졌다. 파일은 13 개가 빠졌다
   (MahaDAO `ERC20Pausable`·`Pausable`·`SafeCast`, Zer0 `Pausable`·`ERC721Pausable`·`ERC1155Pausable`, API3 `RoleDeriver`·`WhitelistRoles`,
   Coinfabrik `Roles`, AaveV3 `InitializableImmutableAdminUpgradeabilityProxy`, eden `ReentrancyGuardUpgradeable`, Origin `InitializableGovernable`, Seaport `ConsiderationBase`).
3. F2 키·`Taint` 수정·`line_map` 검사 활성화는 첫 판과 비교해 판정 변화 0 이다(§2.5).

**예전 수치와의 대조** (아래 1–3 은 첫 판 428 기준이다)

1. `target_analysis.py` 의 T1 실질 타깃(569 위, parse-only)은 1,936 개였다. 문서에는 1,941 로 적혀 있다. 함수를 (파일, 계약, 이름, 파라미터 수)로 맞춰 보면 다음과 같다.
   - 여전히 구조 계층: 1,582
   - 이번에 **구조가 없다고 판정**: 274
   - 파일이 컴파일 실패: 46
   - 파일이 중복: 34
2. **274 개는 예전 규칙의 과다 계수다.** parse-only AST 는 형 변환(`address(x)`)과 옛 문법의 이벤트 호출(`NewDelay(...)`)을 "비-builtin 호출" 로 셌다. 예를 들어 `Timelock.setDelay` 에는 `require` 3 개, 형 변환 1 개, 이벤트 1 개만 있는데 구조 계층으로 들어가 있었다.
   타입 AST 는 이것들을 builtin 으로 분류한다.
3. 예전 486(551 기반, 타깃 내용만 비교한 중복 제거)과 비교한다.
   - 이번 428 중 410 이 486 에 있었고, 18 개가 새로 들어왔다. 새 파일은 `suspected_flattened` 파일과, 폐포가 달라 분리된 중복 그룹에서 나왔다.
   - 486 에 있던 76 개가 빠졌다. 73 개는 F5(사소 함수만), 3 개는 컴파일 실패(token_audit, Compound, eden)다.

### 2.4 검토할 점

0. **T2 에도 같은 규칙을 적용했다(정책 변화).** 예전에는 실질 타깃 규칙을 T1 에만 적용하고 T2 는 전부 대상이었다.
   이번에는 ROADMAP §0.A-5("실질 타깃 전부 + 연산 계층 기준")를 T1·T2 모두에 적용했다. 그래서 `_msgSender()` 같은 기준 미달 T2 함수가 빠지고, 그것만 있는 파일이 평가 대상 파일에서 빠진다(최종 87 개, 래퍼 제외분 포함).
   빠진 파일도 다른 타깃의 폐포 구성원으로는 그대로 쓰인다.
   **결정(2026-11-19 사용자):** T2 도 단독 디버깅 대상이다(internal·library 함수도 그 기능을 따로 파악해야 하므로). 다만 얇은 래퍼는 뺀다(§2.2-4). 보고는 tier 별 분모로 나눈다.
1. **(A) 의 문장 계수.** 선언과 `require` 를 문장으로 셀지는 미정이었다. 지금은 센다. 세지 않으면 (A) 로 들어오는 함수가 줄어든다.
   원시 특징(`stmts_decl`, `stmts_require`)이 있으므로 재집계할 수 있다.
2. **checked arithmetic overflow 만 있는 경우.** `x += a` 하나만 있는 함수는 (A) 의 문장 수에서 걸러지지만, 문장이 3 개 이상이면 들어온다. ROADMAP §0.2-2 (1) 의 쟁점은 별도 규칙으로 걸러내지 않았다.
3. **입력 의존의 전역.** `msg`/`block`/`tx` 멤버와 `address(this).balance`(`this.balance`)를 입력으로 넣었다. 둘 다 엔진의 `@GlobalVar` 슬롯이다(`engine_api.py` 1244·1411, `Evaluation.py` 3656).
   다른 주소의 `.balance` 는 엔진이 지원하지 않으므로 넣지 않았다. 2026-11-19 에 balance 를 넣기 전과 비교했는데, 판정이 바뀐 함수는 0 개다.
4. **builtin 경계.** `abi.encode`, `keccak256` 등은 builtin 으로 보고 구조로 치지 않는다. Step Into 대상이 아니기 때문이다.
5. **`is_root`.** 외부 호출(`this.f()` 제외)과 virtual override 디스패치는 간선으로 치지 않는다. 같은 프로젝트의 다른 타깃 파일에서 부르는 경우도 그 타깃의 폐포에 이 파일이 들어 있어야만 보인다.
6. **modifier 본문.** 구조 판정에 modifier 본문을 넣지 않는다. 예전 규칙과 같다. modifier 에만 분기가 있는 setter 는 연산 계층 규칙으로만 들어올 수 있다.
7. **F1 해석기.** 선정과 다른 해석기를 쓰지만 569 에서 결과가 같음을 확인했다. 코드를 하나로 합치는 정리는 남아 있다.
   (외부 검토 제안: `select_targets` 가 폐포를 manifest 로 남기고 F1 이 일치를 assert. 미적용.)

### 2.5 외부 검토 반영 (2026-11-19)

**수정하고 다시 돌린 것.** F2 내용 키(토큰열·문자열 보존 + 폐포 상대경로), `Taint.feed` 덮어쓰기, 제외 사유 이름(`below_criterion:*`),
`verify_normalize.py` 의 꺼져 있던 `line_map` 단조성 검사(`if False` 제거). 569 를 다시 돌린 결과 파일 단계·중복 대응·함수 선정이 **모두 이전과 같다**
(제외 사유 이름만 바뀜). 튜플 대입이 있는 함수는 50 개(`taint_unhandled_assign`)이고 선정 여부에는 영향이 없다.

**검토가 남긴 미확인 사항을 확인한 것.**

1. F2 에서 옛 `normalized_hash`(공백 접기)가 달랐던 3 쌍(MerkleProof 1, Address 2)은 주석을 지운 토큰열(문자열 포함)이 같다. 차이는 주석과 파라미터 목록 줄바꿈뿐이다.
   62 개 중 30 은 원문 sha 동일, 29 는 `normalized_hash` 동일, 3 은 이 경우다.
2. `line_map` 단조성을 켜고 569 폐포 파일 940 개 전부를 검사했다. 위반 0, 키 누락 0, 원본 줄 범위 밖 0.
3. 스냅샷 경계. 569 의 경계 근거는 SHA 이름 디렉터리 503, 그 하위 36, low 30 이다. low 30 개는 17 개 감사 폴더로, 폴더마다 `.sol` 이 1–13 개이고 같은 파일 이름이 두 번 나오는 폴더는 없다(여러 버전이 섞인 흔적 없음).
   569 의 폐포가 스냅샷 밖으로 나가거나 SHA 디렉터리 두 개를 가로지르는 경우는 0 이다.
4. 루트 `lib/` 규칙만으로 `external_dependency` 가 되어 빠진 파일은 사실상 1 개(`lib/EnumerableSet.sol`, OpenZeppelin 사본)다.
5. pragma 모집단. `pragma_allows_below_0.8` 17,097 개 중 0.8.x 도 허용하는 혼합 범위가 파일 단위로 1,675 개다(`>=0.4.24` 847, `>=0.6.11` 251, `>=0.5.0` 197 …).
   이 파일들도 뒤 단계를 거치므로 데이터셋 손실 수는 아니다. 모집단은 "폐포의 pragma 가 0.8.x 만 허용하는 파일"로 서술한다.
6. require 가 있는 제외 함수는 303 개(T1 183 / T2 120)다. 셀 문장이 정확히 2 개인 것이 172, 상태 변경 함수가 209, 입력 의존 산술이 있는 것이 45 다.
   입력 의존 산술이 있는데 빠진 함수는 전체 122 개(T1 16 / T2 106)이고 모두 문장 2 개 이하(`require(...); balance -= amount;` 류)다.
7. (B) 의 상수 산술. 상수로만 된 부분식(`-2**127`, `10**18`)을 연산자 수에서 빼면 7 개가 빠진다(SafeCast `toInt8–128` 5 개 T2, `setTransferLimitAndLimitStatus` 2 개 T1). **반영함**(사용자 결정, §2.2).
8. T2 단독 대상 기준. 사용자 결정으로 얇은 래퍼(§2.2-4)를 단독 타깃에서 뺐다. 분기·루프가 있는 T2 는 남긴다.

## 3. 재현 방법

2026-11-19 에 `evaluation/Dataset/target_functions.json` 으로 동결했다. 환경 · solc 설치 · 기대 결과 확인 방법은 `DATASET_SELECTION.md` §8.

```
# 1. 파일 선정 (약 7 분)
git -C evaluation/DAppSCAN/repo ls-files -z > tracked.z
.venv/Scripts/python.exe -I evaluation/DAppSCAN/select_targets.py evaluation/DAppSCAN/repo/DAppSCAN-source/contracts targets_v5.json --tracked tracked.z
# 2. 평가 대상 파일·함수
.venv/Scripts/python.exe evaluation/Dataset/select_functions.py evaluation/Dataset/target_functions.json --jobs 6
```
