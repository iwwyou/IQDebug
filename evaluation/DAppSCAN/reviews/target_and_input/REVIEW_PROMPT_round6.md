# 6차 검토 요청: 타깃 함수 계층(T1/T2) 구현과 입력 생성 정책

> 데이터셋 선정(1~5차 검토)은 마무리됐다. 이번에는 **① AST 로 확정한 타깃 계층 분류의 구현 로직**과
> **② 디버깅 입력 자동 생성 정책**을 검토해 주기를 요청한다. 기준: 2026-10-28,
> `select_targets.py`(v5) → `targets_v5.json`, `ast_manifest.py` → `ast_manifest.json`.

## 1. 배경 (요약)

**IQDebug** 는 구체 트랜잭션 trace 를 재생하지 않고, abstract interpretation(구간 도메인)으로 미리
계산한 추상 프로그램 상태 위에서 디버거 operation(Step Forward/Backward, Step Into/Out, Breakpoint,
Continue)을 수행하는 Solidity 소스 수준 디버거다. 논문(ISSTA 2027)의 기여는 **그 operation 들의 의미론**이다.

- 타깃 함수를 고르면 엔진이 **디버깅 입력 슬롯**을 자동 도출한다 — `@Param`(파라미터),
  `@StateVar`(상태변수, 중첩 멤버·인덱스 경로 포함), `@GlobalVar`(`msg.*`/`block.*`),
  `@IReturn`(본문 없는 인터페이스 호출의 반환값). `constant` 는 값이 고정이라 제외된다.
  사용자는 **값(주로 구간)만** 채운다.
- 엔진은 CFG 위 worklist fixpoint 로 노드마다 추상 환경을 채운다. 내부 호출(internal/상속/library/
  super)은 본문을 해석하고, 인터페이스 외부 호출은 불투명(반환 ⊤)이다.
- 이후 탐색 operation 은 **엔진을 다시 호출하지 않고** 계산된 상태 위에서 커서를 움직인다.

**세 단계 분리 원칙**(이전 검토들에서 합의): A 데이터셋 선정(소스 적격성) / B 엔진 지원 범위 사전 태깅 /
C 실행 결과 funnel. **C 의 실패를 A·B 로 되먹이지 않는다.**

## 2. 확정된 데이터셋과 AST manifest

- 주 집합(옵션 2′): **569 파일 / 102 스냅샷 / 95 감사 프로젝트**(DAppSCAN, Solidity ≥0.8,
  스냅샷 경계 안에서 의존 폐포가 해석되고 폐포 전체에 공통 solc 버전이 존재하는 소스).
- `solc --standard-json` + `stopAfter: "parsing"` 으로 745개 파싱: **성공 744 / 실패 1**.
  파일별로 **허용 버전의 최대값**에 해당하는 바이너리를 골라 파싱했다(solc 는 parse 단계에서도 버전
  pragma 를 강제한다 — 0.8.38 하나로는 558/745만 커버되어 13개 버전을 받았다).
  parse-only 이므로 **import 해석·타입 검사는 하지 않는다.**

옵션 2′ 의 AST 집계:

| 계층 | 수 | 설명 |
|---|---|---|
| **T1** | **2,602** | 일반 `contract` 의 구현·본문 있는 `public`/`external` 함수. 전부 `contract` 종류이며 그중 **abstract 계약 소속 278** |
| **T2** | **2,126** | internal/private 1,077 + **library 1,049** |
| `special` | 284 | constructor 248 · receive 24 · fallback 12 |
| `empty_body` | 33 | 본문이 `{}` |
| `declaration_only` | 237 | 인터페이스 선언 등 |

교차검증: AST 의 named·implemented 함수(특수 제외) 4,761 vs 이전 정규식 근사 4,772 → 차이 0.2%.

T1 2,602 의 내부 구성:

| 구분 | 수 |
|---|---|
| 빌트인 외 호출이 있음(Step Into 가 발생 가능) | 1,891 |
| 호출 없음(leaf) | 711 |
| 그중 **단순 getter 류**(view/pure + 호출·분기·루프 전부 없음) | **462** |
| **실질 타깃**(호출 ∨ 분기 ∨ 루프) | **1,941** |
| 중복 그룹 대표만 남길 때 | **1,900** |
| state mutability | nonpayable 1,562 · view 931 · pure 54 · payable 55 |
| 파라미터 수 | 0개 635 · 1개 1,050 · 2개 551 · 3개 179 · 4개 이상 187 |

그 밖에: 호출 지점 14,654개(빌트인 제외 11,995) — identifier 7,864 · member 3,843 · type cast 2,668 ·
`super` 78 · `new` 140, 전부 `resolution_basis="syntax_only"`(internal 이라 단정하지 않음).
타깃 중 루프 293 · 분기 999 · `unchecked` 187 · inline assembly 162 · try/catch 9.
**storage 참조를 파라미터로 받는 T2 348개**(독립 호출에 호출 컨텍스트 필요 → `requires_storage_binding`).
modifier 보유 899, **placeholder(`_`)가 2개 이상인 modifier 0건**.

## 3. 검토 대상 ① — 타깃 계층 분류의 **구현**

5차 검토에서 정책(가시성 기준 T1, 실행 전 사전 확정)은 승인받았고, 아래 구현은 그 이후 작성해
**아직 검토받지 않았다**. `ast_manifest.py` 의 전부다:

```python
def tier_of(fn, owner_kind, owner_abstract, is_free):
    kind = fn.get("kind", "function")        # function | constructor | receive | fallback
    body = fn.get("body")
    implemented = bool(body)
    if kind in ("constructor", "receive", "fallback"):
        return "special", kind               # 타깃 아님, 기록만
    if not implemented:
        return "declaration_only", kind
    if not body.get("statements"):
        return "empty_body", kind            # 본문이 `{}`
    if is_free:
        return "T2", "free_function"
    if owner_kind == "library":
        return "T2", "library_function"
    if owner_kind == "interface":
        return "declaration_only", kind
    vis = fn.get("visibility")
    if vis in ("public", "external"):
        return "T1", "abi_visible_contract_function"
    return "T2", "internal_contract_function"
```

보조 정보로 함수마다 다음을 기록한다: `definition_id`(스냅샷\|파일\|계약\|src 바이트 범위),
`contract_kind`, `is_abstract_contract`, `is_free_function`, `is_virtual`, `has_override`,
`visibility`, `state_mutability`, `implemented`/`nontrivial_body`, 파라미터 타입·**data location**,
`requires_storage_binding`, modifier 목록과 placeholder 수, 루프·분기·`unchecked`·assembly·try/catch,
호출 지점(구문 형태·빌트인 여부·`resolution_basis="syntax_only"`).

**역할 분담(이번에 확정하려는 것)**
- **실행은 T1 만 한다.** T2(internal/library/free)는 **callee** 이므로 직접 디버깅 타깃으로 돌리지 않는다.
- **T2 는 커버리지의 분모**다 — T1 실행에서 ① callee 로 해석됐는지 ② Step Into 로 도달 가능한지
  ③ 어느 것도 아니면 사유(루프 본문 안 호출 / 생성자 전용 / opaque 외부 호출 / ⊥ 분기 / 미지원)를 기록한다.
  조건을 만족하는 T2 는 **전부** 분모에 넣는다(실행 결과·입력 정책과 무관하게 고정).
- `special`·`empty_body`·`declaration_only` 는 타깃·분모 모두에서 분리해 별도 집계한다.

### 이 부분에서 묻고 싶은 것
1. **library 함수 1,049개를 T2(= callee 분모)로 둔 판단**이 맞는가? `public` library 함수는 ABI 로도
   노출되고 `using for` 로 붙어 호출되기도 한다. T1 로 올려야 하는 하위 분류가 있는가?
2. **abstract 계약의 `public`/`external` 함수 278개를 T1 에 넣은 판단**이 맞는가? 그 자체로는 배포되지
   않고 파생 계약의 진입점으로만 실행된다. 별도 계층(T1-abstract)으로 분리해 보고해야 하는가?
