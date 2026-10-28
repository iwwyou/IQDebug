# ISSTA 2027 — 평가 데이터셋·타깃 모집단 구축 기록

작성 2026-10-28. 기준 산출물: `select_targets.py`(v5) → `targets_v5.json`,
`ast_manifest.py` → `ast_manifest.json`.
용도: 논문의 *Evaluation Dataset / Benchmark Construction / Experimental Setup /
Threats to Validity* 작성 근거. **이 문서는 구현 세션의 기록이고, 논문 본문은 별도 세션에서 쓴다.**

> **외부 요약본과의 차이(정정)**: 다른 LLM이 만든 진행 요약은 "AST 기반 함수 수 검증 미수행"
> 상태에서 작성된 것이다. 그 뒤 **AST manifest 를 실제로 생성**했으므로 §5 의 함수 수는 더 이상
> 정규식 추정치가 아니다. 그 밖의 정정은 §9에 모았다.

---

## 1. 데이터셋의 역할과 선정 원칙

평가 대상은 **IQDebug** — 구체 트랜잭션 trace 를 재생하지 않고, abstract interpretation(구간
도메인)으로 미리 계산한 추상 프로그램 상태 위에서 디버거 operation(Step Forward/Backward,
Step Into/Out, Breakpoint, Continue)을 수행하는 소스 수준 디버거. 논문의 기여는 **그 operation
들의 의미론**이다.

따라서 데이터셋은 **버그 벤치마크가 아니라 "실제 DApp 함수들의 모집단"** 이다. 취약점 라벨·SWC
분류·버그 oracle 을 쓰지 않는다.

선정 원칙은 세 단계 분리다. 이 분리는 외부 검토 다섯 차례에서 가장 일관되게 요구된 사항이다.

| 단계 | 내용 | 결정 시점 |
|---|---|---|
| **A. Dataset selection** | 소스 자체의 적격성(버전·의존 폐포·파일 유형) | 엔진과 무관하게 사전 결정 |
| **B. Static support classification** | 엔진이 사전 선언한 지원 범위 태깅 | AST 기준, 엔진 실행 전 |
| **C. Execution funnel** | 실제 분석 성공·실패와 operation 결과 | 실행 후 **보고**(선정에 되먹이지 않음) |

C 의 실패를 A 로 되돌려 대상을 지우면 평가가 유리하게 편향된다 — 이것이 세 단계를 나눈 이유다.

---

## 2. 원천 데이터셋

- **DAppSCAN** (`InPlusLab/DAppSCAN`, arXiv 2305.08456), 경로 구조
  `DAppSCAN-source/contracts/<감사회사>-<프로젝트>/<저장소 스냅샷>/.../*.sol`.
- 받은 범위: `DAppSCAN-source/contracts` + `SWCsource` 만 sparse checkout(약 102MB).
  `DAppSCAN-bytecode`(3.1GB)와 `audit_report` PDF(935MB)는 쓰지 않는다.
- 선정 이유: ① **프로젝트 구조가 보존**되어 import 폐포를 구성할 수 있다(엔진이 폐포 위에서 타깃
  함수를 해석한다), ② 실제 감사 대상 코드라 수치 연산·내부 호출·라이브러리 사용이 풍부하다,
  ③ 선행 연구(심사 중)의 intent 라벨 데이터셋과 겹치지 않는 모집단이 필요했다, ④ 라벨을 쓰지 않으므로
  라벨 품질이 쟁점이 아니다.
- **수집 검증**: `.sol` **21,457개** = `git ls-files` 추적 목록과 **정확히 일치**(누락 0, 추가 0).
  감사 디렉터리 608개, 스냅샷 경계 682개.
- **빌드 메타데이터는 거의 없다**: 트리 전체에서 빌드 설정을 가진 디렉터리가 **4곳**
  (`package.json` 2, `truffle-config.js` 1, `hardhat.config.js` 1, `package-lock.json` 1).
  그래서 remapping 을 읽을 수 없고(보유 스냅샷 0), 패키지 import 는 저장소 안에서 해석되지 않는다.

---

## 3. 선정 기준 (v5)

### 3.1 Solidity 버전
- **0.8.0 이상**만 대상. 0.8부터 정수 산술이 기본 checked 라, 엔진이 모델링하는 의미가 균일해진다.
- 파일 안 **모든** `pragma solidity` 를 추출해 각각을 semver 제약으로 해석하고, **교집합**을 그 파일의
  허용 버전 집합으로 둔다. 비교자 `^ ~ >= <= > < =` 와 `||` 를 처리한다.
