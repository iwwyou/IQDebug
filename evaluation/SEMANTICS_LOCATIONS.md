# 데이터 위치와 문장의 의미론 (구현 기준)

구현 세션 문서. 엔진이 storage · memory · calldata 와 각 문장을 **어떻게 해석하는가**를 한곳에 적는다.
논문의 탐색(operation) 의미론은 `paper/SEMANTICS_AUDIT.md`, 결정 경위는 `ROADMAP.md` "구현 계획 — 타입·storage 참조·값 슬롯".

상태 표시: **✅ 구현됨** · **🔧 예정**(단계 번호) · **⛔ 미지원**(명시적 오류) · **❌ 지금 틀림**(고칠 대상).
`[forge]` = 같은 코드를 forge 1.9.0 으로 실행해 엔진 결과가 실제 값을 포함하는지 확인한 것(저장소 밖, 일회성).

초안 2026-11-26. 아래 "현재 동작" 은 같은 날 작은 예제로 실제 실행해 본 결과다.

---

## 1. 추상 상태

한 프로그램 지점의 상태 = **환경** `이름 → 칸`. 노드마다 환경 하나(path-insensitive), 분기가 합쳐지면 join.

| 칸 | 무엇 | 구현 |
|---|---|---|
| 값 칸 | uint·int·bool·address·bytesN·enum·string — 추상값(구간·집합) | `Variables` |
| 복합 칸 | struct·배열·mapping 의 **내용** — 멤버·원소·키별 칸, 구체화하지 않은 칸의 요약(summary), 선택자 칸(`m[x]`) | `StructVariable`·`ArrayVariable`·`MappingVariable`, `Interpreter/Semantics/Storage.py` |
| storage 위치 | storage 참조 지역변수가 가리키는 곳 = **뿌리 변수 + 경로**, 경로의 index 는 묶을 때 고정 | `StorageRef` (2.2) |
| memory 객체 | memory struct·배열 — 지금은 변수마다 자기 내용(사본) | 🔧 4단계: 변수는 화살표, 내용은 객체 저장소 |

- 상태변수 = 그 이름의 복합/값 칸. **함수 진입 값은 알 수 없는 호출 직전 상태**(⊤)이고, 디버깅 입력이 좁힌다.
  초기화식(`uint256[2] a = [1, 2]`)은 배포 직후 값일 뿐이라 진입 값으로 쓰지 않는다. constant·immutable 은 예외(값이 고정).
- 이름은 선언 단위다. 블록이 같은 이름을 가리면 바깥 칸을 보관했다가 블록 끝에서 되돌린다(`Engine.SHADOW_SEP`).
- 파라미터·named return 이 같은 이름의 상태변수를 가리는 것은 ⛔(`UnsupportedShadowing`). 결정 9(어느 선언인지로 구분)는 🔧.

### 1.1 칸 이름과 선택자

`m[k]` 는 키 k 를 해석해 칸을 고른다(`Storage.resolve`).

- 키가 한 값 → **구체 칸** `m[3]`
- 키가 범위이고 식이 순수 → **선택자 칸** `m[x]` (x 를 다시 대입하면 버린다 — 그때부터는 다른 칸일 수 있다)
- 부수효과가 있는 식 → 저장하지 않는 가상 칸(겹칠 수 있는 칸들의 join)

불변식: 구체화하지 않은 모든 칸의 실제 값 ∈ summary. 그래서 칸을 버리거나 summary 에서 새로 만들어도 sound 하다.

---

## 2. 선언

| 선언 | 결과 | 상태 |
|---|---|---|
| `uint256 x;` (지역) | 0 | ✅ |
| `uint256 x = e;` | e 의 값(선언 타입으로 변환) | ✅ |
| `D memory e;` · `uint256[] memory a;` | 0 으로 채운 struct · 빈 배열 | ✅ |
| `D memory e = …` | 2.1 대입 표의 memory ← … 규칙 | ✅ / 🔧(memory ← memory) |
| `D storage d = 경로;` | **위치에 묶기** (2.2) | ✅ 2026-11-26 |
| `D storage d;` (나중에 대입) | 묶이지 않은 참조. 묶기 전에 쓰면 오류(solc 도 거부) | ✅ |
| `D calldata c` (파라미터) | 읽기 전용 값. 진입 값 ⊤, 디버깅 입력으로 좁힘 | ✅ |
| 루프 안 선언 | 실행할 때마다 새로 묶는다(이전 반복 값이 남지 않는다) | ✅ |

### 2.1 대입 `x = y` — 위치 조합

