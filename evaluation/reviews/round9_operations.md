# Operation 의미론 — 계층·성질·결정 로그

> 2026-10-29. **구현 세션 산출물**이다. 논문 세션은 이 문서를 읽어 `paper/SEMANTICS_AUDIT.md`
> 로 옮겨 적는다(이 문서를 직접 수정하지 않는다).
>
> 범위: 외부 LLM 검토(라운드 8) → 사용자 검토(라운드 9·10)에서 확정된 operation 집합,
> 그 의미론이 의존하는 성질, 성질을 깨던 구현 결함과 그 수정(K1–K3), 아직 측정되지 않은 항목.
> 모든 수치·CFG 덤프는 `.venv\Scripts\python.exe` 로 실측한 것이다.

---

## 1. 두 계층 (1차 분류: 무엇을 바꾸는가)

예전에 쓰던 **국소 / 재배치 2분류는 철회**했다. `Continue` 가 결과에 따라 스택을 보존·push·pop·리셋
전부 하므로 한 operation 이 두 부류에 걸쳐, 분류가 성립하지 않았다.

| 계층 | operation | 비고 |
|---|---|---|
| **Session** | `Apply`(= evaluate 재실행), `Reconcile`(코드 편집 후 프레임 재검증) | 분석 결과 `A` 가 교체된다 |
| **Navigation — graph traversal** | `StepForward`, `StepBackward`, `SelectSuccessor`, `SelectPredecessor`, `StepInto`, `StepOut`, `JumpLocal` | `NG` 간선을 따른다 |
| **Navigation — graph search** | `Continue`, `NextBreakpoint` | 거리 무제한, 프레임을 쌓거나 pop 한다 |
| **Navigation — direct positioning** | `JumpRoot`, `Restart` | 스택 리셋 |
| **Navigation — ordered positioning** | `NextIntent`, `PrevIntent` | `intentLines()` 의 줄 전순서 위 이동 |
| **Observation / Config** | `ToggleBreakpoint`, `InspectState`, `LoopSummary`, `ExplainUnreachable`, `SelectFrame` | 커서를 옮기지 않는다 |

**2차: 성질 태깅** (부류가 아니라 라벨. 한 operation 에 여러 개가 붙는다)
`stack-local` / `root-normalizing` / `search-based` / `choice-dependent` / `snapshot-preserving` / `engine-free`.

예: `Continue` = `search-based` + `snapshot-preserving` + `engine-free`, 결과가 Exhaust 면 추가로
`root-normalizing`. 2분류로는 담을 수 없던 것이 여기서 담긴다.

### 1.1 제거된 operation

| | 사유 |
|---|---|
| `PreviousBreakpoint` | 역방향 호출 경로가 유일하지 않아 전함수가 아니다 |
| `UnwindToFrame` (= 옛 `popToFrame`) | **`StepOut^k` 와 완전히 동일**하다 — `slice(0,-1)` 반복 = `slice(0,i+1)`, `clearMarks()` 도 같다. primitive 가 아니라 파생 연산이므로 목록에서 빼고 "유도되는 연산" 으로 한 줄 언급한다 |
| `Compare(A_I, A_I')` | intent verdict + `Apply` 가 같은 역할을 한다. 세션에 스냅샷을 둘 보관하는 구조 변경이 필요해 비용도 크다 |

---

## 2. Operation 가용성 `avail(n)`

노드 종류에 따라 쓸 수 있는 operation 이 다르다. 구현은 이미 이렇게 동작하는데 이름이 없었다.

| `NodeKind(n)` | `avail(n)` = base ∪ … | 근거 |
|---|---|---|
| `Stmt` | — | |
| `Cond` | `SelectSuccessor`, `ExplainUnreachable` | 후속이 둘 |
| `LoopHead` | `SelectSuccessor`, `LoopSummary` | 탈출 경로가 여럿 (§4) |
| `Merge` | `SelectPredecessor` | 선행이 여럿인 유일한 지점 |
| `Call` | `StepInto` | `flags ∋ call` 이고 `opaque` 가 아닐 때 |
| `Exit` | `StepOut` | 프레임 깊이 ≥ 2 |

`base = {StepForward, StepBackward, Continue, NextBreakpoint, JumpLocal, ToggleBreakpoint, InspectState}`.

**착지 가능 노드 집합은 이 구분에 쓰지 않는다.** 조건 노드를 비착지로 만들면 `require`/`if` 가드
직전 상태를 볼 자리가 없어지고(intent 디버깅의 작업 자체), `whyUnreachable` 이 지목하는 노드로
갈 수 없고, 그 줄에 breakpoint 도 걸 수 없다.

---

## 3. 성질

### 3.1 `NG` 는 단일 관계다 (S1)

```
NG = (V_land, E_N)       V_land = 착지 노드,  E_N = navEdges(cfg)
Succ(n) = { m | (n,m) ∈ E_N }
Pred(m) = { n | (n,m) ∈ E_N }
```

**이전 결함.** `forwardCands` 와 `backwardCands` 가 서로 다른 규칙을 썼다 —
`fwdEdges` 는 루프 헤더에서 `true` 간선을 잘라 본문에 들어가지 않는데, `backwardCands` 는
`ix.pred` 를 그대로 쓰고 `loop_body` 노드를 **통과해** 본문을 거쳐 나왔다. 그래서
`Pred ≠ Succ⁻¹` 이었다.

반례(실측):

```
L4  while (i < 3) {          while_cond_4  (condition)
L5      if (n > 5) {         if_condition_5 (condition, loop_body)
L6          return i;        Return_6       (loop_body)      → RETURN
L8      i = i + 1;           Assign_8       (loop_body)      → while_join [back_edge]
L9  }                        while_exit_4   (branch_else, loop_exit)
L10 return 99;               Return_10                        → RETURN
                             ※ EXIT 로 들어오는 간선이 하나도 없다

Succ(L4)   = { L9 }
Pred(EXIT) = { L10, L4 }      ← walk(RETURN) 이 Return_6 → if_true_5 → if_condition_5
                                → while_body_4 (전부 loop_body, 비착지) 를 통과해 L4 에 착지
증상: EXIT 에서 Step Backward → L4, 그 자리에서 Step Forward → L9. 제자리가 아니다.
```