- 버전 카탈로그는 **실제 출시된 solc 0.4.0–0.8.38**(기준일 2026-10-28, `latestRelease` 확인).
  미출시 버전을 넣으면 "허용 집합이 비지 않았다" 는 판정이 거짓이 된다.
- **fail-closed**: 설명되지 않는 토큰이 남거나(`^0.8.2 foo`), 빈 OR 항(`0.8.20 ||`)이거나,
  따옴표가 섞인 버전(`pragma solidity "0.8.13";` — 실제 7건)은 **거부**한다.

### 3.2 의존성 폐포
- 전이적 import 폐포를 **같은 스냅샷 경계 안에서만** 해석한다. 해석 규칙은 상대경로 → remapping →
  스냅샷 루트 기준 → 같은 디렉터리 순이고, **basename/접미사 추측과 다른 프로젝트로의 해석을 금지**한다.
- 후보 조건: 미해결 import 0 ∧ 폐포의 모든 파일이 유효 pragma ∧ 전부 ≥0.8 ∧ **폐포 전체에 공통 solc
  버전 존재**.
- 외부 패키지를 임의 버전으로 복원해 주 집합에 넣지 않는다.
- ⚠ 이 검사는 **정적으로 복원한 import·버전 제약의 일관성**이고 컴파일 성공을 보장하지 않는다.
  논문에서는 *heuristically reconstructed, snapshot-isolated import closure* 로 표현한다.

### 3.3 파일 유형 제외
제외: `test_or_mock`, `external_dependency`(node_modules·`@scope`·알려진 패키지·**스냅샷 루트 직하**의
lib/vendor/deps), `verification`(certora/spec), `migration`, **`merged_source`**.

`merged_source` 판정 근거(세 가지 중 하나):
1. 원문에 `// File:` 마커 2개 이상 — 도구가 만든 병합 소스의 강한 정황,
2. 경로·파일명의 flatten 표기(단어 경계 매칭),
3. import 0 ∧ 최상위 선언 3개 이상 ∧ **OpenZeppelin 본문 지문 2개 이상**
   (`"Ownable: caller is not the owner"` 류 30개 문구를 **주석 제거본의 문자열 리터럴**에서 탐색).

**제외하지 않고 태그만** 남기는 유형: `suspected_flattened`(import 0 + 선언 3개↑, 근거 없음),
`canonical_names`(표준 계약과 이름만 일치), `project_lib_dir`(프로젝트 자작 `lib/libs`).

범용 재사용 프레임워크 저장소인 **OpenZeppelin 스냅샷 2개**는 주 집합에서 제외(manifest 로 명시).
Aave V3·Compound·Frax 등 실제 프로토콜은 **유지**한다.

### 3.4 함수·타깃 범위
- 파일 선정 조건은 **본문 있는 named function 1개 이상**.
- **constructor / receive / fallback 은 타깃에서 제외**하되 **파일은 데이터셋에 남긴다**(타깃만 되지
  않고, 다른 타깃의 폐포 구성원으로는 계속 쓰인다). 집계는 별도로 한다.
  제외 사유는 "입력이 없어서" 가 아니다 — fallback 은 `fallback(bytes calldata) returns (bytes memory)`
  처럼 파라미터를 가질 수 있다. 정확한 사유는 **연구 범위를 named regular function 의 디버깅 의미론으로
  한정**하기 때문이다.
- 생성자에 대해서는 다음과 같이 써야 한다: *분석은 사용자가 지정한 추상 pre-state 에서 시작하며,
  생성자나 도달 가능한 트랜잭션 시퀀스가 보장하는 상태 불변식(`owner != 0`, `total == 100` 등)은
  복원하지 않는다(과대근사).* "입력 모델이 생성자를 대체한다" 는 서술은 과장이다.

---

## 4. 선정 funnel (v5)

동일 파일이 여러 태그에 해당할 수 있어 태그별 수는 단순 합산하지 않는다.

| 단계 | 잔여 파일 | 탈락 내역 |
|---|---|---|
| 원본 `.sol` 수집 | **21,457** | git 추적 목록과 일치 |
| pragma·버전 조건 통과 | 4,215 | 0.8 미만 허용 17,097 · pragma 없음 138 · 구문 이상 7 |
| 파일 유형 태그 통과 | 2,884 | test/mock 775 · external dependency 470 · merged source 119 · verification 26 · migration 19 |
| 적격 타깃 함수 보유 | 1,759 | 1,125 (그중 **특수 함수만 가진 파일 50**) |
| 의존 폐포 검사 통과 = **옵션 2** | **745** | 미해결 import 989 · 폐포 0.8 미만 25 · **공통 컴파일러 없음 0** |
| 프레임워크 저장소 제외 = **옵션 2′** | **569** | OpenZeppelin 2개(104 + 72) |
| suspected 제외(민감도) | 551 | `suspected_flattened` 18 |