3. `empty_body` 33개를 타깃에서 뺀 것이 타당한가(디버깅할 내용이 없다는 근거)? 분모에서도 빼는 게 맞는가?
4. `is_free`(파일 수준 free function)를 T2 로 둔 것, `interface` 안의 구현 함수를
   `declaration_only` 로 둔 것(인터페이스는 본문을 가질 수 없으나 방어적으로 처리)에 문제가 있는가?
5. **상속으로 들어온 함수**는 현재 **선언된 계약에서 한 번만** 집계한다(파생 계약에서 중복 계산하지
   않음). 파생 계약의 진입점으로서의 실행 컨텍스트는 `definition_id` 와 분리된 실행 컨텍스트 ID 로
   다루려 한다. 이 처리로 T1 분모가 왜곡되지 않는가?
6. **실행 모집단 축소**: 실질 타깃 1,941(중복 제거 1,900)만 돌리고 단순 getter 462개는 별도 집계하려
   한다. 이 축소가 "쉬운 것만 골랐다" 가 아니라 "정보가 없는 함수를 분리했다" 로 방어되는가?
   파일럿을 스냅샷별 상한 N 의 **사전 규칙 층화 표본**으로 시작하는 것은 타당한가?

## 4. 검토 대상 ② — 입력 생성 정책

엔진의 `evaluate(debug_values)` 는 슬롯마다 값이 있어야 동작하고, 현재 자동 생성 API 는 **스텁**이다.
타깃이 1,900개 규모이므로 **규칙 기반 생성기**가 필요하다. 그리고 입력이 **커버리지를 결정**한다 —
`if (x > 100) helper(x)` 에서 `x=[0,10]` 이면 `helper` 는 해석되지 않고 `[0,⊤]` 이면 해석된다.

### 제안하는 설계
**두 축을 분리한다.**

- **폭(width)**: 수치 슬롯에 주는 구간의 크기. 후보 두 가지
  - (A) **희소 격자** `{0, 2, 5, 10, 20}` — 5점.
  - (B) **균등 격자** `{0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20}` — 11점.
  - `0` 은 singleton(단일 값) 기준선이다.
- **기준값(base `c`)**: `uint` 는 `[c, c+w]`, `int` 는 `[c−w/2, c+w/2]`.
  기본 규칙은 `c = 0`. 민감도로 **"그 파라미터와 비교되는 소스 상수 중 최솟값"**(`require(amount >= 100)`
  이면 `c = 100`)을 쓰는 변형을 둔다 — 규칙이 기계적이고 모든 함수에 동일 적용된다.
- **비수치 타입**: `bool` → ⊤(두 값), `address` → 심볼릭 주소 1개(민감도 2개), `bytes`/`string` → ⊤,
  배열·매핑·구조체 경로 → 엔진이 도출한 경로 슬롯에 같은 규칙 적용, 구체 인덱스가 필요한 슬롯은
  인덱스 0(민감도로 1개 더).
- **`@IReturn`**(인터페이스 반환값) 과 **`@GlobalVar`**(`msg.value`, `block.timestamp` 등)도 같은 폭 규칙.
- **⊤(type-wide, 예: `uint256` 전 범위)** 는 **주 정책이 아니라 참조점 1개**로만 포함해 "넓히면 어떻게
  무너지는가" 를 보여준다.
- 채울 수 없는 슬롯(미지원 타입, 구체 인덱스가 필요한데 규칙으로 못 정하는 경우)은 **집계해 보고**한다.

**왜 type-wide 를 주 정책으로 쓰지 않는가**(이전 검토의 권고와 다른 선택):
이 도구의 장점은 **여러 입력의 수행 상황을 한 번에 보는 것**인데, 폭이 타입 전체면 거의 모든 분기가
feasible 이 되고 구간이 ⊤ 로 붕괴해 **보여줄 정보가 사라진다**. 또한 학위논문의 `S1(singleton) → 확장`
설계와 이어진다. 대신 폭 스케줄을 **사전에 고정**하고 전 타깃에 동일 적용하므로 "유리한 입력을 골랐다"
는 문제는 발생하지 않는다고 본다.