빈도: 루프 본문에 `return` 이 있는 함수 10/159(6.3%) = 실질 타깃 1,941 기준 0.5%.
**빈도는 낮지만 논문의 기여가 이 형식 모델 자체**라, 정의가 틀린 문제로 다룬다.

**수정.** `navEdges(cfg)` 를 한 번 만들고 `Succ`/`Pred` 를 둘 다 거기서 읽는다.
대칭은 테스트로 보장되는 성질이 아니라 **구성상 자명**해진다. 회귀 가드는
`temp/test_nav_symmetry.py` (navGraph.ts 이식, 16 케이스).

### 3.2 줄 단사성 (S2)

> 입력 전제(한 줄 한 문장, 블록 헤더 분리, 중괄호 필수, 닫는 `}` 단독) 아래에서
> **각 소스 줄의 착지 노드는 최대 한 개**다.

따라서 `landingAtLine` 은 결정적이고, breakpoint 를 `(파일, 줄)` 로 키잉하는 것이 건전하다.
증명은 네 겹이다.

1. **게이트** — `if (c) { s=1; } else …`(한 줄 블록), `a=1; b=2;`(다중 문장), `if (c) s=1;`(중괄호 없음),
   `} }`(닫는 `}` 뒤 문장)는 전부 `SourceError` 로 거부된다
2. **분기 더미 비착지** — `} else if (n>5) {` 줄에는 `else_if_condition`·`else_if_true`(·`else_if_false`)
   세 노드가 있지만 뒤의 둘은 `branch_then`/`branch_else` 라 착지하지 않는다
3. **join 접기** — else-if 체인의 안쪽 join 은 `isCollapsedJoin` 으로 접히고, 바깥 join 하나만
   체인을 닫는 `}` 줄(`DisplayLines.chain_end_line`)에 남는다
4. **루프 보조 노드 비착지** — `loop_body` / `loop_init` / `loop_incr` (K1)

실측 예외는 두 개였고, 하나는 수정했다.

| 예외 | 상태 |
|---|---|
| `for` 헤더 — `for_init` + `for_cond` 가 같은 줄 | **K1 로 해소.** `for_init` 을 비착지로. 효과는 `for_cond` 의 IN 에 이미 포함(`j=[0,0]` 실측)되므로 정보 손실 없음. `for` 포함 함수 203/4,314(4.71%) |
| `try/catch` — `} catch {` 줄에 `try_join`·`catch_entry`·`catch_end` **3개** | **C 재설계로 해소** (§5). catch 를 if/else-if 와 동형인 조건 체인으로 바꿔 `} catch … {` 줄에 `catch_cond` 하나만 남고, 체인 닫는 `}` 줄에 join 이 간다. try/catch 포함 함수 **7/4,314(0.16%)** |

**현재 S2 예외는 없다.** `temp/test_nav_symmetry.py` 25 케이스(try/catch 9개 포함)가 S1/S2/S3/S4 전부 통과한다.

### 3.3 land-closure frontier 의 분기 금지 (S3)

`landFwd` 는 비착지 노드를 통과하다가 **후속이 둘 이상이면 예외를 던진다.**

> `landFwd` 가 **통과하는** 비착지 노드 — 착지 노드에서 `fwdEdges` 를 따라 다음 착지 노드를
> 만나기 전까지 지나는 노드 — 는 out-degree ≤ 1 이다.

"모든 비착지 노드의 out-degree ≤ 1" 은 **거짓**이다 — 루프 본문 안의 조건 노드가 반례다
(`if_condition_6` flags=`['condition','loop_body']`, 후속 2개). 예외가 터지지 않는 이유는
**루프 본문 진입 금지 정책 덕분에 거기까지 도달하지 않기** 때문이다. 즉 S3 은 §4 정책을
전제로 하며, 정책을 풀면 즉시 깨진다.

- 이 성질은 configuration 의 성질이 아니라 **분석 결과 `A` 의 성질**이다 → `WellFormedSnapshot(A)`
- 예외 발생 위치를 렌더 경로에서 `navEdges` 구성 시점으로 옮겨야 한다(현재는 React 렌더 중
  throw → 화면이 하얘진다). **미반영**
- 데이터셋 전수 검사 **미수행** (§7)

### 3.4 생사 판정은 노드 단위로 충분하다 — 단 loop exit 만 예외

`forwardCands` 는 간선이 아니라 **목적지 노드**의 ⊥ 로 후보를 거른다. 그게 건전한 이유:

> 조건 노드의 모든 출력 간선은 **비착지 분기 더미**를 지나고, 그 더미가 간선의 refine 된 환경을
> 담는다. 그래서 간선이 ⊥ 면 더미도 ⊥ 가 되어 노드 단위 판정으로 잡힌다.

실측 확인 — `if (n>8) { require(n<3); } else {…}` 에서 `require_condition_6 --true--> require_true_6`
의 `require_true_6` IN 이 `n=⊥` 이므로 `forwardCands(require_condition_6) = []`.

**유일한 예외가 loop exit 이다.** `isLandable` 의

```ts
if ((has(n,'branch_then') || has(n,'branch_else')) && !has(n,'loop_exit')) return false
```

때문에 `loop_exit` 은 **착지 가능한 유일한 분기 더미**이고, 혼자서 두 역할을 겸한다 —
헤더 false 간선의 refine 을 담는 분기 더미이면서 `break` 들이 모이는 merge. 그래서
`while(true) { if (n>5) break; }` 에서