---

## 5. 최종 집합과 AST 확정 수치

### 5.1 파일 단위

| | 옵션 2 | **옵션 2′ (주 집합)** | 2′−suspected |
|---|---|---|---|
| Solidity 파일 | 745 | **569** | 551 |
| 스냅샷 / 감사 프로젝트 | 104 / 97 | **102 / 95** | 93 / 86 |
| 중복 그룹 / 소속 파일 | 52 / 128 | 43 / 106 (18.6%) | 42 / 104 |
| 폐포 크기 1 / 2–4 / 5+ | 257 / 220 / 268 | 225 / 180 / 164 | 207 / 180 / 164 |
| 상위10 스냅샷 파일 점유 | 58.5% | 51.0% | 52.3% |
| 경계 신뢰도 low | 206파일 | 30파일 | 22파일 |
| xlsx 미등재 provenance | 25파일 | 25파일 | 25파일 |

### 5.2 함수 단위 — **AST(solc parse-only) 확정값**

`solc --standard-json` + `stopAfter: "parsing"` 으로 745개를 파싱: **성공 744, 실패 1**
(`QuillAudits-FutiraCoin/code/token_audit.sol`, `ParserError`). 파일별로 **허용 버전의 최대값**에
해당하는 바이너리를 골라 파싱했다(solc 는 parse 단계에서도 버전 pragma 를 강제한다 — 0.8.38 하나로는
558/745만 커버되어 13개 버전을 받았다). import 미해결은 파싱에 영향이 없다.

옵션 2′(568 파일 파싱 성공, 101 스냅샷) 기준:

| 계층 | 수 | 설명 |
|---|---|---|
| **T1** — ABI-visible contract function | **2,602** | 일반 `contract` 의 구현·본문 있는 `public`/`external` 함수. 전부 `contract` 종류이며 그중 **abstract 계약 소속 278** |
| **T2** | **2,126** | internal/private 1,077 + **library 1,049** |
| **타깃 합계(T1+T2)** | **4,728** | |
| `special` | 284 | constructor 248 · receive 24 · fallback 12 — 타깃 아님 |
| `empty_body` | 33 | 본문이 `{}` — 타깃 계층에서 분리 |
| `declaration_only` | 237 | 인터페이스 선언 등 |

**교차검증**: AST 의 named·implemented 함수(특수 제외) **4,761** vs 정규식 근사 **4,772** → 차이 0.2%.
정규식 추정은 총량에서는 거의 맞았지만, **가시성·계층 분해는 AST 로만 가능**했다.

구조 특성(옵션 2′ 타깃 기준):
- **빌트인 외 호출이 있는 타깃 3,414개(72.2%)** ← Step Into 가 발생할 수 있는 모집단.
  **호출이 전혀 없는 leaf 타깃 1,314개** — operation 특성이 다른 집단이라 층화 근거가 된다.
- 호출 지점 **14,654개**(빌트인 제외 **11,995**): identifier 7,864 · member 3,843 · type cast 2,668 ·
  `super` 78 · `new` 140. 전부 `resolution_basis="syntax_only"` — **internal 이라 단정하지 않는다.**
- 루프 293 · 분기 999 · `unchecked` 187 · inline assembly 162 · try/catch 9.
- **storage 참조를 파라미터로 받는 T2 348개** — 직접 디버깅에 호출 컨텍스트가 필요(`requires_storage_binding`).
- modifier **사용 928건 / 856 함수**. 6차 검토 C10 반영 — 예전의 "899" 에는 생성자의
  **base-constructor specifier 44건**이 섞여 있었다(parse-only 는 `ModifierInvocation.kind` 를 채우지
  않아 `constructor() Base(1)` 의 `Base` 가 modifier 사용과 같은 모양으로 들어온다 → base 계약 이름으로
  분리했다). 해석 상태: 선언 계약에서 해석 576 · 파일 내 유일 56 · **미해결 296(264 함수, 그중 T1 232)**.
  미해결은 상속·import 로 들어온 modifier 라 이 파일만으로는 본문을 알 수 없다 → B단계에서 확정한다.