| x \ y | storage | memory | calldata |
|---|---|---|---|
| **storage 상태변수(·그 멤버·원소)** | 내용 복사 ✅ | 내용 복사 ✅ | 내용 복사 ✅ |
| **storage 참조 지역변수** | **다시 묶기** ✅ | solc 오류 | solc 오류 |
| **memory** | 새 객체(복사) ✅ | **같은 객체를 가리킴** ❌→🔧4 | 새 객체(복사) ✅ |

현재 동작(2026-11-26 실행):

```solidity
g = g2; g2.v = 3;            r = g.v;   // [5,5]  ✅ (복사 — g2 를 바꿔도 g 는 그대로)
D memory e = D(7, 8); g = e; e.v = 9;    r = g.v;   // [7,7]  ✅
D memory e = g; g.v = 4;      r = e.v;   // [5,5]  ✅ (storage → memory 는 사본)
D memory e = D(1, 2); D memory f = e; f.v = 9;  r = e.v;   // [1,1]  ❌ 실제 9 — memory 별칭(4단계)
```

### 2.2 storage 참조 = 위치 ✅ 2026-11-26

```solidity
D storage d = s[x].inner;
```

1. 묶기(`Storage.bind_ref`): 경로의 index 를 **지금 값**으로 숨은 변수에 담는다. `d → s[d␞k0].inner`, `d␞k0 = x 의 지금 값`.
2. 사용: `d` 가 나오는 식은 해석할 때 경로로 펼친다(`Storage.expand_refs`). `d.v = 1` 은 `s[d␞k0].inner.v = 1` 이다.
   그래서 쓰기가 원래 상태변수에 반영되고, alias 전파·선택자 무효화도 원래 경로 기준이다(`m[d.v]` 와 `m[s[..].inner.v]` 는 같은 칸).
3. 고정: 뒤에 x 가 바뀌어도 d 는 같은 칸(숨은 변수는 다시 묶을 때만 바뀐다).
4. 다시 묶기 `d = 경로2`: 같은 방식으로 새로 묶는다. 숨은 변수를 읽던 선택자 칸은 버린다.
5. 참조의 참조 `e = d`: d 를 펼친 경로를 e 가 **자기 숨은 변수로** 다시 고정한다(d 를 나중에 다시 묶어도 e 는 그대로).
6. 표시: 숨은 변수(이름에 `\x1e`)는 상태 출력에서 뺀다. 참조 자신의 값은 `→ 경로`.

`[forge]` 13 예제 — 쓰기 반영, index 고정, 다시 묶기, 참조의 참조, 배열 원소, mapping 참조, 루프 안 선언, 분기 안 쓰기,
memory 사본, 선택자 무효화. 전부 실제 값 포함(`ROADMAP.md` 표본 참고).

**정밀도 손실(sound).** x 가 범위일 때 `D storage d = s[x]; d.v = 1; r = s[x].v;` 는 실제 1 인데 `[1,2]`(옛 값 2 와 join).
숨은 변수와 x 가 같은 값이라는 관계를 엔진이 모른다. 🔧 x 를 다시 대입하기 전까지 "같은 칸" 관계를 유지(선택자 칸과 같은 무효화 규칙).

### 2.3 경로마다 다른 위치 — join 🔧2

```solidity
D storage d = a;  if (c) { d = b; }  d.v = 1;
```

- 합류하면 참조의 값 = **후보 모음** `{a, b}`. 지금은 명시적 오류.
- 같은 배열·mapping 의 후보(`pools[1]`·`pools[2]`)는 이미 된다 — 숨은 index 변수가 정수처럼 `[1,2]` 로 join 된다.
- 쓰기: 후보가 하나면 그 칸을 바꾼다(strong). 여럿이면 후보마다 "옛 값 ∪ 새 값"(weak) — 기존 `s[x] = 99`(x 범위)와 같은 규칙.
- 읽기: 후보 값들의 join. refine(조건으로 좁히기)은 후보가 하나일 때만.
- 루프: index 는 정수 widening 으로 멈춘다(연결 리스트 순회 → `nodes[1..MAX]`). 다른 변수 후보는 소스의 경로 수만큼이라 유한하다.

`[forge]` `c=true → a.v 10, b.v 1` · `c=false → a.v 1, b.v 20` — 엔진(🔧) `a.v [1,10]`, `b.v [1,20]` 이 둘 다 포함.
연결 리스트 1→2→3 순회 후 쓰기 — 실제 `nodes[3].val = 7`, 엔진(🔧) 각 노드 "옛 값 ∪ 7".