```
while_cond_5 --false--> while_exit_5     이 간선은 ⊥ (엔진이 join 에서 제외했다)
Break_7      --normal-> while_exit_5     살아 있는 것은 이쪽뿐
while_exit_5 IN n=[6,10]                 ← break 경로의 값. 노드는 살아 있다
```

**불가능한 전이를 "조건 실패로 탈출" 로 보여주고 있었다.** 엔진은 그 간선을 이미 ⊥ 로 알고
있었는데 그 정보가 화면에서 사라졌다.

판별 기준을 정식화하면: 노드 단위 판정이 불충분한 노드는 **착지 가능 ∧ 선행 ≥ 2 ∧ 선행 중
조건 노드가 있는** 노드이고, 8개 구문 패턴 실측에서 그런 노드는 `loop_exit`(break 있는 루프)
**하나뿐**이다. 선행이 1개면 노드 IN == 그 간선의 flow 이므로 두 판정이 일치한다.

→ **그래서 범용 간선 feasibility(옛 K4)는 구현하지 않았다.** §4 의 탈출 요약이 그 유일한
구멍을 메운다. (아래 §6 참조)

### 3.5 추상 상태를 조건화하는 것은 **구문적 위치**이지 탐색 경로가 아니다

> 분기 본문 안의 노드는 그 분기 조건으로 refine 된 환경을 갖는다. 조건화되지 **않는** 것은
> 사용자가 수행한 operation 열(navigation path)이다. **같은 노드는 어떤 경로로 도달해도
> 같은 상태를 보인다.**

실측:

```
if (n > 8) { s = 1; } else { s = 2; }        n ∈ [0,10]
  if_condition_5  IN  n=[0,10]
  Assign_6 (then) IN  n=[6,10]   ← refine 적용
  Assign_8 (else) IN  n=[0,5]    ← refine 적용
  if_join_5       IN  n=[0,10]  s=[1,2]   ← 조건이 사라진다
```

| | |
|---|---|
| 일치 | 분기 본문 내부 — 그 위치에 있다 = 그 조건이 성립한다 |
| 갈라짐 ① | **merge** — 양쪽의 join. then 으로 왔든 else 로 왔든 같다 |
| 갈라짐 ② | **loop exit** — false 간선과 break 의 join (§4 가 분리한다) |
| 갈라짐 ③ | **다른 경로로 같은 노드** — Jump / Continue / backward+forward 가 모두 같은 상태 |

③ 이 **`JumpToLine` 이 건전한 이유**다. 한계가 아니라 설계의 귀결로 서술한다.

**관련 UI 결함 (미수정).** `StateLocals.tsx:20-31` 의 "도달 경로" 가 `cfg.edges` 에서 첫 선행을
따라 역추적한다 → (i) 사용자가 실제로 탐색한 경로가 아니고 (ii) 경로를 그려서 보여주는 행위가
"상태가 그 경로에 조건화되어 있다" 고 암시한다. 라벨을 "가능한 경로 하나(예시)" 로 바꾸거나 제거.

### 3.6 `Feas` → `MayReach`

```
MayReach(n) ≡ IN(n) ≠ ⊥          ⊥ = 환경의 **한 변수라도** ⊥
```

- `MayReach(n) = false` ⟹ 구체적으로 도달 불가 (증명된 쪽)
- `MayReach(n) = true` ⟹ 실제 도달 가능성은 **불확실** (비관계적 interval 도메인이라
  `require(a+b>c)` 같은 복합식의 제약이 `a`, `b` 로 되돌아가지 않는다)

`Feas`("feasible")는 보장보다 센 이름이므로 개명한다. UI 문구 `"도달 불가(⊥) — L_c 조건의 이
분기는 현재 입력에서 성립할 수 없습니다"` 는 증명된 방향이라 정확하다.

**왜 `some(⊥)` 이고 `all(⊥)` 이 아닌가.** 환경은 변수별 구간의 곱 도메인이라
`γ(env) = { σ | ∀x. σ(x) ∈ γ(env(x)) }` 이고, 한 변수가 ⊥ 면 `γ(env(x)) = ∅` 이므로 전체 곱이
공집합이다. `all` 로 바꾸면 도달 불가 노드를 도달 가능하다고 말하게 되어 ⊥ 진단이 무력화된다.
실측에서는 엔진이 모순 조건에 `_set_bottom_env` 로 환경 **전체**를 ⊥ 로 덮으므로
(`if_true_6` IN = `n=⊥ m=⊥ total=⊥ s=⊥`) 둘이 같은 답을 낸다 → **부분 ⊥ 의 존재 여부가
실질 쟁점**이고 미측정이다(§7).

### 3.7 빈 환경 규칙

`in_state` 가 비는 노드는 정확히 **루프 본문의 문장 노드**다 (`engine_api._in_of`):

```python
# IN 이 없는 노드: 문장이 없으면 IN == OUT(join·분기), 문장이 있으면 worklist 를 거치지 않은
# 루프 본문(fixpoint 가 처리) — 실행 전 상태를 따로 갖지 않으므로 비워 둔다
if ist_given is not None: return ist_given
return st if not getattr(n, "statements", None) else {}
```

실측: `Break_7`(loop_body) IN=`{}` OUT=`{n:[6,10],…}` / `Assign_11`(본문 밖) IN 채워짐.

§4 의 **양방향** 루프 본문 금지가 확정되었으므로 `isDead` 가 루프 본문 노드에 호출될 일이
없어진다 → `isDead` 의 `in_state → state` 폴백은 도달 불가 코드가 되고, **단정으로 전환**해야
한다("IN 상태로만 생사를 판정한다" 를 예외 없이 쓸 수 있게 된다). **미반영**.

### 3.8 스냅샷 불변성 (Session 계층)