- **placeholder 가 2개 이상인 modifier 0건** — 단 이는 **해석된 632건에 한해서만** 유효하다.
  "데이터셋에 그런 패턴이 없다" 는 근거가 아니다(미해결 296건은 알 수 없다).
- **placeholder 앞뒤에 문장이 있는 modifier 사용 623건** — 타깃 본문이 modifier 문장 **사이에 끼인다**
  (`nonReentrant`·`initializer` 류). placeholder 개수만 세던 예전 지표로는 전혀 보이지 않았고,
  탐색 의미론(Step Forward 가 본문 밖 문장을 지난다)에 직접 영향이 있다.
- 집중도(타깃 수 기준): 상위1 14.9% / 상위5 44.2% / 상위10 60.0%
  (MahaDAO 706 · Compound 396 · AaveV3 369 · Amplify 312 · eden 308).

---

## 6. 선정 로직의 개선 이력 (v1 → v5)

버전마다 **기준과 범주가 바뀌었으므로 후보 수 증감을 품질 개선율로 읽어서는 안 된다.**

| 판 | 핵심 변경 | 결과 |
|---|---|---|
| v1 | 최초 pragma·import·함수 분석, 느슨/엄격 해석 비교 | 엄격 642 |
| v2 | **탐색 누락 수정**(확장 경로 + `onerror`, 20,501 → 21,457), 프로젝트 경계 격리, cross-project 해석 금지 | 682 |
| v3 | 주석·문자열 마스킹, semver 제약 평가, 폐포 공통 컴파일러, 의심 파일 보수적 유지 | 2′ 651 |
| v4 | Yul 함수 제외·탐색 길이 제한 해제, 중복 해시 수정, 병합 재분류, `unique_suffix` fallback 제거 | 2′ 566 |
| v5 | 병합 근거를 **이름 → 본문 지문**으로, 빈 OR 항 거부, 특수 함수 집계, 근거 JSON 기록 | 2′ **569** |

측정으로 확인한 주요 결함과 규모:
- **파일 누락 956개(4.5%)** — `os.walk` 가 긴 경로 디렉터리 131곳을 조용히 건너뛴 결과. `git ls-files`
  대조로 발견했고, 확장 경로 탐색으로 21,457 전부 복구.
- **cross-project import 해석 4,195건**(끌어온 사본이 0.8 미만 913건) — 폐포 버전 판정이 무관한
  프로젝트 파일에 좌우되고 있었다. 금지 후 접미사 매칭은 3건만 남아 그 fallback 도 제거.
- **flattened 판정이 주석 제거본에서 `// File:` 을 찾아 항상 0** — 약한 신호만 작동해 **정상 파일
  100개를 과잉 배제**하고 있었다(v4 에서 682 → 782).
- **이름 기반 병합 판정의 오탐** — v4 가 제외한 123개를 본문 지문으로 재검사: 지문 ≥2 112개, 마커 18,
  경로명 20, **근거 전무 7개**. v5 에서 그 7개는 복귀하고 **본문 지문으로 3개를 새로 포착**.
- 합성 반례로만 존재하고 **실측 0건**인 결함: 한 줄 다중 문장 import 누락, 문자열 속 pragma/function
  오인, semver fail-open, 폐포 공통 컴파일러 위반, 상대경로 경계 이탈, 접미사 모호 해석.
  **0건이어도 전부 닫았다** — "빈틈이 있었지만 우연히 안 걸렸다" 보다 "닫았고 영향 0건이었다" 가
  방어 가능한 서술이다.

---

## 7. 품질·대표성

- **import 해석**(전 코퍼스): 상대경로 성공 38,122 · 스냅샷 루트 기준 108 · 같은 디렉터리 10 ·
  remapping 0 · **모호 거부 0 · 경계 이탈 0**. 미해결 **12,393**
  (scoped package 9,465 · 기타 경로 1,793 · 누락 상대경로 1,093 · 원격 24 · 단일 이름 18).
- **영향 후보 수**(각 미해결 import 가 탈락에 관여한 후보 수, **합산 불가**):
  `@openzeppelin/contracts/token/ERC20/IERC20.sol` 451 · `access/Ownable.sol` 298 ·
  `token/ERC20/utils/SafeERC20.sol` 282 · `ERC20.sol` 152 · `utils/math/SafeMath.sol` 134.
  → 옵션 3(의존성 복원)을 할 때 **우선순위 근거**이지, "복원하면 451개가 회복된다" 는 뜻이 아니다.