---

## 3. 함수 호출

### 3.1 인자 전달

| 파라미터 \ 인자 | storage 경로·참조 | memory | calldata | 값 |
|---|---|---|---|---|
| **storage** (internal·library) | **위치를 넘김** ✅ 2026-11-26 | solc 오류 | solc 오류 | — |
| **memory** | 사본 ✅ | **같은 객체** ✅(되쓰기로 근사)→🔧4 | 사본 🔧 확인 | — |
| **calldata** | — | — | 같은 값 | — |
| 값 타입 | — | — | — | 사본(선언 타입 변환) ✅ |

규칙 (2026-11-26, `Engine.invoke_callee`):
- **storage 파라미터 = 위치.** 인자 식의 뿌리가 상태변수이면, callee 진입 때 파라미터를 그 경로에 묶는다(`Storage.freeze_ref`,
  index 는 호출 시점 값). callee 안의 `p.v = 1` 은 상태변수 칸에 바로 쓰고, 복귀 때 상태변수 반영으로 호출자에 돌아간다. 되쓰기 없음.
  같은 위치를 두 파라미터로 넘겨도(`h2(g, g)`) 둘 다 같은 칸이다.
- **상태변수를 memory·calldata 파라미터로 넘기면 사본**이고 되쓰지 않는다.
- 되쓰기(복귀 때 파라미터의 최종 내용을 원래 객체에)는 memory → memory 와 **타깃 자신의 storage 파라미터(별도 칸)** 를 넘길 때만 —
  4단계(memory 객체) 전까지의 근사.
- 인자가 호출식(`h(gp())`)이면 아직 사본(되쓰기 없음) — 🔧.

이전 동작: 복합 인자를 모두 사본으로 넘기고 복귀 때 원래 객체에 되썼는데, 그 뒤 callee 출구의 상태변수 사본이 호출자의 상태변수를 다시
덮어 storage 쪽 되쓰기가 사라졌다(`h(g)` 뒤 `g.v` 가 옛 값). memory 파라미터에 상태변수를 넘긴 경우는 이 덮어쓰기 덕분에 우연히 맞았다.

`[forge]` 13 경우 전부 정확히 일치 — 상태변수·mapping 칸·참조를 넘김, memory 사본·memory 객체, 같은 위치 두 번, callee 가 넘긴 칸을 다른
경로로 읽음, 중첩 호출, 호출 뒤 index 변수 변경, using-for library, mapping·배열 파라미터, 그리고 RewardsManager 형태
(`deposit` → `_deposit(pid, pool)` 안의 `updatePool(pid)` 가 같은 칸을 바꾼 뒤 `pool.acc` = 150).
- `this.f()` 는 외부 호출 — ABI 복사, storage 파라미터 없음(solc). 호출 컨텍스트 `msg.sender = this` ✅.

### 3.2 타깃 함수 자신의 storage 파라미터 🔧3

호출자가 없으므로 파라미터가 가리킬 곳을 **디버깅 입력**으로 받는다. 기본 = 별도 칸(어떤 상태변수와도 겹치지 않음).
후보 = 같은 타입의 상태변수 칸, 앞선 같은 타입의 파라미터. 지금은 기본(별도 칸)만.

### 3.3 반환

| 반환 타입 | 의미 | 상태 |
|---|---|---|
| 값 | 사본 | ✅ |
| `memory` | 사본(호출자가 새 객체로 받음) | ✅ (4단계에서 객체 화살표) |
| `storage` | **위치** — `D storage d = gp();` 는 callee 의 `return 경로` 에 묶는다. named 반환(`returns (D storage d)`)은 묶이지 않은 참조로 시작. 값으로 쓰면(`gp(x).v`) 그 칸 | ✅ 2026-11-26 |

반환 경로가 여럿이면 같은 경로(`s[k]`·`s[k+1]` → 둘 다 `s[숨은 index]`)는 index 를 join 한다. 다른 변수면 🔧 후보 모음.
`[forge]` 6 경우(반환 뒤 index 변수 변경·named 반환·상태변수 반환·호출식 읽기·두 경로 반환·반환받은 참조를 다시 넘김) 전부 포함, 두 경로 반환 외에는 정확히 일치.

---

## 4. 문장별 동작