```
Σ = (A_I, C)       A_I = 입력 영역 I 에서 계산한 스냅샷 (CFG + IN/OUT + callees + diagnostics)

navigation : (A_I, C) --o--> (A_I, C')              ← A 불변
Apply(I')  : (A_I, C) ------> (A_{I'}, C_reset)
Reconcile  : (A_I, C) ------> (A_{I'}, C'')          코드 편집: 프레임 유효성만 검사
```

- **Snapshot immutability** — 모든 navigation operation 에서 `A` 가 보존된다.
  = "navigation 중 엔진 호출 0회" 의 형식적 표현
- **Snapshot consistency** — `∀f ∈ stack(C'). f.node ∈ Nodes(A_{I'})`.
  구현은 이미 있고(`useNavigation` 의 `valid` 검사) 이름이 없었다

---

## 4. 루프 — 본문은 양방향 블랙박스, 헤더가 유일한 요약 지점

**결정(사용자, 라운드 10).** 루프 본문은 forward·backward **양방향 모두** 진입 금지.
그러면 루프 헤더가 "이 루프가 어떻게 끝날 수 있는가" 의 유일한 요약 지점이어야 한다.

`loop_cond` 는 **착지 가능으로 유지**한다 — `loopDelta`(진입 전 ↔ 수렴 후 바뀐 변수,
실측 `L5 │ loopDelta │ {'i': '[0,3]'}`)가 기록되는 곳이고, 구체 디버거라면 3번 step 해야 알
수 있는 것을 한 화면에 보여주는 지점이다. 루프 본문 비착지 결정의 근거가 바로 이것이다.

### 4.1 탈출 경로 요약 (K3)

루프 헤더 노드에 `loop_exits: LoopExit[]` 를 싣는다.

```
LoopExit = { kind, from_, to, feasible, state }
kind ∈ { normal, break, return, revert, other }
```

- `normal` = 헤더 false 간선(조건 실패) / `break` = 본문의 break / `return`·`revert` = 본문에서
  함수 종료(loop exit 를 거치지 않고 RETURN/ERROR sink 로 간다) / `other` = 그 밖(중첩 루프 등)
- `feasible=false` → 그 경로는 ⊥ (엔진이 join 에서 제외한 것). **후보로 제시하지 않는다**
- `state` = **그 경로만의** 환경. loop exit 노드의 `in_state` 는 모든 경로의 join 이라
  normal 탈출과 break 탈출을 구별할 수 없었다

엔진은 이미 loop exit 의 env 를 선행별로 계산해 join 한다(`Engine.py` 의 `exit_env` 루프).
그 중간 flow 를 버리지 않고 깊은 복사로 남기는 것이므로 **추가 계산이 없다.**

실측:

```
while (true) { if (n>5) break; i=i+1; }        n ∈ [0,10]
  normal  feasible=False                      ← 엔진이 ⊥ 로 판정한 간선. 후보에서 제외됨
  break   feasible=True   n=[6,10]

while (i<3) { if (n>5) break; i=i+1; }
  normal  feasible=True   i=[3,3]             ← 조건 실패로 나왔다 (i 가 3 에 도달)
  break   feasible=True   i=[0,2]             ← break 로 나왔다 (3 에 도달하기 전)
  ※ while_exit 의 in_state 는 i=[0,3] — join 이라 둘을 구별할 수 없었다

while (i<3) { if (n>5) return i; i=i+1; }
  normal  feasible=True   i=[3,3]  -> while_exit
  return  feasible=True   n=[6,10] i=[0,2]  -> RETURN

while (i<3) { require(n>5); i=i+1; }
  normal  feasible=True   i=[3,3]
  revert  feasible=True   n=[0,5]  -> ERROR    ← "이 루프는 n ≤ 5 에서 revert 할 수 있다"

for (j=0; j<5; j=j+1) { s=s+1; }
  normal  feasible=True   j=[5,5]
```

**알려진 비일관 (문서화 필요).** `for` 의 `normal` 요약은 `j=[5,5]` 를 담는데, loop exit 노드의
`in_state` 에는 `j` 가 없다(`scope_anchor` 가 for-init 변수를 루프 밖에서 제거). 요약 쪽이 더
유용하지만("루프가 j=5 로 끝났다" 는 다른 어디에도 없다) 다음 줄에서 `j` 가 사라지므로
"루프 탈출 시점의 값(루프 지역변수 포함)" 이라고 라벨해야 한다.

### 4.2 `navEdges` 명세

```ts
NavEdgeKind = 'normal' | 'true' | 'false'
            | 'loop_exit_normal' | 'loop_exit_break' | 'loop_exit_return'
NavEdge     = { from, to, kind, via?, state? }
```

구성 (CFG 당 1회, `WeakMap` 캐시):

1. `back_edge` 제외 (fixpoint 로 흡수)
2. 착지 노드(+`entry`)만 출발점으로 삼고, ⊥ 노드는 제외
3. **루프 헤더이고 `loop_exits` 가 있으면** 그것이 유일한 출구 정의
   - `feasible=false` 와 `kind='revert'` 는 제외 (ERROR sink 는 착지 대상이 아니다 —
     요약으로만 보여준다)
   - `to = (le.to === return_exit) ? cfg.exit : landFwd(le.to)`
4. 그 밖에는 `fwdEdges` 의 각 간선을 `landFwd` 로 접어 착지 노드 간 간선으로
5. `Succ`/`Pred` 를 **이 집합에서** 읽는다

얻는 것:

```
루프 본문 안 return:
  while_cond_5 -> while_exit_5  [loop_exit_normal]
  while_cond_5 -> EXIT          [loop_exit_return]  via=Return_7
  Succ(while_cond_5) = [while_exit_5, EXIT]
  Pred(EXIT)         = [while_cond_5, Return_11]        ← 대칭 복구, 정보 손실 없음

while(true) + break:
  while_cond_5 -> while_exit_5  [loop_exit_break]  via=Break_7
  Succ(while_cond_5) = [while_exit_5]                   ← 불가능한 normal 전이가 사라졌다
```