- **중복**: 해시 2종(`raw_hash`, 문자열 보존 `normalized_hash`). 옵션 2′ 에서 43그룹 / 106파일(18.6%).
  제거하지 않고 **재가중 민감도**로 처리한다.
- **집중도**: 옵션 2′ 상위10 스냅샷이 파일 51.0% / 함수 59.8% / 타깃 60.0%. **프로젝트당 상한을 두지
  않는다**(새로운 임의 기준이 생기고 대형 프로토콜의 복잡한 함수가 빠져 오히려 편향될 수 있다).
  대신 **함수 단위 micro + 스냅샷 단위 macro** 를 본문에 함께 보고한다.
- **스냅샷 경계·provenance**(서로 독립된 개념으로 분리 기록):
  경계 판정은 마커 219파일 / 2단 폴백 21,238파일. 저장소 루트 추정은 SHA 명명 경계 514 ·
  SHA 명명 하위 3 · 경계 그대로 165(**신뢰도 low**). `Audit_and_Repository_link.xlsx` 대조 결과
  감사 디렉터리 608개 중 **595 등재 / 13 미등재**. 옵션 2′ 안에서 경계 low 30파일, 미등재 25파일.
  둘 다 **주 집합에서 제외하지 않고** 민감도로만 본다.

---

## 8. 타깃 함수 선정 정책 (AST 기준으로 확정)

- **T1** = 일반 계약의 구현·본문 있는 `public`/`external` 일반 함수. 명칭은 **ABI-visible contract
  function** 이고 "트랜잭션 진입점" 이라 단정하지 않는다(library public, abstract 계약의 virtual 등은
  성격이 다르다). 호출 그래프 in-degree 0 을 쓰지 않는 이유: `transfer` 처럼 내부에서도 불리지만
  외부 진입점인 함수가 빠진다.
- **T2** = 그 외 구현 named 함수 — internal/private, library 함수, free function.
- **두 집합을 모두 엔진 실행 전에 AST 로 고정한다.** T2 를 "T1 실행에서 닿지 않은 함수" 로 정하면
  입력 정책과 엔진 실패가 표본 구성을 바꿔 A/B/C 분리가 무너진다.
- 함수 정체성 = `definition_id`(스냅샷\|파일\|계약\|src 바이트 범위). 실행 컨텍스트 ID 와 분리한다
  (같은 정의가 상속 컨텍스트마다 다르게 실행될 수 있다). **AST 의 `src` 는 UTF-8 바이트 오프셋**이다.
- 직접 디버깅의 입력 모델이 구성되는지 여부는 **태그**로 남긴다(`requires_storage_binding` 348 등).
  실패했다고 T2 분모에서 빼지 않는다.

### 실질 타깃 규칙과 제외 사유 (2026-10-28, `target_analysis.py`)

T1 2,602 전부를 실행 집합으로 쓰지 않는다. **본문에 탐색할 구조가 있는** 함수만 쓴다 — 비-builtin
호출(Step Into 의 대상) ∨ 분기 ∨ 루프. 결과 **실질 타깃 1,941**, 중복 파일 제거 후 **1,900**.

| 제외 사유 | 수 | 근거 |
|---|---|---|
| `trivial_view_pure_getter` | 462 | view/pure 이고 호출·분기·루프 없음 |
| `access_control_guarded_setter` | 166 | modifier 가 전부 접근 통제(onlyOwner 62 · onlyByOwnerOrGovernance 61 · onlyAdmin 9 …), 해석된 placeholder 전부 1개, **166 중 145 는 본문에 호출·분기·루프·return 이 전혀 없는 단일 대입 setter**(`setTimelock`·`setOwner`·`setAdmin` …) |
| `other` | 33 | |

6차 검토는 규칙에 `or bool(modifiers)` 를 넣어 166개를 타깃에 포함하라고 권고했으나 **채택하지
않았다**. `require(msg.sender == owner)` 의 결과는 사용자가 넣은 `@GlobalVar msg.sender`/
`@StateVar owner` 로 이미 결정되므로 추상 상태를 탐색해 알아낼 것이 없고, 타깃에 넣으면 커버리지
분모만 희석된다. **빠뜨린 것이 아니라 이 사유로 제외했다**는 사실을 기록으로 남긴다.

### overload 타깃 지정 (6차 검토 C9)