| 문장 | 동작 | 상태 |
|---|---|---|
| `x = e` | 고른 칸에 strong update. 키가 범위인 칸이면 겹칠 수 있는 다른 칸·summary 에 weak update(`Storage.propagate`). 뿌리 변수를 읽던 선택자 칸은 버린다 | ✅ |
| `x op= e` | `x = x op e` 와 같다 | ✅ (참조 경유 포함, `[forge]`) |
| `x++` · `x--` | `x += 1` · `x -= 1` | ✅ 단, **멤버 대상(`g.v++`)은 예외** ❌ — 이번 변경 전부터, 미조사 |
| `delete x` | 값 = 0, struct·정적 배열 = 재귀 0, 동적 배열 = 길이 0, mapping = 영향 없음 | ✅ (참조 경유 포함) |
| `arr.push(v)` · `arr.pop()` | 길이 ±1, 원소 쓰기 | ✅ (참조 경유 포함) / 빈 배열 접근 revert ❌→🔧3 |
| pop 뒤 그 원소를 가리키던 참조 사용 | 실제로는 배열 밖 storage 칸에 읽고 쓴다(`[forge]` 쓴 값이 다음 `push()` 원소로 나타남) | ⛔ 🔧 명시적 오류로 |
| `require(c)` · `if (c)` | 조건으로 상태를 좁힌다(refine). 참조 경유도 원래 칸을 좁힌다(`[forge]` 아님, 예제 `[4,10]`) | ✅ |
| `return e` | 반환값 모음(3.3) | ✅ |
| 내부 호출 | 3절 | ✅ (memory 별칭은 4단계) |
| 외부 호출(주소) | 반환 ⊤, 효과 미모델링(CG3). view 는 효과 없음 | ✅ |
| `emit` | 인자 식만 평가 | ✅ |
| assembly | `sstore` = 변경 가능한 상태변수 전부 ⊤, `sload` = ⊤. Yul `if`·`for`·`switch` 는 ⛔ | ✅ / ⛔ |
| assembly `p.slot := n` | 참조가 storage 칸 번호를 직접 가리킨다(`[forge]` n=1 이면 둘째 상태변수, keccak 이면 어떤 변수도 아닌 칸) | ⛔ 🔧 — 고정 위치 저장소 패턴(`keccak256("…")`) 수를 센 뒤 결정 |
| 디버깅 입력 | 진입 제약 — 슬롯이 고르는 칸에 값을 놓는다(쓰기 아님). 겹치는 입력이 함께 성립하지 않으면 "디버그 입력 모순" | ✅ |

---

## 5. 합류

- **join:** 값 칸 = 추상값 join, 복합 칸 = 칸별 join(한쪽에만 있는 칸은 그쪽 summary 에서 만든다), 참조 = 같은 경로면 그대로(숨은 index 는 따로 join),
  다른 경로면 🔧 후보 모음.
- **widening:** 루프 머리에서 정수 구간. 그래서 루프 안 index·누적값은 넓어진다(합 루프가 ⊤ — `KNOWN_ISSUES.md` I20-2).
- **path-insensitive:** 경로를 나눠 기억하지 않는다. "d 가 b 이면 a.v 는 10" 같은 관계는 잃는다(sound, 덜 정밀).

---

## 6. 미지원으로 둔 것

명시적 오류를 내고 조용히 해석하지 않는다.

- 경로마다 다른 위치의 storage 참조(🔧2 에서 구현)
- pop 뒤 떠 있는 참조 사용, assembly `.slot`
- Yul 제어 흐름(`if`·`for`·`switch`)·여러 줄 Yul 식
- 파라미터·named return 이 상태변수를 가리는 이름
- keccak256 계산(값은 ⊤)

---

## 7. 구현 위치

| 규칙 | 코드 |
|---|---|
| 선언·다시 묶기 | `Interpreter/Engine.py` `_interpret_var_decl`·`_interpret_assignment` |
| 묶기·펼치기 | `Interpreter/Semantics/Storage.py` `bind_ref`·`expand_refs` |
| 키 해석·칸 선택·전파·무효화 | `Interpreter/Semantics/Storage.py` `resolve`·`select`·`propagate`·`on_assign` |
| 대입 | `Interpreter/Semantics/Update.py` `update_left_var`(LHS 를 펼친 뒤) |
| 읽기 | `Interpreter/Semantics/Evaluation.py` `evaluate_identifier_context`(참조 = 그 위치) |
| 호출·복귀 | `Interpreter/Engine.py` `invoke_callee`·`_interpret_callee_body` |
| join·widening | `Utils/Helper.py` `VariableEnv._merge_values` |
| 데이터 위치 기록 | `Analyzer/EnhancedSolidityVisitor.py` `_declared_type`·`_param_from_group`, `SolType.dataLocation` |