부수 효과: `while(true)` 에 break 가 **없으면** 모든 요약이 infeasible 이라
`Succ(loop_head) = {}` → "이후 모든 경로가 도달 불가" 가 뜬다. 무한 루프에 대해 올바르다.

---

## 5. 결정 로그

| | 결정 | 라운드 |
|---|---|---|
| D3 | StepOut 후 표시는 caller 의 호출 지점 **post** (phase 도입) | 9 (설계 확정, 미구현) |
| D10 | 리셋을 유지하되 근거를 "유일성" 이 아니라 `depth=1` + 출발 문맥 독립성으로 | 9 |
| O11 | `Next/PrevIntent` 유지 (ordered positioning 별도 계층) | 9 |
| — | `SelectSuccessor`: **StepForward 가 분기에서 멈춘다**(안 A). `highlightedSuccs` 형제 허용 절 제거 | 10 승인, 미구현 |
| — | `JumpToLine` 을 `JumpLocal`(프레임 국소, 스택 보존) / `JumpRoot`(리셋) 로 분리 | 10 승인, 미구현 |
| — | `for_init` 비착지 (`landingAtLine` 의 배열 순서 의존 제거) | 10 승인, **K1 완료** |
| — | 루프 탈출 summary 간선 | 10 승인, **K3 완료** |
| — | `Apply` 후 **타깃 entry 리셋**. 커서 보존하지 않고 breakpoint 로 보완 | 10 승인, 미구현 |
| — | `popToFrame` 제거 | 10 승인, 미구현 |
| — | 루프 본문 **양방향** 진입 금지 | 10 승인, **navEdges 로 완료** |
| — | `Compare` 제거 | 10 승인 |
| — | `avail(n)` 도입, 착지 가능 집합은 건드리지 않음 | 10 승인 |

### 5.2 try/catch — 조건 체인 재설계 (C, 2026-10-29)

**바뀐 모델.** catch 절들을 `try_cond` 의 **병렬 false 간선**에서 `if`/`else if` 와 **동형인
순서 있는 조건 체인**으로 바꿨다. Solidity 의 catch 절은 에러 타입으로 위에서부터 매칭되어
**처음 맞는 하나만** 실행되므로, 병렬 모델은 순서와 배타성을 잃고 있었다.

```
try_cond(cond_expr=None, 호출을 statement 로) --true--> try_true --> [본문] --> try_join --> 다음
                                             --false-> catch_cond_1 --true--> catch_true_1 --> [본문] --> catch_join_1 --> try_join
                                                       catch_cond_1 --false-> catch_cond_2 --true--> … --> catch_join_2 --> catch_join_1
                                                                              (마지막 catch 에 false 간선 없음)
```

**다섯 가지 결함이 같이 해소됐다.**

| | 재설계 전 | 후 |
|---|---|---|
| 루프 안 catch/try 분기 소멸 (**불건전**) | `h: return 1` → catch IN `⊥`, 루프 탈출 `s=[3,3]` | catch `s=[99,99]`, 탈출 `s=[3,99]` |
| `try … returns (r)` 반환값 바인딩 | `r = ⊤` (호출이 계산한 값을 버렸다) | `r = [1,11]` |
| `returns` 변수 스코프 | try 앞·try/catch 뒤에도 `r` 이 보였다 | 성공 블록 한정 |
| 줄 단사성(S2) | `} catch {` 줄에 착지 2~3개, 닫는 `}` 에 0개 | 각 1개 |
| `is_loop_body` 전파 누락 | 루프 안 try/catch 하위 노드가 NG 에서 고립(= 걸리지만 작동하지 않는 breakpoint) | 전파됨 |

**핵심 메커니즘.** `try_cond`·`catch_cond` 의 `condition_expr` 를 **None** 으로 뒀다.
`Engine._edge_env_from_pred` 와 `_edge_flow_from_node_out` 이 둘 다 `cond_expr is not None` 을
가드로 두므로, None 이면 refine·feasibility 경로를 **통째로 건너뛴다** → 두 분기가 항상
feasible 하다. `_fixpoint_impl` 은 조건 타입으로 분기하지 않으므로(균일하게
`transfer_function` + `_edge_flow_from_node_out`) **그쪽에 핸들러를 넣을 필요가 없었다** —
수정은 "핸들러 추가" 가 아니라 "술어 제거" 였다.

호출은 `try_cond` 의 **statement** 로 옮겼다(`VarDecl` / 다중 반환은 선언문들 + 튜플 대입).
그래서 평범한 대입 경로가 반환값을 바인딩하고, callee 가 **착지 가능한** 노드에 등록되어
Step Into 가 유지되고, 평가가 1회로 끝난다(예전엔 간선마다 평가해 catch 3개면 4회).

**말단 false 간선을 만들지 않는 이유.** `if` 는 else 가 없어도 조건이 거짓이면 *if 다음으로
계속 진행*하므로 join 으로 가는 false 간선이 필요하다. 반면 맞는 catch 절이 없으면 *예외가
함수 밖으로 전파*되어 이어지는 경로가 없다. 체인 유비가 깨지는 유일한 지점이다. 말단을
join 으로 보내면 **호출 이전 환경이 join 에 섞여** 모든 try/catch 의 정밀도가 떨어진다
(`s = [1,2]` → `[0,2]`). ERROR sink 로 보내는 안도 검토했으나, 간선을 아예 만들지 않는 쪽이
정밀도가 같고 catch-all 판정도 필요 없다.

**구현 중 발견한 것 하나.** `try_false_stub` 이 **catch 탐색의 BFS 경로를 받치고 있었다.**
`get_current_block(context="catch")` 는 "L 이전의 join 을 찾고 predecessor BFS 로 앞 조건을
찾는다"(학위논문 Dependent-Context Insertion). try 본문이 `return`/`revert` 로 끝나면 본문이
RETURN/ERROR sink 로 가므로 **join 에 들어오는 간선이 하나도 없어** BFS 가 실패한다. 예전엔
`false_stub → join` 간선이 우연히 그 경로였다. 이미 있는 `join.scope_anchor`(그 join 을 만든
조건 노드) 역포인터로 O(1) 조회하도록 바꿨다 — BFS 보다 견고하다.