같은 계약에 이름이 같은 T1 이 **26개 그룹 / 56개 정의**로 존재한다(실질 타깃 중 54개).
예: `Vesting.balanceOf` 가 `()`·`(uint256)`·`(address)` 세 개. 엔진의 CFG 저장소는
`name → {signature → FunctionCFG}` 라 overload 를 이미 구분하지만, 이름만 넘기면
`next(iter(...))` 로 **먼저 등록된 하나가 조용히 선택**되어 **30개 정의에 도달할 수 없고**
결과가 엉뚱한 시그니처에 귀속된다. 2026-10-28 `engine_api` 에 `param_types` 를 배선했다 —
`EngineSession.set_target_param_types()` / `analyze(_source)(param_types=…)` / `/analyze` 요청 필드.
타입 어휘 차이(`uint`↔`uint256`, `user:IERC20`↔`IERC20`, `uint[] memory`↔`uint256[]`)는 정규화로
흡수하고, 그래도 못 맞추면 인자 개수로 유일하게 좁혀질 때만 선택한다. 지정하지 않으면 모호했다는
사실이 `target_resolution()["selected_by"] == "first_registered_ambiguous"` 로 남는다.
회귀 가드: `temp/test_overload_target.py`.

### 입력 생성에 쓰는 실질 타깃의 형태

| 항목 | 값 |
|---|---|
| 파라미터 개수 | 0: 333 · 1: 785 · 2: 471 · 3: 167 · ≥4: 185 |
| 파라미터 타입(개수 기준) | int 스칼라 1,319 · address 1,277 · bytes/string 333 · 사용자정의 127 · bool 72 · address 배열 49 · **int 배열 33** |
| int 스칼라 파라미터를 가진 타깃 | **957** |
| int 배열만 있고 스칼라는 없는 타깃 | 24 |
| mutability | nonpayable 1,363 · view 502 · pure 21 · payable 55 |

**int 스칼라와 int 배열을 한 분모로 묶지 않는다** — 전자는 `[lo,hi]`, 후자는 `array[...]` 로 문법이
다르고 "폭" 개념이 성립하지 않는다(§9.11).

### 커버리지는 세 층으로 분리해 보고한다
| 층 | 정의 | 분모 |
|---|---|---|
| **static** | AST 에 존재하는 적격 함수·호출 지점 | AST manifest |
| **analysis** | 엔진이 실제로 해석한 함수·호출 지점 | 실행 결과 |
| **navigation** | 디버거 커서·operation 으로 **탐색 가능한** 위치 | 탐색 모듈 |

함수 기준 수치와 호출 지점 기준 수치는 **분모가 다르므로 하나의 백분율로 합치지 않는다.**
analysis 와 navigation 이 갈리는 대표 사례: **루프 본문 안에서만 호출되는 함수**는 해석되지만 커서를
루프 본문에 둘 수 없어 Step Into 가 불가능하다. 그 밖에 생성자 전용 호출, opaque 외부 호출,
주어진 입력에서 ⊥ 가 된 분기가 있다.

또한 **abstractly_reachable(추상 상태가 ⊥ 아님)** 과 **concretely_feasible(구체 실행으로 확인)** 을
구분한다. RQ1 은 전자를 쓰고, RQ3 의 구체 실행 결과와 동일시하지 않는다.

---

## 9. 외부 요약본 정정 목록

1. "AST 기반 함수 수 검증 미수행" → **수행 완료**(§5.2). 함수 수는 AST 확정값으로 갱신.
2. "함수 4,772개" → 정규식 근사. **AST 기준 named·implemented 4,761, 타깃 4,728(T1 2,602 + T2 2,126)**.
3. 병합 판정의 "**precision 91%**" 라는 표현은 쓰지 않는다 — 그것은 **두 휴리스틱의 일치율**이고
   독립적 ground truth 검증이 아니다.
4. fallback 제외 사유를 "파라미터가 없어서" 로 쓰면 틀린다(파라미터를 가질 수 있다) — §3.4 참조.
5. 생성자 제외 사유도 "입력 모델이 대체" 가 아니라 **"추상 pre-state 에서 시작하고 불변식을 복원하지
   않는다(과대근사)"** 로 쓴다.
6. `constructor_nonempty` 류 지표는 "본문 있는 생성자" 가 아니라 **"공백·세미콜론 외의 내용이 있다고
   정규식이 판정한 생성자"** 다. AST 에서는 `implemented` 와 `nontrivial_body` 를 구분한다.
7. v5 이후 **OZ 지문을 주석 포함 원문에서 찾던 문제**를 고쳐 주석 제거본에서 찾도록 했다(선정 수는
   변하지 않았다 — 주석에만 OZ 문구가 있는 파일이 실제로 없었다).