**현재 계획**: 전체 타깃에는 (A) 희소 격자, 층화 표본에는 (B) 균등 격자로 추세 해상도를 확보.
비용 추정: 1,900 타깃 × 11점 = 20,900회 `evaluate`(균등 격자를 전체에 적용할 경우).

### 이 부분에서 묻고 싶은 것
1. **(A) vs (B)** — 어느 쪽을 주 결과로 두어야 하는가? 둘을 병행하는 위 계획이 타당한가,
   아니면 하나로 통일해야 하는가? 폭의 상한을 20 으로 둔 것(더 넓힐 필요가 없다는 판단)은 적절한가?
2. **동시 확장 여부**. 수치 슬롯이 여러 개일 때 ① 전부 같은 폭으로 동시에 넓히는가, ② 한 번에 하나만
   넓히는가(좌표별), ③ 둘 다 보고하는가? T1 의 파라미터 수 분포가 0개 635 · 1개 1,050 · 2개 551 ·
   3개 179 · 4개 이상 187 이라 동시 확장이면 2개짜리에서 이미 상태공간이 (w+1)² 이 된다.
   상태변수·`@GlobalVar` 슬롯까지 합치면 더 커진다.
3. **기준값 규칙**. `c = 0` 기본 + "비교 상수의 최솟값" 민감도가 적절한가? `c = 0` 이면
   `require(amount > 0)` 류에서 상당수 분기가 ⊥ 가 되어 커버리지가 과소평가될 수 있다.
   더 방어 가능한 기계적 규칙이 있는가?
4. **비수치 타입 처리**. `address` 를 심볼릭 1개로 두면 `msg.sender == owner` 류 비교가 ⊤/⊥ 중 무엇도
   확정되지 않는다(엔진은 심볼릭 주소 집합 도메인을 쓴다). 주소·bytes·string 입력 정책을 어떻게
   선언해야 실험이 해석 가능한가?
5. **보고 지표**. 폭별로 ① 추상적으로 도달 가능한 노드 수 ② 해석된 내부 호출 수 ③ Step Into 가능한
   호출 지점 수 ④ ⊤ 가 된 변수 수 ⑤ 구간 폭 ⑥ 해석 시간 ⑦ widening 발생 횟수를 비교하려 한다.
   빠진 지표가 있는가? `abstractly_reachable` 과 `concretely_feasible` 을 분리해 보고하는 것으로
   충분한가?
6. **입력 정책이 RQ 에 걸리는 방식**. 폭 스케줄을 RQ2(정밀도·비용)의 독립변수로 쓰고, RQ1(operation
   의미론의 적용 가능성)은 **폭 하나를 고정**해 보고하려 한다(어느 점을 고정해야 하는가?).

## 5. 답변 형식

① 치명적 결함(수정 없이는 결과를 신뢰할 수 없는 것), ② 개선하면 좋은 것, ③ 타당한 부분 으로 나누고,
각 항목에 **구체적 반례나 수정 코드**를 붙여 주기 바란다. 타깃 계층은 현재 구현 유지 / 수정 /
재설계 중 무엇을 권하는지, 입력 폭은 (A)/(B)/병행/다른 안 중 무엇을 권하는지 분명히 밝혀 주기 바란다.

**미구현(의도적)**: 입력 생성기, 커버리지 원장, operation oracle, B단계 지원 범위 태깅.
polyfill 없이 현재 상태를 그대로 검토해 주기 바란다.

## 첨부 파일

- `ast_manifest.py` / `ast_manifest.json` — AST 함수 manifest(§2·§3 의 출처).
- `select_targets.py` / `targets_v5.json` — 데이터셋 선정 정본과 결과.
- `DATASET_RECORD.md` — 1~5차 검토 반영 이력, 실측된 결함 규모, 주장 금지 목록.
- (참고) `reviews/dataset_selection/REVIEW_PROMPT_round5.md` — 직전 라운드 요청서.