**남는 한계 (명시).** 호출의 상태변수 부수효과가 분기 **이전**에 적용되므로 catch 경로에도
남는다. 실제 Solidity 는 revert 시 롤백한다. 재설계 전과 같은 동작이고 revert 롤백 모델링은
범위 밖이다.

**검증.** `temp/test_try_catch.py` 30/30 (재설계 수용 기준 T3–T7 10개를 **재설계 전에 먼저
실패 상태로 박아 두고** 통과로 뒤집힌 것을 확인), `temp/test_nav_symmetry.py` 25 케이스
S1/S2/S3/S4 전부 통과, 나머지 9개 회귀 스크립트는 uid·타이밍 외 출력 동일, `npx tsc --noEmit` 통과.

**범위 밖.** `try new T()` (컨트랙트 생성) → §9.1. slice 단계에서 명시적으로 거부한다.

### 5.1 이전 결정의 번복 하나

`ContractAnalyzer` 의 for-init 주석에 사용자 결정이 기록되어 있다 —
*"init 노드에도 for 헤더 라인을 준다. `uint256 i = 0` 은 실제로 실행되는 문장인데 `src_line` 이
없어서 프론트가 Step Forward 로 착지하면 코드 창에 하이라이트되는 줄이 없었다."*
K1 은 그 노드를 **비착지**로 만들어 같은 문제를 다르게 해결한다(착지하지 않으므로 하이라이트할
줄이 필요 없고, land closure 가 `for_cond` 로 밀어 준다). `src_line` 부여 자체는 유지한다.

---

## 6. 구현 현황

| | 내용 | 상태 |
|---|---|---|
| **K1** | `CFGNode.is_for_init` 신설, `for_init`/`for_incr` → `loop_init`/`loop_incr` 플래그, `isLandable` 에서 제외 | ✅ |
| **K2** | 함수·생성자·modifier **EXIT 에 닫는 `}` 줄** 부여 | ✅ |
| **K3** | 루프 탈출 요약 `loop_exits` (엔진 → 직렬화 → 프론트 타입) | ✅ |
| **K4** | 범용 간선 feasibility | ❌ **불필요 판정** — §3.4 에서 노드 단위 판정이 `loop_exit` 외에는 충분함을 확인. K3 이 그 유일한 구멍을 메운다. 코어 수정 범위를 줄였다 |
| — | `navEdges` 단일 관계 + `Succ`/`Pred` 도출 | ✅ |
| — | `temp/test_nav_symmetry.py` (S1–S4, **25 케이스** — try/catch 9개 포함) | ✅ |
| **C** | **try/catch 를 조건 체인으로 재설계** (아래 §5.2) | ✅ |

### K2 가 왜 필요했나

```
L13 | }    | line_info 는 EXIT 를 L13 에 걸어 두는데
EXIT       | src_line = -1  → display_line = -1
```
```ts
const isBp = (cfg, fileKey, id) => {
  const n = navIndex(cfg).byId.get(id)
  return !!n && n.display_line >= 0 && ...   // EXIT 는 -1 → 영원히 false
}
```

EXIT 는 **착지 가능한 유일한 `display_line = -1` 노드**였고, 그래서 **함수 끝에 breakpoint 를
걸 수 없었다.** `Apply` 후 entry 리셋(라운드 10 결정)의 보완 수단이 breakpoint 이므로
전제조건이다. 수정 후 실측: 함수 닫는 `}` 줄이 들어간다(L8/L9/L11/L12/L13 케이스별 확인).

회귀: 기존 `temp/` 10개 전부 통과, `smoke_engine_api` 의 출력 차이는 uid(uuid4, 매 실행 변동)와
`EXIT line=-1 → line=8` 뿐(uid 정규화 후 동일). 프론트 `npx tsc --noEmit` 통과.

### 프론트 P1 — 완료 (2026-10-29)

전부 `frontend/src/debug/` 안이고, `npx tsc --noEmit` 통과. **프론트 전용 테스트 하네스가
없다**는 점이 이 묶음의 약점이다 — `temp/test_nav_symmetry.py` 는 `navGraph` 이식본만 검사하고
`useNavigation`(설정 전이)은 가드가 없다.

| 항목 | 내용 |
|---|---|
| StepForward/Backward 가 분기·합류점에서 **멈춘다** | 후보가 2개 이상이면 커서를 옮기지 않고 하이라이트 + 안내만 한다. `selectSuccessor`/`selectPredecessor` 의 형제 허용 절(`\|\| highlightedSuccs.includes(id)`)을 제거해 **"모든 이동은 커서의 NG 간선을 따른다"** 가 참이 됐다 |
| `avail(n)` 을 **out-degree 기준**으로 | `succCandidates`/`predCandidates` 를 직전 step 이 남긴 frontier 대신 **커서의 실제 NG 차수**(`fc`/`bc`)에서 계산한다. 조건 노드인데 후속이 1개인 경우(한쪽 분기 ⊥, 체인의 마지막 catch)에 선택 UI 가 뜨지 않는다 |
| `JumpLocal` / `JumpRoot` 분리 | 줄이 `activeCfg` 에 있으면 프레임 안에서 이동(스택·깊이 보존), 없으면 타깃 CFG 에서 찾아 루트 리셋. 예전엔 늘 `result.cfg` 만 봐서 **다른 파일의 같은 번호 줄로 조용히 날아갈** 수 있었다 |
| `Apply` → 타깃 **첫 착지 노드** 리셋 | `continueRun` 재사용을 끊었다. 예전의 "breakpoint 없으면 EXIT" 는 세 선택지 중 가장 나빴다 |
| `popToFrame` 제거 | `StepOut^k` 와 동일한 파생 연산. Call Stack 클릭은 더 이상 스택을 절단하지 않는다("클릭 = 관찰" 관례). 관찰용 `SelectFrame` 은 **미구현** |
| `loop_exits` UI | 루프 헤더에서 탈출 경로별 **kind·feasibility·그 경로만의 상태**를 보여준다. `feasible=false` 는 "현재 입력에서는 불가능(⊥)" 로 표시 |
| "도달 경로" 라벨 수정 | "가능한 경로 하나(예시)" + "표시 상태는 이 지점의 값이고 도달 경로에 따라 달라지지 않습니다" 주석. 예전 라벨은 (i) 사용자가 실제로 탐색한 경로가 아니고 (ii) 경로 조건화를 암시해 **둘 다 거짓**이었다 |