**6차 외부 검토(2026-10-28)에 대한 정정** — 그 검토는 수치 대부분을 정확히 재현했으나, 엔진 소스를
보지 못해 생긴 오판이 둘 있다.

8. **"빈 interval 경계를 백엔드가 정규화하지 않을 수 있다"(C8) → 이미 구현돼 있다.**
   `engine_api._debug_value_text`: 양쪽 빈칸 = **미입력이라 seed 자체를 하지 않고**(⊤로 채우는 것이
   아니다), 한쪽 빈칸 = 슬롯의 `signed`/`bits` 로 타입 min/max 치환, `lo > hi`·파싱 실패는 Diagnostic.
   다만 **생성기는 양쪽 경계를 명시해 보낸다** — 실험 결과가 UI 의 빈칸 처리 정책에 의존하지 않게.
   RQ2 기준선 서술에는 "미입력 = seed 없음" 을 명시한다.
9. **"백엔드가 overload 를 구분하지 못한다"(C9) → 저장 구조는 이미 구분한다.** 문제는
   `_resolve_fcfg` 가 `param_types` 를 넘기지 않던 **배선 누락**이었다(§8 참조). "함수 선택 API 재설계"
   가 필요한 문제가 아니었다.
10. **"@IReturn 이 호출 지점을 구분하지 못한다"(C12) → 설계 의도이고, 비관계적 도메인에서는 쟁점이
    아니다.** `_ap_label` 이 메서드 호출의 인자를 드롭하므로(`token.balanceOf(user)` → 슬롯 키
    `token.balanceOf()`) 인자 유무와 무관하게 값을 줄 수 있고, `ireturn_registry` 키는
    `(핸들, 함수명, index)` 다. 같은 키의 두 호출이 같은 구간을 **공유**하지만, interval 이 비관계적이라
    `a - b = [100,120] - [100,120] = [-20,20]` 로 **독립 가정과 같은 결과**가 나온다(관계적으로 묶는 쪽이
    오히려 `[0,0]` 이라 불건전하다). 실측: 같은 `(수신자, 멤버)` 키에 **인자가 다른** 호출이 모이는
    함수는 4,728 개 중 **60개(1.27%)**이고 대부분 `using for` 라이브러리 연산(`.add`/`.mul`)이라
    @IReturn 대상이 아니다. 인터페이스 호출로 실제로 물리는 사례는 한 줌이지만 의미가 있는 것들이다:
    `Comptroller.liquidateCalculateSeizeTokens` 의 `oracle.getUnderlyingPrice(담보)` vs `(차입)`,
    `ArthPool.mint1t1ARTH` 의 `_COLLATERAL.balanceOf(address(this))` vs `(msg.sender)`.
    → **설계는 유지하고 정밀도 한계로 명시**한다. B단계 레코드에
    `assumption_scope = "shared_per_(handle, function)"` 와 위 반례를 남긴다.
11. **"T1 중 정수 타입 파라미터 1,087 / 없음 1,515" → 1,061 / 1,541.** 차이 26 은 **정수 배열**
    (`uint256[]` 21 · `uint[]` 9 · `uint32[]` 1)을 문자열 부분일치로 포함한 것이다. 스칼라는 `[lo,hi]`,
    배열은 `array[...]` 로 생성 규칙이 다르므로 분모를 합치지 않는다.
12. **modifier 수 "899/307/340" → 856 함수 / 264 함수 / 296 건.** 예전 집계는 생성자의
    base-constructor specifier 44건을 modifier 사용으로 셌다(§5.2).
13. **AST 의 `src` 오프셋은 파일 원본 바이트가 아니다.** solc 에 넘긴 문자열, 즉
    `open(..., encoding="utf-8", errors="replace").read().encode("utf-8")` 기준이다. 원본 바이트로
    슬라이스하면 어긋난다(이 문서의 호출 지점 측정에서 한 번 틀렸다가 바로잡았다). 소스 매핑을 쓰는
    모든 후속 코드에 해당한다.

---

## 10. 논문에서 아직 주장할 수 없는 것

- 569개 파일이 **컴파일**에 성공한다 (parse-only 는 import 해석·타입 검사를 하지 않는다).
- 폐포가 컴파일러로 검증되었다 (휴리스틱으로 복원한 snapshot-isolated closure 다).
- OZ revert 문구 2개 일치가 라이브러리 본문 복제를 **증명**한다.
- 이 데이터셋이 Ethereum 스마트컨트랙트 생태계를 통계적으로 대표한다
  (→ "감사 이력이 있는 공개 DApp 중 사전 정의한 조건을 만족하는 소스 집합" 으로 한정해 쓴다).