직렬화 이름도 맞췄다 — `loop_exits[].from_` → **`via`**. `edges[].from_` 은 `_cfg_to_dict` 가
`from` 으로 교정하는데 중첩 필드는 교정되지 않아 규약이 엇갈렸다. `via` 는 "이 경로를 만든
노드" 라는 뜻에 더 맞고 예약어 문제도 없다. 프론트가 받는 JSON 키를 실측 대조했다
(`{kind, via, to, feasible, state}`).

### 남은 구현 (우선순위)

| | 항목 | 꼭지 |
|---|---|---|
| P1 | `SelectFrame`(관찰 — 스택 보존) 신규. 지금 Call Stack 클릭은 아무 일도 하지 않는다 | §1.1 |
| P1 | `useNavigation`(설정 전이)의 테스트 가드 — 현재 없다 | — |
| P2 | `isDead` 폴백 → 단정, 예외 위치를 `navEdges` 구성 시점으로 | §3.3, 3.7 |
| P2 | `Feas` → `MayReach` 개명 | §3.6 |
| P2 | `Σ = (A_I, C)` 세션 계층 + `Reconcile` 분리 | §3.8 |
| P2 | `Δ(n) = {x | IN(n)(x) ≠ OUT(n)(x)}` — 기존 계산값 조회만 | — |

---

## 7. 측정해야 할 항목 (전부 미수행)

파일럿(`analyze → evaluate`, 데이터셋 타깃)에서 세어야 하는 것들. 1·2 는 RQ 의 **적용 범위**에
직접 들어간다.

| | 항목 | 왜 |
|---|---|---|
| 1 | `WellFormedSnapshot(A)` 위반 함수 수 (S3: frontier 의 비착지 노드가 분기하는가) | 깨지면 그 함수는 navigation 자체가 불가능하고 지금은 UI 가 죽는다. assembly·중첩 modifier 가 후보 |
| 2 | 줄 단사성(S2) 위반 함수 수 | `try/catch` 외에 또 있는가 |
| 3 | **부분 ⊥** 노드 수 (일부 변수만 ⊥) | §3.6 의 `some`/`all` 쟁점이 실존하는지. 0 이면 논쟁이 공집합 |
| 4 | 빈 `in_state` 노드 수 (루프 본문 외에 있는가) | §3.7 폴백 제거의 전제 |
| 5 | 엔진 `line_info` 색인 ↔ 프론트 착지 색인 불일치 | 실측에서 L11 `}` 이 엔진은 `else_if_join_7`, 프론트는 `if_join_5`. 지금은 identity 라 무해하지만 아무것도 검사하지 않는다 |
| 6 | 루프 탈출 경로 분포 (normal only / +break / +return / +revert, infeasible 비율) | §4 의 효용 측정 |
| 7 | `top_vars` 가 interval-⊤ 를 탐지하지 못하는 문제 | RQ1 의 ⊤ 지표 (이전 라운드 이월) |
| 8 | 한 줄에 non-builtin 호출 ≥ 2 인 비율 — 측정됨: **21.7%** of call-bearing lines, 36.3% of call-bearing functions, nested 1,332/6,209 | D3 phase 설계 근거 |

### 참고 — 이미 측정된 수치

| | 값 | 모집단 |
|---|---|---|
| `for` 포함 함수 | 203 (4.71%) | 선정 551 파일, 함수 4,314 |
| `while` 포함 함수 | 58 (1.34%) | 동 |
| `try/catch` 포함 함수 | 7 (0.16%) | 동. 전부 OZ 보일러플레이트 |
| 루프 본문에 `break` | 13 (8.2%) | 루프 보유 함수 159 |
| 루프 본문에 `return` | 10 (6.3%) | 동 |

---

## 8. 철회한 주장 (기록)

논문에 쓰면 안 되는 것들. 전부 실측으로 반증되었다.

| 주장 | 왜 틀렸나 |
|---|---|
| "엔진이 루프 탈출 간선을 refine 하지 않는다 — `while(i<3)` 의 false 분기가 `[0,3]`" | **틀렸다.** `while_exit_5` IN `i=[3,3]`. `[0,3]` 은 루프 **헤더의 OUT**(고정점, refine 전)이다. `Engine.py` 의 `exit_env` 루프가 선행별로 `_edge_flow_from_node_out` 을 적용한다. 따라서 "navigation 의 도달성이 엔진 부정밀성에 의존한다" 는 근거도 전부 철회 |
| "`while(true)` 는 false 간선이 없어 Step Forward 가 루프 탈출을 못 찾는다" (외부 검토 §7.2) | **틀렸다.** `while_cond_5 -> while_exit_5 [false]` 는 조건 충족 가능성과 무관하게 구조적으로 생성된다. 진짜 결함은 §3.4 (간선은 ⊥ 인데 노드가 살아 있다) |
| "경로 선택이 추상 상태를 조건화하지 않는다" | 너무 거칠다. 분기 본문은 조건화된다. 정확한 서술은 §3.5 |
| "`isDead` 의 `in_state → state` 폴백은 문서화되지 않은 위험" | 과했다. `engine_api._in_of` 에 근거가 적혀 있고 대상이 루프 본문 문장 노드로 특정된다. 결함이 아니다 |
| "`else if` 줄에 착지 노드가 둘" / "`else if` 는 다중 착지와 무관" | 둘 다 부분적으로만 맞다. 엔진 `line_info` 에는 L7 에 2개(`if_join` + `else_if_condition` — 학위논문 Table 대로), 프론트 착지는 1개(`chain_end_line` 이 join 을 L11 로 옮긴다). **두 자료구조가 다르다** |
| "비착지 노드는 out-degree ≤ 1" | **틀렸다.** 루프 본문 안의 조건 노드가 반례. 올바른 서술은 §3.3 (frontier 한정) |
| "`Continue` 는 항상 breakpoint 에서 멈춘다" | **틀렸다.** `resolveStops` 가 후보가 갈라지는 분기점에서도 멈춘다 |
| "리셋이 유일하게 가능한 선택" | 과했다. 정책은 여럿이고, 리셋의 근거는 `depth=1` + 출발 문맥 독립성이다 |
| gdb 유비 — "프로세스가 끝나 스택이 사라지니 루트 리셋이 맞다" | IQDebug 에서 함수 종료는 실제로 일어난 사건이 아니다. 근거는 "검색 목표가 없을 때의 결정적 정규화 위치" |

---

## 9. 지원 범위 밖 / Future work

구현 세션이 **의도적으로 하지 않기로** 한 것들을 한 곳에 모은다. 전부 "모르고 빠진 것" 이 아니라
근거를 두고 제외한 것이며, 조용한 오분석을 내는 대신 가능한 곳에서는 **명시적으로 거부**한다.

### 9.1 입력 단계에서 거부하는 구문

`Dependencies.main._UNSUPPORTED` 가 slice 단계에서 `SourceError("지원하지 않는 구문 — …")` 를
낸다. 입력 전제 위반(`_one_statement_violation`)과는 **다른 범주**다 — 전제 위반은 "같은 뜻을
줄만 나눠 쓰면 분석된다" 지만, 이쪽은 분석기가 그 구문 자체를 다루지 않는다.

| 구문 | 근거 | 검증 |
|---|---|---|
| `try new C(...)` (컨트랙트 생성 try) | 빌더의 try 체인은 "try 표현식 = 외부 함수 호출" 만 다룬다. **데이터셋 전체 0건** — 선정 551 파일 0, `DAppSCAN-source` 전체 0, 줄바꿈을 끼운 `try\n new` 까지 multiline 검색으로 확인 | `temp/test_try_catch.py` T8 (거부 + 문자열·주석·`new` 단독 오탐 없음) |

문자열 리터럴·라인 주석은 `_code_only` 로 지운 뒤 검사하므로 `s = "try new thing";` 이나
`// try new C() here` 는 통과한다.

### 9.2 모델링하지 않는 의미론

| | 근거 |
|---|---|
| **revert 롤백** — try 의 호출이 상태변수를 바꾼 뒤 revert 하면 실제로는 되돌아가지만, 분석은 그 부수효과를 catch 경로에도 남긴다 | 호출을 `try_cond` 의 statement 로 두므로 분기 **이전**에 적용된다. 재설계 전과 같은 동작이고, 롤백을 모델링하려면 상태 스냅샷·복원이 필요해 범위를 크게 넘는다 (§5.2) |
| **외부 호출의 반환값** — enterable 하지 않은 호출은 ⊤ | 호출 대상 주소가 미지(`top_vars` 에 `AddressSet ⊤`)이므로 건전한 유일한 값 |
| **어느 catch 절이 잡는가** — 체인의 분기는 조건 없이 둘 다 feasible | 판정에 필요한 "revert 데이터의 에러 타입" 은 interval 도메인으로 표현할 수 없다. 체인이 **순서**는 담지만 **매칭**은 담지 않는다 (§5.2) |
| **맞는 catch 가 없는 경로** — CFG 에 남기지 않는다(말단 false 간선 없음) | join 의 상태에 영향이 없어 건전하다. 잃는 것은 "이 try/catch 는 revert 할 수 있다" 를 디버거가 보여주지 못하는 것뿐. 필요해지면 "마지막 절이 타입 지정이면 false → ERROR 간선 추가" 로 **가산적**으로 넣을 수 있다 (§5.2) |
| **경로별 조건화** — 같은 노드는 어떤 탐색 경로로 도달해도 같은 상태 | 설계의 귀결이지 결함이 아니다. `JumpToLine` 이 건전한 근거이기도 하다 (§3.5) |

### 9.3 보류한 operation·기능

| | 근거 |
|---|---|
| `Compare(A_I, A_I′, n)` — 두 입력 영역의 스냅샷 비교 | intent verdict + `Apply` 가 같은 역할을 한다. 세션이 스냅샷을 하나만 보관하므로(`engine_server._sessions[key]`) 구조 변경 + 메모리 2배가 필요하다 (§1.1) |
| `UnwindToFrame` | `StepOut^k` 와 완전히 동일한 파생 연산. 논문 목록에서 빼고 UI 단축키로만 둔다 (§1.1) |
| `PreviousBreakpoint` | 역방향 호출 경로가 유일하지 않아 전함수가 아니다 (§1.1) |
| 범용 간선 feasibility (옛 K4) | 노드 단위 판정이 `loop_exit` 외에는 충분함을 8개 구문 패턴으로 확인. K3 의 탈출 요약이 그 유일한 구멍을 메운다 (§3.4, §6) |

### 9.4 아직 하지 않은 것 (할 예정)

§6 의 "남은 구현" 과 §7 의 "측정해야 할 항목" 이 이에 해당한다. 9.1–9.3 과 달리 **범위 밖이
아니라 순서상 뒤**인 것들이다.