- 모든 함수에서 모든 디버깅 operation 이 적용 가능하다.
- 추상적으로 도달 가능한 경로와 구체적으로 실행 가능한 경로가 같다.

---

## 11. 남은 작업

| 우선 | 작업 | 상태 |
|---|---|---|
| **P0** | **입력 생성기** — 슬롯 → `debug_values` 자동 생성. P0=type-wide(주), P1=좁은 구간, P2=경계값(민감도). 채울 수 없는 슬롯(구체 인덱스 필요·`@IReturn`·미지원 타입) 집계 | 미구현 (`suggest_value` 등은 스텁) |
| **P0** | **탐색 로직 순수 모듈 추출** — `useNavigation.ts` 의 설정 전이·Continue·프레임 스택을 React 밖으로 | 미구현 |
| **P0** | **커버리지 원장** — 함수당 static/analysis/navigation + 미도달 사유 | 미구현 |
| **P0** | **operation oracle** — 참조 구현과 차분 검사(Step Forward/Backward 집합 일치, Into/Out 불변식, Continue 정지 지점, 루프 접기, NG 비순환·분기 위치·IN 상태 표시) | 미구현 |
| **P0** | **B단계 지원 범위 태깅** — shadowing·try/catch·do-while·Yul 제어흐름·storage alias 등 사전 선언 | 미구현 |
| P1 | **병합 판정 독립 검증** — AST 로 제외 119개·유지 18개의 본문 동일성 비교 → `merge_validation.json` | AST 준비됨, 미수행 |
| P1 | micro/macro·중복 재가중·경계 신뢰도·provenance 민감도 산출 | 설계 완료, 미측정 |
| P2 | provenance 근거가 있는 스냅샷에 한해 외부 패키지 복원(옵션 3) | 확장 실험 |

---

## 12. 산출물 (재현성)

| 파일 | 내용 |
|---|---|
| `select_targets.py` | v5 선정 로직 정본 |
| `targets_v5.json` | 선정 결과 — funnel, 745/569/551 목록, 모든 파일의 최초 탈락 단계, `per_file_evidence`(태그·병합 근거·특수 함수), 파일별 pragma·폐포 크기·해시 2종·태그·**import 해석 근거(method/confidence)**, 스냅샷·저장소 루트 추정, 미해결 import 분류와 영향 후보 수 |
| `ast_manifest.py` / `ast_manifest.json` | AST 함수 manifest — 함수별 `definition_id`·계약·종류·가시성·`implemented`/`nontrivial_body`·**`modifier_records`(역할·해석 상태·placeholder 수·앞뒤 문장 수)**·**파라미터 이름/중첩 보존 타입/data location**·반환 파라미터·`requires_storage_binding`·루프/분기/`unchecked`/assembly/try-catch·호출 지점(구문 형태·빌트인 여부·`resolution_basis`) |
| `audit_v2.py` / `audit_v2.json` | 2차 검토 지적의 전수 발생 규모 측정. **독립 실행 가능**(v2 규칙 내장), 682/782 재현 확인 |
| `audit_logic.py`, `audit_strict.py`, `audit_strict.json` | v1 결함 규모와 느슨/엄격 해석 비교 |
| `historical/select_targets_v{1,3,4,5}.py` | 과거 판 보존 |
| `scan_pragma.py`, `scan_projects.py`, `scan_features.py` | 초기 조사 도구(버전 분포·프로젝트 분류·구성요소 빈도) |
| `../solc/fetch_solc.py` + `solc-0.8.*.exe` 13개 | 공식 목록 기반 다운로드 + **sha256 검증**, parse-only 용 |
| `target_analysis.py` / `target_analysis.json` | **타깃 정책 집계** — 실질 타깃 규칙·제외 사유 3분류·overload 그룹·중복 제거 후 수·입력 생성용 파라미터 형태·modifier 해석 상태. manifest(사실)와 정책(판단)의 분리 지점 |
| `reviews/` | 외부 검토 이력 — `README.md`(라운드별 지적과 반영), `dataset_selection/REVIEW_PROMPT_round5.md`, `target_and_input/REVIEW_PROMPT_round6.md` |
| `../../temp/test_overload_target.py` | overload 타깃 지정 회귀 가드(6차 C9) |
| `DATASET_RECORD.md` | 이 문서 |
