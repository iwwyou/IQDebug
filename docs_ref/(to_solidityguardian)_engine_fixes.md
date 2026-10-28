# IntentKeeper → SolidityGuardian 역포팅 대상 엔진 수정

> **방향**: 이 문서는 IntentKeeper 에서 고친 것 중 **SolidityGuardian 에도 적용할 것**을 모은다.
> (반대 방향 = `(solidityguardian)_engine_code_changes.md`)
>
> ⚠ **SolidityGuardian 디렉토리는 읽기 전용이다.** 여기 적힌 것을 Guardian 에 옮길지, 언제 옮길지는
> 사용자가 결정한다. 이 문서는 "무엇을 어떻게 옮기면 되는지"의 명세일 뿐이다.
>
> **적용 여부 확인 방법**: 각 항목의 `Guardian 상태` 는 Guardian 파일을 **읽어서** 확인한 것이다
> (2026-09-19 기준). 포팅 전 재확인 권장.
>
> 관련 로그: `PROGRESS.md` (decision 번호), `Dependencies/ISSUES.md` (의존성 분석 이슈)

---

## 배경 — 왜 이 수정들이 필요했나

IntentKeeper 에서 `Dependencies/objectfile/*.pkl` 을 **전량 재생성**하면서 드러났다.
재생성 전에는 `analyze_file` 이 visitor 예외를 `[ERR]` 로 출력만 하고 넘어갔고, 그 결과
**함수 CFG 에 statement 가 하나도 안 들어간 채 pkl 로 저장**돼 있었다. 그런 함수를 호출하면
크래시도 경고도 없이 **선언된 리턴변수의 기본값(예: uint160 이면 0)을 조용히 반환**한다.
(Guardian 의 `Dependencies/main.py:343` 주석이 지적한 바로 그 현상 —
"web3bugs_35_H_10 케이스 빌드 중 TickMath.getSqrtRatioAtTick / DyDxMath.getDx/getDy 에서 발견,
아직 아무 pkl 도 이걸로 재생성하지 않았음")

**전체 효과 (IntentKeeper 기준, 135개 의존성 소스 재생성)**

| | 수정 전 | 수정 후 |
|---|---:|---:|
| visitor 오류 `[ERR]` | 552 | **212** (−62%) |
| 파싱 오류 `[ANTLR]` | 212 | **192** |
| 본문 있는 함수 | 380 | **384** |
| 본문이 빈 채 저장된 함수 | 39 | **30** |

**복구된 대표 함수** (전부 stmts=0 → 채워짐)

| 함수 | 전 | 후 |
|---|---|---|
| `UnsafeMath.divRoundingUp` | nodes=4, stmts=**0** | nodes=5, stmts=**1** |
| `DyDxMath.getDx` | nodes=9, stmts=**0** | nodes=11, stmts=**2** |
| `DyDxMath.getDy` | nodes=9, stmts=**0** | nodes=11, stmts=**2** |
| `DyDxMath.getLiquidityForAmounts` | nodes=12, stmts=**0** | nodes=17, stmts=**5** |
| `FullMath.mulDiv` | — | nodes=38, stmts=**25** |
| `TickMath.getSqrtRatioAtTick` | stmts=**0** | nodes=110, stmts=**23** |
| `TickMath.getTickAtSqrtRatio` | stmts=**0** | nodes=80, stmts=**69** |

이 함수들에 의존하는 평가 케이스: **web3bugs_35_H_08 / 35_H_10 / 35_H_12**

---

## F1. `require`/`assert` 가 BOTTOM 을 전파하지 않음 ★코어

**파일**: `Interpreter/Engine.py` — `_run_worklist` 의 `condition_node_type in ["require","assert"]` 분기
**Guardian 상태**: 미확인(포팅 전 확인 필요). IntentKeeper 에선 `PROGRESS.md` decision #42.

**증상**: debug 입력이 함수 가드와 모순이면(`a=[200,300]` vs `require(a<=100)`)
`@Post` 가 `Warning` + `internal error: This 'balance' is may be elementary variable but may not be declared`
(risk 0.0)로 나온다. 같은 상황에서 `@During` 은 `Unreachable` 을 낸다 — **두 경로 비일관**.

**근본 원인**: `can_true` 가 False 일 때
```python
else:
    self._set_bottom_env(t.variables)   # t.variables 가 아직 {} → 빈 dict 순회 = no-op
    # work.append(t) 없음 → BOTTOM 이 downstream 으로 전혀 안 퍼짐
```
**바로 위 `if`/`else-if` 분기는 같은 상황을 올바르게 처리**한다(seed → BOTTOM → append).
즉 메커니즘은 이미 있는데 require/assert 분기만 두 줄을 빠뜨렸다.

연쇄: 이후 노드가 전부 `{}` → `_force_join_before_exit` 가 `{}` 들을 join → `EXIT.variables={}`
→ `_is_bottom_env({})` 가 **False** → 도달불가 인식 실패.

**실측 근거**: 정상/모순 모두 `EXIT.preds=1` 로 **그래프는 안 끊긴다**. 차이는 predecessor 의
`variables` 개수뿐(17 → 0). → predecessor 검사로는 못 잡고 env 를 봐야 한다.
그리고 BOTTOM 이 아니라 *빈 dict* 였다 → `_is_bottom_env` 단독으론 못 잡는다.

**수정**
```python
else:
    t.variables = VariableEnv.copy_variables(cur_vars)   # ① seed
    self._set_bottom_env(t.variables)                     # ② BOTTOM
    work.append(t)                                        # ③ 전파
```

**부수 효과(의도됨)**: 직렬화가 `state={}` → `state={a:'⊥', …}` 로 바뀐다. 프론트가 `⊥` 포함
여부로 dead 를 판정한다면 그동안 dead 노드를 못 잡고 있었던 것이 자동 해결된다.

---

## F2. `@Post` 도달불가 판정 + `_exit_env` 폴백 제거 ★코어

**파일**: `Interpreter/Engine.py` `_process_exit_node_intents`,
`Analyzer/GuardianVerificationEngine.py` `_exit_env`
**Guardian 상태**: 미확인. IntentKeeper `PROGRESS.md` decision #42 와 한 묶음.

**수정 A** — `_process_exit_node_intents` 진입부(**try 블록보다 앞**)에 도달불가 판정 추가.
try 안에 두면 그 `except` 가 예외를 삼켜 다시 internal-error 로 둔갑한다.
```python
preds = guardian._preds(fcfg, normal_only=True)
if not preds: unreachable
else:
    env = guardian._exit_env(fcfg, normal_only=True)
    unreachable = (not env) or self._is_bottom_env(env)
```
`_preds` 를 먼저 보는 이유: (a) 정상 경로로 exit 에 닿는 길이 없으면 그 자체로 도달불가,
(b) 아래 수정 B 로 `_exit_env` 가 raise 하므로 여기서 먼저 걸러야 한다.

**수정 B** — `_exit_env` 의 `related_variables` 폴백을 **fail-loud** 로.
```python
preds = self._preds(...)
if not preds:
    raise RuntimeError("_exit_env: no normal predecessor for exit of ...")
```
그 폴백은 `_get_post_exit_node` 가 없던 시절 "explicit return 함수의 EXIT 는 predecessor 0개" 를
덮던 응급처치(**Guardian Initial commit 유래**)이고, 근본 원인은 `_get_post_exit_node` 가
RETURN 을 고르면서 이미 해결됐다. 남겨두면 *도달 불가한* exit 를 "선언 초기값 env" 로 덮어써
**vacuous verdict** 를 만든다. 9가지 함수 형태로 폴백 발동을 시도했으나 **0회** — 사실상 사문화.

⚠ `VerdictStatus` 에 `Unreachable` 이 없다면 함께 추가해야 한다.

---

## F3. `assembly` 분기에서 `find_function_context` 튜플 언패킹 누락 ★최대 효과

**파일**: `Analyzer/SolidityAnalyzer.py` (Guardian: **L291-296**)
**Guardian 상태**: ❗**동일 버그 존재 확인** (L295 `ca.current_target_function = ca.find_function_context(start_line)`)

**증상**: `[ERR] ctx=assembly: unhashable type: 'list'` — **131건**, 10개 파일.
assembly 를 가진 모든 소스가 **첫 Yul 문장부터** 실패해 함수 본문이 빈 채 저장된다.

**traceback**
```
EnhancedYulVisitor.visitYulAssignment
 → ContractAnalyzer.process_yul_assignment
 → ContractCFG.get_function_cfg
 → self.functions.get(function_name)      TypeError: unhashable type: 'list'
```

**근본 원인**: `find_function_context` 는 docstring 대로 **`(함수이름, 파라미터타입리스트)` 튜플**을
반환하는데, assembly 분기만 그걸 통째로 `current_target_function` 에 넣는다.
**전수 확인: 6개 호출부 중 나머지 5개는 전부 정상 언패킹.** 고립된 버그다.

**수정**
```python
if parent == "assembly":
    ca.current_context_type = "assembly"
    ca.current_target_contract = ca.find_contract_context(start_line)
    ca.current_target_function, ca.current_target_function_param_types = \
        ca.find_function_context(start_line)
    return
```

---

## F4. Solidity 16진 리터럴을 signed 로 판정 ★두번째 효과

**파일**: `Analyzer/EnhancedSolidityVisitor.py` `visitLiteralExp` (Guardian: **L2380**)
**Guardian 상태**: ❗**동일 버그 존재 확인**

**증상**: `Bitwise/Shift operands must both be int/uint intervals` — **156건**
(TickMath, PRBMathCommon, PRBMathSD59x18, FixedPoint, BitMath 등)

**근본 원인**
```python
result_expr.expr_type = 'uint' if literal_value.isdigit() else 'int'
#                                                             ^^^^^  16진 → signed
```
Solidity 숫자 리터럴은 10진·16진 **모두 부호 없는 값**이다. 음수는 단항 `-` 이지 리터럴 자체가
음수가 되지 않는다. 10진과 16진을 다르게 취급할 이유가 없다.

**연쇄 (TickMath 실증)**
```solidity
uint256 ratio = absTick & 0x1 != 0 ? 0xfffcb933... : 0x1000...;
```
```
0x1        → 'int' → IntegerInterval
absTick    → uint256 → UnsignedIntegerInterval
absTick & 0x1                       → 타입 불일치 ✗
0xfffcb933... → IntegerInterval → ratio(uint256 선언)가 signed 로 오염
ratio >> 128 (128 은 10진 → Unsigned) → 타입 불일치 ✗
```

**수정**
```python
if literal_value.isdigit() or literal_value.startswith(('0x', '0X')):
    result_expr.expr_type = 'uint'
    result_expr.type_length = 256
```

⚠ **주소 리터럴을 여기서 분리하지 않는다**(검토 후 기각):
- Solidity 주소는 `address(0x..)` **타입 변환**으로 들어오고 별도 경로다
  (`expr_type='address'` 를 세팅하는 유일한 곳은 intent 문법의 `visitAddrLiteralExpr`)
- `Evaluation._literal_is_address` 는 길이 검사가 없어 `0x1` 도 "주소" 로 통과한다
- 이 리포의 맨몸 40-hex 4건은 **전부 비트마스크**였다(`SafeTransferLib` 의 `0xff..ff`).
  길이로 판정하면 그걸 주소로 오분류한다.

**효과**: Bitwise 89 → **0**, Shift 67 → **3**, 미선언 오류 96 → 69.

---

## F5. Yul 리터럴이 `expr_type` 없음 + `literal` 이 int

**파일**: `Analyzer/EnhancedYulVisitor.py` `_build_literal` (Guardian: **L178-194**)
**Guardian 상태**: ❗**동일 버그 존재 확인**

**증상**: `[ERR] ctx=assembly: Unsupported literal expr_type 'None'` — **51건**
(TickMath 36, PRBMathCommon 7, PRBMathSD59x18 5, FullMath 2, UnsafeMath 1)

**근본 원인 (둘)**
1. **`expr_type` 미설정** — 5개 반환 경로 전부. `evaluate_literal_context` 가 `ety` 로 분기하는데
   `None` 이면 모든 분기를 빠져나가 마지막 `raise` 에 걸린다.
2. **`literal` 이 str 이 아니라 int** — `int(getText())` 로 변환해 넣는다. 그런데
   `evaluate_literal_context` 는 `int(txt, 0)` 로 파싱하므로 int 를 주면
   `TypeError: int() can't convert non-string with explicit base` 가 나고,
   그 함수의 `except ValueError` 로는 **안 잡힌다**.
   → ①만 고치면 51건이 `TypeError` 로 바뀔 뿐이다. **둘을 같이 고쳐야 한다.**

**타입 판정 = `'uint'` / 256**: Yul 은 타입 없는 256-bit word 언어다.
이 리포 소스에서 `slt`/`sdiv`/`smod`/`sar`/`signextend` 는 **0건**, `sgt` 만 7건.

**수정**
```python
if ctx.DECIMAL_NUMBER():
    return Expression(literal=ctx.DECIMAL_NUMBER().getText(),
                      expr_type='uint', type_length=256, context="LiteralExpContext")
if ctx.HEX_NUMBER():
    return Expression(literal=ctx.HEX_NUMBER().getText(),   # '0x..' 원문 그대로
                      expr_type='uint', type_length=256, context="LiteralExpContext")
if ctx.STRING_LITERAL():
    return Expression(literal=ctx.STRING_LITERAL().getText(),
                      expr_type='string', context="LiteralExpContext")
text = ctx.getText()
if text in ('true', 'false'):        # Yul 에 bool 없음 → 1/0 word
    return Expression(literal='1' if text == 'true' else '0',
                      expr_type='uint', type_length=256, context="LiteralExpContext")
raise ValueError(f"Unknown yulLiteral: {text!r}")   # 무조건 0 반환 폴백 제거
```

**효과**: `UnsafeMath` 오류 1→0, `FullMath` 2→0. `DyDxMath` 본문 복구.

---

## F6. 비트 연산 `& | ^` 이 `Evaluation` 디스패치에 없음

**파일**: `Interpreter/Semantics/Evaluation.py` `evaluate_binary_operator`
**Guardian 상태**: ❗**동일 누락 확인** — 디스패치 0건, 그러나 `Domain/Interval.py` 에
`bitwise`/`bitwise_not` 은 **이미 5개 존재**한다. 즉 **도메인은 있는데 배선만 없다.**

**증상**: `Unsupported operator '&' in expression: absTick & 0x2` 등 **약 105건**
(전부 Solidity 식. `ctx=if` 92, `simpleStatement` 10)

**중요**: 식 평가가 실패하면 대입문이 통째로 실패해 **LHS 가 이전 값을 유지**한다 —
⊤ 로 떨어지는 게 아니라 **조용한 오분석**이다.

**수정** (시프트 분기 바로 뒤, 시프트와 **동일한 규약**: 타입 일치 요구, 불일치 시 예외)
```python
elif operator in ('&', '|', '^'):
    if (isinstance(leftInterval, IntegerInterval) and
            isinstance(rightInterval, IntegerInterval)):
        result = leftInterval.bitwise(operator, rightInterval)
    elif (isinstance(leftInterval, UnsignedIntegerInterval) and
          isinstance(rightInterval, UnsignedIntegerInterval)):
        result = leftInterval.bitwise(operator, rightInterval)
    else:
        raise ValueError(f"Bitwise operands must both be int/uint intervals, got "
                         f"{type(leftInterval).__name__} and {type(rightInterval).__name__}")
```
단항 `~` 는 `evaluate_unary_operator` 에 **이미 배선돼 있다**(`bitwise_not()`).

⚠ **F4 와 함께 적용해야 한다.** F6 만 넣으면 105건이 "Unsupported operator" 에서
"타입 불일치" 로 바뀔 뿐이다(실측: 89건이 그렇게 됐다).

⚠ **논문 claim 확인 필요**: IntentChecker 원고 Limitations 가 Yul 지원에
"bitwise operations, shifts" 를 포함한다고 적었는데, Yul `_BINARY_OPS` 는 `and/or/xor` 를
`&/|/^` 로 매핑하므로 **이 디스패치가 없으면 Yul 비트 연산도 동작하지 않았다.**

---

## F10. Yul 비교 연산이 `BoolInterval` 을 냄 (Yul 엔 bool 이 없다)

**파일**: `Analyzer/EnhancedYulVisitor.py` — `_BINARY_OPS` 매핑 / `_build_function_call`
**Guardian 상태**: ❗**동일 구조** (`'gt': '>'` 등으로만 매핑, word 변환 없음)

**증상**: `Shift operands must both be int/uint intervals, got UnsignedIntegerInterval and BoolInterval`
— TickMath 41건 중 **40건이 이 하나에서 연쇄**.

**실패 패턴** (3줄 묶음이 8회 반복)
```yul
let f := shl(7, gt(r, 0xFFFF...))   ← Shift: Unsigned << BoolInterval  ✗
msb := or(msb, f)                    ← 'f' 미선언 (앞줄이 실패해 선언 안 됨)
r   := shr(f, r)                     ← 'f' 미선언
```

**근본 원인**: **Yul 에는 bool 타입이 없다.** `lt`/`gt`/`slt`/`sgt`/`eq`/`iszero` 는
**1 또는 0 인 256-bit word** 를 반환하고 그대로 산술·시프트에 쓰인다.
`shl(7, gt(r, C))` = "조건이 참이면 128, 거짓이면 0" 은 흔한 Yul 관용구다.
그런데 `_BINARY_OPS` 가 `'gt' → '>'` 로만 매핑해 Solidity 비교로 평가되고 `BoolInterval` 이 나온다.

**수정**: 비교 계열 결과를 `cond ? 1 : 0` 삼항으로 감싸 uint word 로 만든다.
```python
_CMP_OPS = {'lt', 'gt', 'slt', 'sgt', 'eq'}      # and/or/xor 는 비트연산이므로 제외

@staticmethod
def _word(literal: str) -> Expression:
    return Expression(literal=literal, expr_type='uint', type_length=256,
                      context="LiteralExpContext")

def _as_word(self, cond: Expression) -> Expression:
    return Expression(condition=cond, true_expr=self._word('1'), false_expr=self._word('0'),
                      operator='?:', context="ConditionalExpContext")
```
호출부: 2인자 binary op 가 `_CMP_OPS` 면 `_as_word(...)` 로 감싸고,
`iszero` 도 `_as_word(...)` 로 감싼다(우변 리터럴도 `_word('0')` — F5 의 문자열 규약).

**정밀도 손실 없음**: `evaluate_conditional_expression_context` 가 `BoolInterval [1,1]`/`[0,0]`
이면 해당 분기로 **정확히 접고**, `[0,1]` 이면 두 분기를 join 해 `[0,1]` 을 준다. ⊤ 로 안 뭉갠다.

⚠ **Yul 경로에만 적용한다.** Solidity 에선 bool 이 산술에 쓰일 수 없으므로 `Evaluation` 쪽에서
`BoolInterval` 을 승격하면 진짜 타입 오류를 덮게 된다.

**효과**: TickMath 41 → 19. **`TickMath.getSqrtRatioAtTick`(nodes=110, stmts 0→23)과
`getTickAtSqrtRatio`(nodes=80, stmts 0→69) 본문 복구** — Guardian 주석이 명시했던 함수들이다.

---

## F11. 시프트 추상 의미론이 불건전 ★코어 · 전수 검증

**파일**: `Domain/Interval.py` — `IntegerInterval.shift`, `UnsignedIntegerInterval.shift`
(+ `Interpreter/Semantics/Evaluation.py` 시프트 디스패치)
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상 ①(불건전)**: 음수 구간에서 **참값을 놓치거나 역전된 구간**을 만든다.
```
[-4,-1] << [0,2] : 참값 (-16,-1) → 구현 (-4,-4)     ← 불건전
[-8,-8] << [0,3] : 참값 (-64,-8) → 구현 (-8,-64)    ← lo>hi 역전
[-8,-8] >> [0,3] : 참값  (-8,-1) → 구현 (-1,-8)     ← 역전
```
전수 검증(w=4/5, wrap 의미론 기준): **signed 49~53%, unsigned 41~44% 조합이 불건전**.

**증상 ②(디스패치)**: `Evaluation` 이 산술처럼 "좌우 타입 일치"를 요구해
`int256 log_2 = (int256(msb) - 128) << 64;`(유효한 Solidity)가 거부됐다.
그 한 줄이 실패하면 `log_2` 선언이 안 돼 뒤따르는 assembly 18줄이 연쇄 실패(TickMath).

**근본 원인**
1. `x << s ≡ x·2^s` 는 **부호에 따라 단조성이 뒤집힌다**. 그런데 구현은
   `lo = x.min << s.min`, `hi = x.max << s.max` 로 **코너를 고정**했다(양수만 가정).
2. unsigned 는 오버플로를 **포화(clamp)** 했다. Solidity 시프트는 revert 없이 **wrap** 하므로
   `(2^255) << 1 = 0` 이다 — 포화는 그 작은 값을 놓친다.
3. 시프트는 산술이 아니다. **좌변=피시프트값 / 우변=시프트양** 으로 역할이 다르고,
   Solidity 에서 시프트 양은 unsigned, 좌변은 signed 여도 된다.

**수정** — 두 클래스의 `shift` 를 동일 로직으로 재정의:
- **4코너 min/max** (고정 코너 폐기)
- 오버플로 → **TOP** (포화 아님)
- **거대 정수를 만들지 않는다**: `'<<'` 는 `s.max ≥ w` 면 계산 없이 TOP,
  `'>>'` 는 `s` 를 `w` 로 캡(그 이상은 결과가 동일 — 0 또는 -1)
- `shift_iv` 는 min/max 만 읽으므로 **타입이 달라도 된다** →
  `Evaluation` 의 타입 일치 요구를 제거(`leftInterval.shift(rightInterval, op)` 로 단순화)

**검증**: 실제 클래스로 w=4/5 전수 — signed/unsigned 각 5,712·29,568 조합 **불건전 0건**
(시프트 양을 uint256 으로 전달해 타입 불일치 상황도 포함).
`uint256 TOP << TOP`, `int256 TOP >> TOP` 모두 **0.0ms** 즉시 반환.

---

## F12. 산술 결과가 타입 범위를 벗어나 **비트수가 폭주** ★코어

**파일**: `Domain/Interval.py` — signed `add`/`subtract`/`multiply`/`exponentiate`,
unsigned `add`/`multiply`/`exponentiate`
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `PRBMathSD59x18.sol` 분석이 **끝나지 않는다**(전체 재생성이 멈춤).
스택: `Interval._mul` ← `multiply` ← `evaluate_binary_operator` ← `fixpoint` ← `reinterpret_from`.

**근본 원인 — 연산자가 아니라 도메인 불변식 부재**:
`multiply` 자체는 4코너 min/max 로 **정확하고 O(1)** 이다. 문제는
`IntegerInterval(lo, hi, w)` 가 `w` 를 **받기만 하고 경계를 강제하지 않는** 것이다.
그래서 결과가 타입 범위를 벗어난 채 다음 연산의 피연산자가 되고,
fixpoint 루프의 `x = x*x` 에서 **비트수가 매 반복 2배**로 자란다.

실측(int256 TOP 에서 시작):
```
시작 255비트 → 1회 511 → 2회 1021 → 3회 2041 → … → 8회 65,281비트
```
각 연산은 O(1)인데 **피연산자가 도메인을 탈출**해 멈춘 것이다.

부수 확인:
- signed `add`/`subtract`/`multiply` 는 **경계 강제가 전혀 없었다**.
- unsigned `multiply` 는 `max` 만 `type_max` 로 포화시키고 **`min` 은 그대로** 뒀다
  → `[거대값, type_max]` 역전 구간이 나온다.
- `exponentiate` 는 `pow(base, exp)` 를 **실제로 계산**한다 → `pow(2, 2**255)` 에서 멈춘다.

**수정**
```python
def _fit_signed(lo, hi, w):      # 벗어나면 TOP (포화 아님 — unchecked 는 wrap)
def _fit_unsigned(lo, hi, w):
def _pow_fits(base, exp, w):     # 비트수로 미리 판정 — 거듭제곱을 수행하지 않는다
                                 #   결과 비트수 ≈ exp · bitlen(base)
```
위 7개 연산의 결과에 적용하고, `exponentiate` 는 `_pow_fits` 가 거짓이면 **계산 없이 TOP**.

⚠ **unsigned `subtract` 는 그대로 둔다** — 이미 유계이고(`[0, a.max]`),
"항상 음수면 bottom"(= underflow revert → 도달불가) 로직이 **checked 의미론으로 load-bearing** 이다
(`require(a <= bal); balance = bal - a` 같은 패턴이 여기 의존한다).

**검증**: `x = x*x` 30회 반복 후 비트수가 255/256 유지, **0.0ms**.
`uint256 2 ** [0, 2^255]` 도 즉시 TOP 반환.

**효과**: 정지 해소 → 전체 재생성 정상 완료(pkl 134, regen1 과 차집합 0).
`[ERR]` 246 → **212**. PRBMathSD59x18 30→23, TickMath 19→14. **증가한 파일 0개.**

⚠ **정밀도 영향**: 오버플로 지점이 포화값 대신 TOP 이 된다. 곱셈은 자주 쓰이므로 영향이 넓다.
다만 기존 포화값은 **틀린 값**이었고, TOP 은 Warning 으로 흐르지 잘못된 Satisfied 를 만들지 않는다.
더 정밀하게 하려면 `unchecked_block` 플래그로 checked 문맥에선 '범위 교집합' 을 쓰면 된다(별건).

---

## F13. Yul 비트 연산의 타입 불일치 — 2의 보수 word 재해석 ★코어

**파일**: `Analyzer/EnhancedYulVisitor.py` (컨텍스트 표식 7곳),
`Interpreter/Semantics/Evaluation.py` (`_is_yul_ctx`, `_reinterpret_as`, 비트 분기)
**Guardian 상태**: ❗**동일** — Guardian 의 `EnhancedYulVisitor` 도
`context="BinaryOperationContext"` 를 쓰고, F6 자체가 미적용이라 비트 분기가 없다.
F6 을 먼저 포팅한 뒤 이 항목을 얹어야 한다.

**증상**: `Bitwise operands must both be int/uint intervals, got IntegerInterval and
UnsignedIntegerInterval` — **15건** (TickMath 14, PRBMathSD59x18 1).
```yul
// TickMath.getTickAtSqrtRatio, L136 부터 6줄 간격으로 14회
log_2 := or(log_2, shl(63, f))
```
좌변 `log_2` 는 Solidity 쪽에서 `int256 log_2` 로 선언됐고(IntegerInterval),
`shl(63, f)` 는 Yul 리터럴이라 unsigned(UnsignedIntegerInterval) 다.

**근본 원인**: **Yul 에는 타입이 없다.** 모든 값이 raw 256-bit word 이고
`or`/`and`/`xor` 는 비트 패턴에만 작용한다. 즉 위 코드는 Yul 로서 **완전히 합법**이다.
엔진은 Solidity 선언의 타입(`int256`)을 Yul 블록 안까지 그대로 들고 들어가고,
F6 에서 넣은 "양쪽 타입 일치" 검사가 그것을 거부했다.
그 검사는 **Solidity 경로에서는 유지돼야 한다**(Solidity 에선 `int & uint` 가 실제 타입 에러).
따라서 "검사를 없앤다"가 아니라 **Yul 경로를 구분**하는 것이 옳은 수정이다.

**수정 (2단계)**

1. Yul 이 만든 표현식에 출처 표식을 남긴다 — `EnhancedYulVisitor` 의
   `context="BinaryOperationContext"` 6곳 → `"YulBinaryOperationContext"`,
   `"UnaryOperationContext"` 1곳 → `"YulUnaryOperationContext"`.
   (Evaluation 은 `context` 를 **분기 조건으로만** 쓰고 문자열 값 자체를 소비하지 않으므로
   접두어 추가는 기존 Solidity 경로에 영향이 없다 — 전 호출부 확인 완료.)

2. 비트 분기에서 Yul 표식이 있을 때만 피연산자를 2의 보수 word 로 재해석한다.
```python
def _is_yul_ctx(expr) -> bool:
    c = getattr(expr, "context", None)
    return isinstance(c, str) and c.startswith("Yul")

def _reinterpret_as(iv, target_cls):
    if iv.is_bottom():
        return target_cls(None, None, iv.type_length)
    w, lo, hi = iv.type_length, iv.min_value, iv.max_value
    if target_cls is UnsignedIntegerInterval:
        return UnsignedIntegerInterval(lo, hi, w) if lo >= 0 \
               else UnsignedIntegerInterval.top(w)      # 부호 비트가 갈림 → TOP
    return IntegerInterval(lo, hi, w) if hi <= (1 << (w - 1)) - 1 \
           else IntegerInterval.top(w)
```
**건전성**: 부호 비트가 구간 안에서 변하지 않으면(`min ≥ 0` 이거나 `max ≤ 2^(w-1)-1`)
비트 패턴이 그대로라 **값 범위가 동일** — 손실이 없다. 변하면 비트 패턴이 갈라지므로
TOP 을 준다. 결과 타입은 **좌변을 따른다**(시프트와 동일 규약).

**효과**: `[ERR]` 212 → **198**. `TickMath.sol` **14 → 0**.
pkl 134 동일, `[ANTLR]` 192 동일, **증가한 파일 0개**, 본문 있는 함수 384 유지.

⚠ **작업 중 자초한 사고 기록**: 이 헬퍼들을 처음에 `class Evaluation` **본문 안에**
컬럼 0 `def` 로 넣었다. 컬럼 0 이 클래스 본문을 끝내버려 아래 55개 메서드가 전부
모듈 레벨 함수가 됐고 `[ERR]` 이 212→**1303** 으로 폭발했다(970건이
`'Evaluation' object has no attribute 'evaluate_binary_operator'`).
모듈 레벨 헬퍼는 반드시 `class` 선언 **앞**에 둘 것. 포팅 시 `ast` 로
`Evaluation` 메서드 수(55)를 확인하면 즉시 잡힌다.

---

## F14. Solidity 숫자 리터럴의 문맥 타이핑 ★코어

**파일**: `Interpreter/Semantics/Evaluation.py` — `_is_numeric_literal`, `_literal_retype`, 비트 분기
**Guardian 상태**: ❗**동일** — F4/F6 과 한 묶음. F4 를 포팅하면 이 문제가 같이 따라온다.

**증상**: F13 적용 후에도 남은 마지막 1건.
```solidity
// PRBMathSD59x18.sol:27  (int256 x, int256 y)
result = (x >> 1) + (y >> 1) + (x & y & 1);
```
`(x & y)` = IntegerInterval, 리터럴 `1` = UnsignedIntegerInterval → 타입 검사 거부.
**Yul 이 아니라 Solidity 경로**라 F13 의 재해석이 적용되지 않는다(적용되면 안 된다).

**근본 원인 — F4 의 필연적 귀결**:
Solidity 에서 숫자 리터럴은 **타입이 없는 유리수**이고, 타입은 **문맥**(상대 피연산자)이 정한다.
`int256 & 1` 에서 `1` 은 `int256(1)` 로 변환되는 것이 언어 규칙이다.
F4 는 `visitLiteralExp` 에서 모든 숫자 리터럴을 uint256 으로 **못박았다**.
그 자체는 옳다 — 음수 리터럴은 존재하지 않고 단항 `-` 로 표현되므로.
빠진 것은 **사용 시점의 문맥 타입 부여**다.

**수정**: 비트 분기에서 타입이 엇갈릴 때, 한쪽이 숫자 리터럴이면 상대 타입으로 재해석.
```python
def _literal_retype(sub_expr, iv, target_cls):
    if type(iv) is target_cls or not _is_numeric_literal(sub_expr):
        return None
    ...
    if lo < b_lo or hi > b_hi:
        return None            # 대상 타입 범위 밖 → 호출부에서 예외 (실제 Solidity 도 컴파일 에러)
    return target_cls(lo, hi, w)
```
호출부는 `expr.right` 를 먼저, 안 되면 `expr.left` 를 시도하고, 둘 다 실패하면
F13 의 Yul 재해석으로 넘어간다. 그것도 아니면 예외 — **⊤ 로 덮지 않는다.**

**F13 과의 차이(혼동 주의)**
| | F13 `_reinterpret_as` | F14 `_literal_retype` |
|---|---|---|
| 대상 | Yul 문맥의 **모든** 피연산자 | Solidity 숫자 **리터럴**만 |
| 근거 | Yul 엔 타입이 없다 (raw word) | 리터럴엔 타입이 없다 (유리수) |
| 손실 | 부호 비트 갈리면 **TOP** | **없음** (단일 상수, 범위 밖이면 예외) |

**적용 범위**: 비트 분기(`& | ^`)에만 넣었다. 산술(`+ - * / % **`)은 도메인 메서드를
직접 호출할 뿐 **타입 일치 검사가 없어** 현재 실패 사례가 없다. 실패하지 않는 경로를
건드리면 값이 조용히 바뀌므로 손대지 않았다(포팅 시에도 동일하게 할 것).

**효과**: `[ERR]` 198 → **197**, 비트 타입 불일치 **15 → 0**(전부 해소).
`PRBMathSD59x18.sol` 23 → 22, `PRBMathSD59x18.avg` 가 본문을 갖게 됨
(본문 있는 함수 384 → **385**, 빈 함수 30 → **29**). **증가한 파일 0개**, pkl 134 동일.

---

## F15. 인터페이스 body 의 event 선언이 null 컨텍스트로 새어나감 ★진단

**파일**: `Analyzer/ContractAnalyzer.py` — `analyze_context`, 인터페이스 분기
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `[ERR] L4 ctx=None: None` — **59건**(인터페이스 15개).
메시지가 `None` 이라 오래 "원인 불명"으로 남아 있었다.

**근본 원인**: 인터페이스 body 분기가 `function ` 이 아니면 `return` 으로 "스킵" 했는데,
그 **스킵이 호출자에게 전달되지 않는다**. `analyze_context` 는 앞에서 이미
`current_context_type`/`current_target_contract` 를 None 으로 초기화한 뒤 그냥 돌아올 뿐이다.
드라이버(`Dependencies/main.py`, `main.simulate_inputs`, `EngineSession._apply_code_record`)는
`ctx is None` 을 확인하지 않으므로 그대로 파싱·visit 을 계속하고,
`event` 선언은 문법적으로 멀쩡하니 파싱에 성공해 visitor 가 돈다:

```
visitEventDefinition → process_event_definition
    ccf = self.contract_cfgs[self.current_target_contract]   # ← None
    → KeyError: None      # str(KeyError(None)) == 'None'  ← 메시지가 'None' 인 이유
```

**수정**: contract/library 분기엔 이미 있는 `event` 처리를 인터페이스에도 맞춘다.

```python
if stripped_code.startswith('function '):
    self.current_context_type = "functionDefinition"
elif stripped_code.startswith('event '):
    self.current_context_type = "event"
else:
    return
self.current_target_contract = self.find_contract_context(start_line)
```

`else: return` 은 유지 — 전수 조사 결과 이 코퍼스에서 **도달 0건**이고(슬라이서가 여러 줄
함수 시그니처를 한 레코드로 합치므로 연속 줄은 오지 않는다), 유효한 Solidity 중 유일하게
올 수 있는 것은 custom error 다(아래 "아직 안 고친 것" 참조).

**효과**: `[ERR]` 197 → **138**. `None` 메시지 **59 → 0**. 증가한 파일 0개.

⚠ **정직하게**: 분석 결과는 달라지지 않는다. `ccf.events` 는 **아무도 읽지 않고**
(`process_emit_statement` 는 `pass`, emit 은 상태를 안 바꾼다), 본문 있는 함수 385 / 빈 함수 29 로
전후 동일하다. 이건 **59건의 가짜 경보를 없애 진짜 문제가 보이게 하는** 정리다.
다만 "스킵 의도가 호출자에게 전달되지 않는" 구조 자체가 결함이므로 고쳐두는 게 맞다.

---

## F16. `bytesN` constant 가 전부 거부 + 문자열 리터럴 정확 인코딩 ★코어

**파일**: `Analyzer/ContractAnalyzer.py` — `process_constant_variable`,
`Interpreter/Semantics/Evaluation.py` — `evaluate_literal_context` bytesN 분기
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `Type 'bytes32' cannot be declared as constant: 'GOVERNANCE'` — **24건**
(Roles 9 · AddressProviderKeys 9 · ERC1155Upgradeable 2 · Controller 2 · ERC165Upgradeable 1 · LockeERC20 1).

**근본 원인 (1) — 허용 목록 누락**: 게이트가
`et in ["string","bytes"] or et.startswith(("int","uint","bool")) or et == "address"` 인데
`"bytes"` 는 **동적** bytes 만 매치한다. `bytes1..bytes32` 는 Solidity 의 value type 이고
`Domain/BytesSet.py` 와 `evaluate_literal_context` 의 bytesN 분기가 **이미 있었다** — 게이트만 막고 있었다.
선언이 거부되면 `register_var` 까지 못 가므로 이 상수를 읽는 코드가 연쇄 실패한다.

**근본 원인 (2) — 리터럴에 선언 타입이 전달되지 않음**: 초기화식 실측 분포

| 형태 | 건수 | `context` | `expr_type` |
|---|---:|---|---|
| 문자열 `"governance"` | 19 | `LiteralExpContext` | `'string'` |
| 16진 `0x01ffc9a7` | 3 | `LiteralExpContext` | `'uint'` |
| `keccak256(...)` | 2 | `FunctionCallContext` | `None` |

`visitLiteralExp` 는 자기가 본 형태로만 찍는다. 그대로 두면 bytesN 분기를 못 타고
**원문 str** 이 상수에 박힌다(`'"governance"'`). F14 와 같은 원리 — 리터럴엔 타입이 없고
문맥(선언)이 타입을 준다.

⚠ **bytesN 에만** 덮어쓸 것. `uint256` 으로 덮으면 `evaluate_literal_context` 가 `'uint256'` 을
모른다(`Unsupported literal expr_type 'uint256'`). 실측 확인.
`keccak256` 은 손대지 않는다 — 이미 `BytesSet.top(32)` 로 올바르게 평가된다(Evaluation.py:1485).

**근본 원인 (3) — 문자열 리터럴 → bytesN 인코딩 부재**: bytesN 분기가 숫자/16진만 처리하고
따옴표 리터럴은 `BytesSet.top()` 으로 떨어뜨렸다.

**수정**

```python
# (1)(2) process_constant_variable
_is_fixed_bytes = et.startswith("bytes") and len(et) > 5 and et[5:].isdigit()
if _is_fixed_bytes and getattr(init_expr, "context", None) == "LiteralExpContext":
    init_expr.expr_type = et

# (3) evaluate_literal_context, bytesN 분기
if len(lit) >= 2 and lit[0] in ("'", '"') and lit[-1] == lit[0]:
    body = lit[1:-1]
    if "\\" in body:                                # 이스케이프 → 틀린 싱글턴이 된다
        raise ValueError(...)
    raw = body.encode("utf-8")
    if len(raw) > byte_size:                        # 실제 solc 도 거부
        raise ValueError(...)
    return BytesSet(values={int.from_bytes(raw + bytes(byte_size - len(raw)), "big")},
                    byte_size=byte_size)
raise ValueError(...)   # hex".." / unicode".." 는 미구현 — ⊤ 로 덮지 않는다
```

**★ 건전성 검증 (이게 이 수정의 핵심)**

상수의 구체 값 집합은 **싱글턴**이므로 값이 정확하면 `BytesSet({v})` 가 가장 좁은 건전한
추상화다. 틀리면 **불건전**(잘못된 definite 판정)이므로 인코딩을 실증했다.

- 규칙: UTF-8 바이트를 **상위 바이트부터** 배치, 하위를 0 으로 채움(left-aligned = 오른쪽 0 패딩).
- 근거: `Dataset/Web3Bugs/S3_2/contest_145_H_02/registry_ReverseRegistrar.sol:12` (실제 ENS 코드)
  ```solidity
  bytes32 constant lookup = 0x3031323334353637383961626364656600000000000000000000000000000000;
  mstore8(i, byte(and(addr, 0xf), lookup))
  ```
  Yul `byte(n,x)` 는 MSB 부터 n 번째를 뽑으므로 이 인덱싱(0→'0', 15→'f')이 성립하려면
  ASCII 가 상위 바이트에 있어야 한다. 리포 내 32바이트 hex 상수 65개 전수 스캔으로 확보.
- **대조 결과**: 엔진이 계산한 `bytes32("0123456789abcdef")` 가 ENS 상수와 **비트 단위 일치**.
- 대상 19개 문자열 전수 검사: 이스케이프 0 · non-ASCII 0 · 길이 초과 0 (전부 pragma 0.8.9).
- 미모델링 형태(이스케이프 / `hex""` / `unicode""` / 길이 초과)는 **raise**. ⊤ 로 덮으면
  "정밀도만 잃는" 게 아니라 잘못된 경로를 믿게 된다. 현재 그 케이스들은 이미 오류라 현상 유지다.

**왜 ⊤ 로 두면 안 되나**: `BytesSet.equals` 는 `is_top` 이면 **무조건** `BoolInterval(0,1)`
(Unknown → Warning)을 낸다. 이 상수들의 실제 쓰임이
`require(hasRole(ROLE_ADMIN, msg.sender))` · `onlyRoles2(Roles.GOVERNANCE, ...)` ·
`currentUInts256[_KEEPER_REQUIRED_STAKED_BKD]`(**mapping key**) 이므로,
⊤ 이면 verdict 가 전부 Warning 이 되고 mapping 슬롯도 특정할 수 없다.
즉 이건 오류 건수가 아니라 **verdict 품질** 문제다.

**효과**: `[ERR]` 138 → **112**. `cannot be declared as constant` **24 → 0**.
AddressProviderKeys 9→0 · Roles 9→0 · ERC165Upgradeable 1→0 · LockeERC20 1→0 ·
ERC1155Upgradeable 6→3 · Controller 31→28. **증가한 파일 0개**, pkl 134, `[ANTLR]` 192 동일,
빈 함수 29 → 28. `temp/` 회귀 7/10/7 통과.

⚠ **범위**: `constant` 에 한정. 일반 state variable(`bytes32 nonconst = "plain"`)은 여전히
원문 str 을 갖는다 — **별건의 잠재 문제**로 남겨둔다(현재 오류 없이 동작 중이라
건드리면 값이 조용히 바뀐다).

⚠ **작업 중 사고 2건**
- 패치 문자열의 `\x00` escape 가 전송 중 뭉개져 소스에 **실제 NUL 바이트**가 들어갔다
  (`ast.parse` → `source code string cannot contain null bytes`). 생성 코드에는 백슬래시
  escape 를 쓰지 말 것 — `raw.ljust(n, b"\x00")` 대신 `raw + bytes(n - len(raw))` 처럼
  escape 없는 표현을 쓴다.
- 1차 시도를 되돌릴 때 `s.index("            et = ...")`(12칸)가 `process_state_variable` 안의
  **16칸 들여쓰기 줄의 부분문자열**에 먼저 매치돼 **84줄이 통째로 삭제**됐다
  (`process_constant_variable` 정의 자체가 소실). 앵커는 반드시 **줄 시작 포함 + 유일성 검증**으로
  잡을 것. 복구는 `git diff HEAD` 로 손상 훅(`@@ -814,90 +834,6 @@`)을 특정하고
  HEAD 해당 구간을 재삽입해서 했다.

---

## F17. 상속 체인을 1단계만 탐색 + intent 문법이 `ceil`/`floor` 를 예약어로 만듦 ★

**파일**: `Analyzer/ContractAnalyzer.py`(`_find_in_ancestors` 신규, `process_modifier_invocation`),
`Dependencies/main.py`(`_load_parent_pkls`),
소스 `Dependencies/libraries/PRBMath{SD59x18,UD60x18}.sol`
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `No active function CFG` 계열 **40건**(Controller 20 · PRBMathSD59x18 18 · 기타 2).
원인이 **두 갈래**였다.

⚠ 먼저 확인한 것: **레코드 슬라이싱은 전부 의도대로였다.** 여러 줄 함수 시그니처
(Controller L18~23), 여러 줄 modifier 시그니처(AuthorizationBase L19~23), 여러 줄
`require(...)`(L24~27) 모두 한 레코드로 정확히 병합된다. 변환 단계 문제가 아니다.

---

### (A) 상속 체인 전이적 탐색 — Controller 20건

체인: `contract Controller is IController, Authorization, Preparable`
→ `contract Authorization is AuthorizationBase` → `modifier onlyGovernance` 는
**AuthorizationBase** 에 정의돼 있다.

두 군데가 얕았다:
- `ContractAnalyzer.process_modifier_invocation` 이 `parent_contracts` 를 **1단계만** 순회.
  `ContractCFG.get_function_cfg` 도 `self.functions` 만 보고 부모 체인을 타지 않는다.
- `Dependencies/main._load_parent_pkls` 는 docstring 에 **"(재귀)"** 라 써 있지만
  실제로는 소스의 `X is A, B, C` 를 한 번 파싱해 직계 부모만 로드했다.
  실측: Controller 분석 시 `contract_cfgs=['Authorization','IController','Preparable']`
  — **AuthorizationBase 없음**.

modifier 해석 실패는 **함수 정의 자체를 예외로 중단**시키므로, 그 함수 본문 전체가
"No active FunctionCFG" 로 연쇄 실패한다. `Controller` 의 등록 함수는 **0개**였다.

**수정**
```python
def _find_in_ancestors(self, ccf, name):
    """상속 체인 BFS. 조상 CFG 는 ccf.parent_cfgs(pkl 에 객체째 직렬화됨) +
       self.contract_cfgs 두 곳에서 얻는다."""
```
`con_Authorization.pkl` 이 `parent_cfgs['AuthorizationBase']` 를 **객체로 품고 있어서**
조회만 고쳐도 도달한다. `_load_parent_pkls` 재귀화는 `contract_cfgs` 를 직접 보는
다른 경로(타입 해석 등)를 위한 **독립적** 보완이다.

⚠ 탐색 순서는 **선언 순서**다. Solidity 의 C3 선형화는 뒤쪽 부모가 더 파생이라
(`is A, B` → B 우선) 엄밀히는 역순이어야 한다. 이 코퍼스엔 조상 간 이름 충돌이 없어
영향이 없지만, 다이아몬드 상속에선 잘못된 정의를 고를 수 있다 — **별건**.

**부수 확정**: `onlyGovernance` 의 CFG 는 **정상**이다 — nodes=7 +
`cond_expr: _roleManager().hasRole(Roles.GOVERNANCE, msg.sender)`.
`require` 가 조건 노드로 모델링되므로 `statements=0` 이 맞다.
→ **`check_pkl.py` 가 modifier 를 손상으로 오분류**하는 것이 확정됐다(판정 기준 교정 필요).

---

### (B) intent 문법이 `ceil`/`floor` 를 예약어로 만듦 — PRBMath 19건

```
[ANTLR] 1:9 no viable alternative at input 'functionceil'
L31  function ceil(int256 x) internal pure returns (int256 result) {
```

`Parser/Solidity.g4:323-324`
```antlr
| intentValue relOp 'ceil'  '(' intentValue ',' numberLiteral ')'   # Ceil
| intentValue relOp 'floor' '(' intentValue ',' numberLiteral ')'   # Floor
```

ANTLR 은 파서 규칙 안의 인라인 문자열 리터럴에 **암묵 토큰**을 만들고, 그 토큰은
`Identifier` 보다 **우선**한다. 결과적으로 `ceil`/`floor` 는 **모든 Solidity 소스에서
식별자로 쓸 수 없게** 됐다. IntentChecker 의 술어 문법이 일반 Solidity 파싱을 깨뜨린 것이다.

충돌 규모(리포 전체 `.sol` **3,984개** 스캔):

| 예약어 | 충돌 | 내역 |
|---|---:|---|
| `floor` | 24 | 멤버접근 11, 호출 6, function 이름 5, 변수 선언 2 |
| `ceil` | 9 | function 이름 4, 호출 4, 변수 선언 1 |
| `array` | 1 | 변수 선언 1 |
| 나머지 17개(`PercentOf` `changed` `returnExpression` `arg` `Before` `After` `Entry` `Exit` `Current` `Assign` `Unchanged` …) | **0** | — |

**채택한 해법 — 소스 rename (사용자 결정)**
`ceil` → `_ceil`, `floor` → `_floor`. **정의 4줄, 2파일**뿐이다:
`PRBMathSD59x18.sol:31,107` · `PRBMathUD60x18.sol:20,58`.
내부/외부 호출자 0곳, RQ1 케이스 JSON 영향 0건(`web3bugs_79_H_02.json` 132 레코드 중 사용 0).
`evaluation/RQ1/.../dependencies/` 사본은 **건드리지 않았다** — `Dependencies/main.py` 는
`Dependencies/{libraries,interfaces,contracts}/*.sol` 만 스캔하고, 루트 `main.load_dependencies()`
는 그 디렉토리를 `interface\s+(\w+)` **정규식 이름 수집에만** 쓴다(파싱하지 않음).

⚠ **문법은 고치지 않기로 했다(사용자 결정)** — intent 문법은 IntentChecker 논문에 실린
것이므로 변경하지 않는다. 즉 **도구 자체의 한계로 남는다**: 사용자가 에디터에
`function floor(...)` 를 입력하면 파싱이 깨진다. `from`(F9) 과는 성격이 다르다 —
`from` 은 Solidity 자체 예약어라 원본이 잘못이었지만, `ceil`/`floor` 는 **유효한 Solidity** 이고
우리 문법이 잘못이다. 소스 rename 은 증상 대응이지 근본 수정이 아니다.
(soft keyword 화 — `CEIL : 'ceil'; identifier : Identifier | CEIL | ...` — 는 언제든 가능하지만
**파서 재생성**이 필요하고 intent 문법 변경을 수반한다.)

---

**효과**: `[ERR]` 112 → **76**. `No active function CFG` 계열 **40 → 1**.
`[ANTLR]` 192 → **188**. **증가한 파일 0개**, pkl 134.
Controller 28→11 · PRBMathSD59x18 22→4 · PRBMathUD60x18 5→4.
본문 있는 함수 385 → **391**.

함수 등록 복구:
```
con_112_Controller.pkl   : 0개 → 8개 함수 (본문 있음 6)
lib_PRBMathSD59x18.pkl   : _ceil/_floor 포함 22개 (본문 있음 21)
lib_PRBMathUD60x18.pkl   : _ceil/_floor 포함 21개 (본문 있음 20)
```
`temp/` 회귀 7/10/7 통과.

⚠ **빈 함수 28 → 30 (+2)**: 회귀가 아니라 **함수가 6개 더 생겨서**다(385→391).
새로 등록된 Controller modifier 들이 `check_pkl.py` 의 (잘못된) 판정에 걸린 것이다.

⚠ **메시지 종류가 바뀐 것**: Controller 의 `may not be declared` 가 3→6 등으로 늘었는데,
전엔 함수 본문이 **아예 분석되지 않았기 때문**에 안 보이던 진짜 선언 문제가 이제 드러난 것이다.
파일 총계는 28→11 로 줄었고 증가한 파일은 0개다.

---

## F18. Phase 1(interfaces) 위상정렬 누락 + file-level enum 미수집 ★

**파일**: `Dependencies/main.py`(Phase 1, `scan_file_level_enums` 신규, Phase 0/주입),
`Analyzer/EnhancedSolidityVisitor.py`(타입 해석 체인)
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `Type 'X' is not defined as struct, enum, or type alias` **17건**.
분해해보니 **네 갈래**였고, 그중 둘이 엔진 결함이다.

| 갈래 | 건수 | 성격 |
|---|---:|---|
| (A) 의존성 자산 누락 | 9 | `.sol` 이 `Dependencies/` 에 없음 — **엔진 무관** |
| (B) Phase 1 위상정렬 누락 | 2 | **F7 미완성** |
| (C) file-level enum 미수집 | 5 | 수집·주입·해석 3단 모두 없음 |
| (D) `Roles` 동명이인 | 1 | 레지스트리 키 충돌 — **별건** |

---

### (B) Phase 1 에만 위상정렬이 빠져 있었다 — 2건

```python
# 수정 전: Dependencies/main.py
ifc_files = sorted(IFC_DIR.rglob("*.sol"))       # ← 알파벳순
# Phase 2(libraries)/Phase 3(contracts) 는 _topo_order(...)   ← F7 이 여기만 고침
```

`IPooledCreditLine is IPooledCreditLineDeclarations is IPooledCreditLineEnums` 이고
`enum PooledCreditLineStatus` 는 맨 위 조상에 있다. 알파벳순은
`IPooledCreditLine` < `…Declarations` < `…Enums` — **의존 방향의 정확히 반대**다.

조상이 나중에 분석되면 부모 분석 시점에 조상 pkl 이 없어 `parent_cfgs` 가 **빈 채로**
저장된다. 실측(수정 전):
```
ifc_IPooledCreditLineEnums.pkl        enumDefs=['CancellationStatus','PooledCreditLineStatus']
ifc_IPooledCreditLineDeclarations.pkl parent_contracts=['IPooledCreditLineEnums'] parent_cfgs=[]
ifc_IPooledCreditLine.pkl             parent_contracts=['IPooledCreditLineDeclarations'] parent_cfgs=[]
```
`EnhancedSolidityVisitor._find_in_chain` 은 `parent_cfgs` 를 재귀로 도는 **올바른 코드가
이미 있었다** — 돌 것이 비어서 무용지물이었을 뿐이다.

**수정**: Phase 1 도 `_topo_order(ifc_files, "interfaces")`.
IFC_DIR 73개 전수 확인 결과 주 선언이 **모두 interface** 라 모드 불일치 위험이 없다.
정렬 결과: `IPooledCreditLineEnums`(40) → `Declarations`(69) → `IPooledCreditLine`(72).

**효과**: 부모가 있는 interface pkl **14개 중 0개 → 12개**의 `parent_cfgs` 가 채워졌다
(나머지 2개는 부모 `.sol` 자체가 없는 (A) 케이스).

---

### (C) file-level enum — 5건

`enum AssetClass {...}`(IMochiProfile.sol), `enum Status {...}`(IMochiVault.sol) 은
**파일 최상위 enum**(Solidity 0.8+ 허용)이다. 실측 `ifc_IMochiProfile.pkl`·
`ifc_IMochiVault.pkl` 의 `enumDefs=[]`.

`SolidityAnalyzer.file_level_enums` **필드도 있고 소비처도 있는데**
(`Evaluation.py:1090`, `Refine.py:190`) 세 단계가 모두 비어 있었다:

1. **수집 없음** — `Dependencies/main.py` Phase 0 은 `scan_type_aliases` /
   `scan_interface_names` / `scan_file_level_structs` 만 한다.
   루트 `main.py:98-102` 와 `engine_api.py:793` 은 regex 로 수집한다 — **pkl 생성 경로만 누락**.
2. **주입 없음** — `analyze_file` 의 주입부에도 빠져 있었다.
3. **타입 해석 분기 없음** — `EnhancedSolidityVisitor` 의 체인에 `file_level_structs` 분기는
   있는데(L699) `file_level_enums` 분기가 없다. `Evaluation.py:1090` 은 *식별자 평가*
   (`AssetClass.Stable`)에만 쓰고 *타입 선언*(`returns (AssetClass)`)엔 쓰지 않는다.

**수정**: `scan_file_level_enums`(brace-depth 0 스캔, 여러 줄 본문 지원) + 주입 + 해석 분기.

⚠ **typeCategory 만 세우면 안 된다** — 하류가 `ccf.enumDefs.get(enumTypeName)` 를 하고
없으면 `undefined enum` 으로 터진다(ContractAnalyzer:1277/1324/1427/1520).
그래서 해석 분기에서 `EnumDefinition` 을 만들어 `contract_cfg.enumDefs` 에 등록한다.
(검증: regen20 에서 `undefined enum` **0건** — 에러를 옮기기만 한 게 아니다.)

⚠ 기존 `scan_*` 3종은 `d.glob("*.sol")`(**비재귀**)라 `contracts/112/` 같은 하위 디렉토리를
놓친다. 새 함수는 `rglob` 을 쓴다 — 기존 3종의 비재귀는 **별건**으로 남긴다.

---

**효과**: `[ERR]` 76 → **69**. `not defined as struct/enum/alias` **17 → 10**
(해소 7 = B 2 + C 5, 남은 10 = A 9 + D 1 — 예상과 정확히 일치).
`undefined enum` 0건. **증가한 파일 0개**, pkl 134, `[ANTLR]` 188 동일, 본문 있는 함수 391 동일.
IMochiProfile 4→2 · IMochiVault 5→2 · IPooledCreditLine 1→0 · IPooledCreditLineDeclarations 1→0.
`temp/` 회귀 7/10/7 통과.

---

## F19. 타입 참조 pkl 로드 + 위상정렬에 타입 참조 반영 (도구)

**파일**: `Dependencies/main.py` — `_type_refs_in`·`_load_pkl_for`·`_load_type_ref_pkls` 신규,
`_topo_order` 의존 집합
**Guardian 상태**: 미확인.

**증상**: `contract RoleAware { Roles public immutable roles; ... }` 처럼 **상속하지 않고
타입으로만 쓰는** 컨트랙트가 해석되지 않음 → `Type 'Roles' is not defined...` 1건 +
`roles` 를 쓰는 4줄 연쇄 = 5건, `con_RoleAware.pkl` 의 함수 4개가 빈 채 저장.

**원인 두 겹**
- `_refs_in` 은 상속·라이브러리호출·using 만 본다 → 타입 참조가 의존으로 안 잡혀
  `RoleAware.sol`(13) 이 `contracts/Roles.sol`(14) 보다 **먼저** 분석된다.
- 분석된 contract CFG 는 다음 파일 분석에 주입되지 않는다(`_global_library_cfgs` 는
  library 만, `_load_parent_pkls` 는 `is` 절만). 인터페이스엔 `ca.interface_names`
  (전역 regex 수집)가 있지만 **contract 엔 그런 레지스트리가 없다.**

**수정**
```python
_typedecl_re / _typeret_re → _type_refs_in(src)   # 타입 위치의 대문자 이름만
_load_pkl_for(pname, ca)    # 후보가 **정확히 1개일 때만** 로드
_load_type_ref_pkls(source, ca)   # analyze_file 에서 _load_parent_pkls 뒤에 호출
refs[f] = _refs_in(src) | _type_refs_in(src)      # _topo_order 안에서만 합집합
```
- 후보 1개 제한: `Controller` 처럼 `con_112_Controller.pkl`/`con_45_Controller.pkl` 이
  공존하는 이름은 어느 프로젝트 것인지 알 수 없으므로 **조용히 아무거나 집지 않는다**.
- `_refs_in` 자체는 건드리지 않는다. 순환 위험을 3개 Phase 전수 검증함
  (interfaces 73 / libraries 32 / contracts 30 — 순환 없음, RoleAware 13→23).

⚠ **가드 함정**: `analyze_file` 이 `ca.contract_cfgs.update(_global_library_cfgs)` 로
라이브러리를 contract_cfgs 에도 넣는다(호환성). 그래서 `if pname in ca.contract_cfgs: continue`
로 막으면 **동명 라이브러리가 진짜 contract pkl 의 로드를 가로막는다.**
"이미 있는 것이 진짜 ContractCFG 일 때만" 건너뛰도록 해야 한다.

**효과**: 단독으로는 **0건**. 진짜 원인은 아래 F20 이었고, F20 을 고친 뒤에 이 배관이
비로소 동작한다. (F19 만으로 regen 이 변하지 않은 것이 F20 을 찾게 된 단서였다.)

---

## F20. `make_contract_cfg` 가 동명의 LibraryCFG 를 contract CFG 로 재사용 ★

**파일**: `Analyzer/StaticCFGFactory.py` — `make_contract_cfg`
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `RoleAware.sol` 5건. F19 의 배관을 다 깔아도 변화가 없었다.

**근본 원인**
```python
def make_contract_cfg(an, contract_name) -> ContractCFG:
    if contract_name in an.contract_cfgs:
        return an.contract_cfgs[contract_name]      # ← 종류를 안 따지고 재사용
```
`analyze_file` 의 호환성 주입(`contract_cfgs.update(_global_library_cfgs)`)이
**다른 프로젝트의 동명 라이브러리**를 먼저 넣어두면, contract 의 CFG 를 만들 때
그 **LibraryCFG 를 그대로 돌려준다.** 그 뒤 contract 의 함수들이 LibraryCFG 안에 쌓이고
pkl 로 저장된다. 실측:
```
contracts/112/Roles.sol  = library Roles  (bytes32 상수, Backd)      순번 0
contracts/Roles.sol      = contract Roles (giveRole/getRole, MarginSwap) 순번 13
→ con_Roles.pkl : 타입 LibraryCFG / 내용은 contract 함수 4개
```
`ContractCFG` 와 `LibraryCFG` 는 **상속 관계 없는 형제 클래스**라, 이 잘못된 객체는
`isinstance(x, ContractCFG)` 검사를 모두 통과하지 못한다. 타입 해석 분기
`type_name in contract_cfgs and type_name not in library_cfgs` 가 계속 실패한 진짜 이유다.

**수정**
```python
_existing = an.contract_cfgs.get(contract_name)
if isinstance(_existing, ContractCFG):
    return _existing
cfg = ContractCFG(contract_name)   # LibraryCFG 등 다른 종류면 새로 만든다
```

**효과**: `[ERR]` 54 → **49**. `RoleAware.sol` **5 → 0**.
`con_Roles.pkl` 타입 LibraryCFG → **ContractCFG**.
본문 있는 함수 392 → **396**, 빈 함수 29 → **25**. 증가한 파일 0개.

⚠ **rename 은 불필요하다.** 처음엔 `Roles` 동명이인을 소스 rename 으로 풀려 했으나,
실측 결과 이름 충돌이 아니라 **객체 종류 검사 누락**이 원인이었다. 평가 자산을
건드리지 않고 해결된다.

---

## F21. 배열 원소 생성에서 중첩배열/mapping/enum 누락 + 리터럴 인덱스 우회 ★코어

**파일**: `Interpreter/Semantics/Evaluation.py` — 배열 인덱싱 3곳
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `CommonLibrary.splitAmounts` **14건**이 한 줄에서 연쇄.
```solidity
function splitAmounts(uint256[] memory amounts, uint256[][] memory weights) ... {
    require(weights[i].length == n, "NV");   ← member 'length' is not a recognised global-member
```

**근본 원인**: 배열 원소를 만드는 **두 분기가 base 타입을 손으로 나열**하는데
`uint/int/bool/address/struct` 만 있고 **중첩배열·mapping·enum 이 빠져 있다.**
`uint256[][]` 의 `arrayBaseType.typeCategory == "array"` 라 마지막 `else` 로 떨어져
**문자열** `"symbolic_w[<unk>]"` 를 돌려주고, 거기에 `.length` 를 걸면 터진다.

세 번째 지점은 별개의 우회다 — **리터럴 인덱스 경로가 `get_or_create_element` 를 안 쓴다.**
길이 미상 파라미터(`uint256[][] memory w`)의 `w[0]` 이 auto-push 없이
`IndexError: Index 0 out of range` 로 터졌다.

**이미 올바른 구현이 있었다**: `Domain/Variable.py` 의
`_create_new_array_element` (중첩 ArrayVariable / MappingVariable / EnumVariable 생성) 와
그 래퍼 `_create_element_virtual`, 그리고 `get_or_create_element`(동적이면 auto-push).
`DebugInitializer` 는 이미 `isDynamicArray` 분기 + `get_or_create_element` 패턴을 쓰고,
`Evaluation.py:510` 도 `_create_element_virtual` 을 쓴다. **세 지점만 누락**돼 있었다.

접근 경로 일관성 (수정 전):
| 파일 | `get_or_create_element` | `.elements[idx]` 직접 |
|---|---:|---:|
| `DebugInitializer.py` | 3 | 3 — 항상 `isDynamicArray` 분기와 짝 ✅ |
| `Evaluation.py` | 1 | 6 |
| `Update.py` | 4 | 3 |

**수정**
```python
# (a) 확정 인덱스·요소 없음 / (b) 불확정 인덱스·빈 배열
elif base_t.typeCategory in ("array", "mapping", "enum"):
    return callerObject._create_element_virtual(idx)

# (c) 리터럴 인덱스
if callerObject.typeInfo.isDynamicArray:
    return callerObject.get_or_create_element(idx)   # 동적 → 확장
...범위 검사...                                       # 정적 → 그대로
```

**건전성**: 생성되는 원소 값은 `_create_default_value` 의 **⊤**
(address→`AddressSet.top()`, bool→`[0,1]`, int/uint→전범위). 길이 미상 배열의 원소를
⊤ 로 보는 것은 over-approximation 이라 건전하고, **문자열을 돌려주던 기존 동작보다
엄격하게 정확하다**(문자열은 추상값이 아니라 이후 연산이 전부 깨진다).
정적 배열의 범위 검사는 유지한다(실제 Solidity 도 컴파일 에러).
이미 요소가 있는 정상 경로(`elements[idx]` 반환)는 건드리지 않는다.

**효과**: `[ERR]` 49 → **35**. `CommonLibrary.sol` **14 → 0**. 증가한 파일 0개.
`temp/` 회귀 7/10/7 통과.

---

## 부수: 평가 자산 보강 (도구 아님)

**누락 인터페이스 9종 복사** — `Web3Bugs/contracts/` 에서 `Dependencies/interfaces/` 로.
`IVMochi` `IUSDM` `IFeePool` `ILiquidator`(42 mochi-core) · `IInflationManager`(112 backd) ·
`IVaultRegistry` `IVaultFactory`(58 mellow-vaults) · `IERC721` `IERC165`(OZ).
`IERC721` 은 `from` 파라미터 4곳을 `_from` 으로(F9 규칙).
→ `Type 'X' is not defined as struct/enum/alias` **17 → 1**, pkl 134 → 143.

⚠ **블록 주석이 슬라이서를 깨뜨린다**: OZ 파일의 NatSpec(`/** ... */`)이
`slice_solidity` 에서 **뒤따르는 선언과 한 레코드로 합쳐져**
`analyze_context` 의 `startswith('interface'/'event'/...)` 분류가 전부 빗나간다
(IERC721 = `[ERR]` 12 + `[ANTLR]` 25). 문법엔 `COMMENT : '/*' .*? '*/' -> channel(HIDDEN)`
이 있으므로 **파서가 아니라 슬라이서/분류기의 문제**다.
기존 Dependencies 144개 중 블록 주석이 있던 파일은 **복사한 3개뿐**이라 여태 안 드러났다.
지금은 그 3개에서 제거해 해결했으나, **사용자가 에디터에 NatSpec 을 치면 같은 문제가 난다.**

**전처리 툴킷 확보**: `SolidityGuardian/temp/` 에서 `IntentKeeper/tools/` 로 9개
(`preprocess_contraction.py` 8단계 파이프라인, `rename_reserved_identifiers.py`,
`generate_case_jsons.py`, `PROCESS_GUIDE{,_v2}.md` 등).
드라이런 결과 **기존 코퍼스는 이미 전처리를 거친 상태**였고(예약어 0 · bare scope 0),
잔여 2단계(enum 멤버 합치기 4파일 · single-line if 확장 1파일)를 적용했으나 오류 변화는 없었다.
`PROCESS_GUIDE_v2.md` 에 **"Dependencies/main.py pkl 생성 시 분석 순서"** 이슈가 이미
문서화돼 있었다 — F7/F18/F19 가 바로 그 항목이다.

---

## F22. 지역 struct/enum 선언이 정의를 붙이지 않음 (regen 기여 0, 그러나 실재하는 결함)

**파일**: `Analyzer/ContractAnalyzer.py` — `_create_variable_object` 2-B/2-C (+ 2-D 중복 제거)
**Guardian 상태**: 미확인.

**원인**: 같은 함수 안에서 2-A(배열)·2-D(매핑)는 `get_full_struct_enum_defs()` 로
정의를 넘겨 `_make_value` 가 초기화하는데, **2-B(struct)·2-C(enum) 만 아무것도 안 했다.**
```python
elif type_obj.typeCategory == "struct":
    v = StructVariable(identifier=var_name,
                       struct_type=type_obj.structTypeName, scope="local")   # 끝. 멤버 없음
```

**수정**: 2-B/2-C 도 `get_full_struct_enum_defs()` 로 정의를 얻어
`initialize_struct` / enum 멤버 테이블을 채운다. qualified(`Ticks.Tick`) 로 못 찾으면
bare(`Tick`) 로 한 번 더 조회한다(실측: `structDefs` 에 두 이름이 **둘 다** 있는 경우가 있다).
2-D 의 인라인 중복(정의 수집 로직)도 헬퍼로 교체.

⚠ **regen 기여 0건** (35 → 35, 증가·감소 모두 없음).
목표였던 `Ticks.sol` 은 `process_variable_declaration:1341` 이
```python
if isinstance(resolved, (StructVariable, ArrayVariable, MappingVariable)):
    v = VariableEnv.deep_clone_variable(resolved, var_name)   # 방금 만든 v 를 버림
```
로 **초기화식이 준 객체를 쓰기 때문**이었다(진짜 원인은 F23).
그래도 **초기화식이 없는** 지역 struct 선언(`Tick memory t;`)에는 이 수정이 필요하므로 남긴다.
"효과 0" 이라는 사실 자체가 F23 을 찾게 한 단서였다.

---

## F23. index-access 의 **멤버 접근 키**가 매핑 조회를 건너뜀 ★코어

**파일**: `Interpreter/Semantics/Evaluation.py` — `evaluate_member_access_context` 반환부 3곳
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `Member 'X' not in struct 'X'` **6건** (Ticks 4 + AddressProviderMeta 2).
```solidity
Ticks.Tick storage current  = ticks[lower];                 // OK
Ticks.Tick storage previous = ticks[current.previousTick];  // ← 여기서 깨짐
previous.nextTick = current.nextTick;   // Member 'nextTick' not in struct 'previous'
```

**근본 원인**: `evaluate_index_access_context` 는 인덱스 접근을 **인덱스 식 평가에 위임**한다.
```python
base_val = self.evaluate_expression(expr.base, variables, None, "IndexAccessContext")  # 매핑
return self.evaluate_expression(expr.index, variables, base_val, "IndexAccessContext")
```
`callerObject=매핑` 을 넘겨 **인덱스 식 핸들러가 매핑 조회까지 마치도록** 설계돼 있다.
- 리터럴 키 → `evaluate_literal_context` 의 `isinstance(callerObject, MappingVariable)` 분기 ✅
- 식별자 키 → `evaluate_identifier_context` 의 매핑 분기 ✅
- **멤버 접근 키 → `evaluate_member_access_context` … 처리 없음** ❌

실행 추적으로 확인: `ticks[current.previousTick]` 평가 중 `get_or_create` 도
`_create_value_virtual` 도 **한 번도 호출되지 않고**, `evaluate_member_access_context` 가
`nested.value`(int24 구간)만 돌려주고 끝난다. 매핑은 버려진다.
그 값이 `resolved` 가 되어 `process_variable_declaration` 의
`isinstance(resolved, StructVariable)` 분기를 못 타므로 빈 struct 가 그대로 남는다.

**수정**: 이미 20곳 이상에서 쓰이는 헬퍼를 반환부 3곳에 붙인다.
```python
if isinstance(nested, EnumVariable):
    return self._mapping_lookup_if_needed(nested.value, callerObject)
if (isinstance(nested, Variables) and not isinstance(nested, (ArrayVariable, StructVariable, MappingVariable))):
    return self._mapping_lookup_if_needed(nested.value, callerObject)
return self._mapping_lookup_if_needed(nested, callerObject)
```
`_mapping_lookup_if_needed` 는
- `callerObject` 가 MappingVariable 이 **아니면 그대로 통과** → 기존 경로 무영향
- 맞으면 `struct_defs`/`enum_defs` 를 `get_full_struct_enum_defs()` 로 보충하고
  키(싱글턴이면 값, 아니면 심볼릭)로 `get_or_create` → `_make_value` 가 **정의가 붙은 struct** 생성

**효과**: `[ERR]` 35 → **31**. `Member not in struct` **6 → 2**.
`Ticks.sol` 7→3. ⚠ `AddressProviderMeta` 2건은 **그대로 남았다**
(첫 보고에서 상위 N개만 출력하는 집계에 가려 `0` 으로 잘못 적었던 것을 정정). 증가한 파일 0개, `[ANTLR]` 187 동일,
본문 있는 함수 396 동일. `temp/` 회귀 7/10/7 통과.
검증: `previous` 가 `structTypeName='Tick'`, `members=['previousTick','nextTick','liquidity']` 로 정상화.

★ **F16·F21·F22 와 같은 패턴이 네 번째다** — 올바른 구현이 이미 도메인/헬퍼에 있는데
특정 경로에서만 그것을 쓰지 않는다. 남은 오류를 볼 때 **"이 경로가 기존 헬퍼를 쓰고 있나"**
를 먼저 확인하는 것이 빠르다.

⚠ 비싱글턴 키는 `_mapping_lookup_if_needed` 의 기존 규약대로 `func_result_{id(result)}`
심볼릭 키를 쓴다(id 기반이라 재현성이 없다). 기존 20여 곳과 동일한 동작이므로 그대로 뒀으나,
**키 명명 규약은 별도로 볼 가치가 있다**.

---

## F24. 존재하지 않는 컨텍스트 문자열 `IndexAccessExpContext` ★코어 · 조용한 오분석

**파일**: `Interpreter/Semantics/Update.py`(1곳), `Interpreter/Semantics/Refine.py`(2곳)
**Guardian 상태**: 미확인(같은 코드로 보임 — 포팅 전 확인).

**증상**: `Cannot resolve mapping key from expression` 3건(Ticks).
같은 모양인데 어떤 줄은 되고 어떤 줄은 안 됐다 —
`ticks[lower].liquidity = ...`(L66) OK / `ticks[upper].liquidity = ...`(L85) 실패.

**근본 원인**: 문법 라벨은 `# IndexAccess`(Solidity.g4:596) 이므로 생성 클래스는
**`IndexAccessContext` 하나뿐**이다. `IndexAccessExpContext` 는 어디에도 없다.
(비슷한 라벨이 더 있어 혼동하기 쉽다: `# IntentIndexAccess`(L385, intent 절),
 `# IReturnIndexAccess`(L296, @IReturn). visitor 도 셋을 구분해 문자열을 만든다.)

따라서 `Update.py:327` 의 키 추출 블록이 **한 번도 실행된 적이 없다**:
```python
if getattr(base_exp, "context", "") == "IndexAccessExpContext":   # 항상 False
    ...  # 식별자/리터럴/멤버접근 키 추출 — 전부 죽은 코드
```
매핑 쓰기는 오직 그 아래 폴백으로만 동작하고 있었다:
```python
# ② 매핑에 엔트리 하나뿐이면 그걸 그대로 사용
if key is None and len(base_obj.mapping) == 1:
    key, _ = next(iter(base_obj.mapping.items()))
```
L66 은 그 시점에 엔트리가 1개라 **우연히** 맞았고, L85 는 엔트리가 늘어 실패한 것이다.

⚠ **오류 3건보다 이쪽이 훨씬 위험하다**: 폴백이 성공할 때도 **키를 검증하지 않고
있는 유일한 엔트리를 집는다.** `ticks[A]` 에 쓰는 코드가 실제로는 `ticks[B]` 를
**조용히** 수정할 수 있었다.

`Refine.py` 의 두 곳도 같은 오타이고, 그건 "심볼릭 인덱스면 refine 을 건너뛴다" 는
**건전성 가드**였다(코드 주석은 `IndexAccessContext` 라고 올바르게 적혀 있다).

**수정**: 세 곳의 `"IndexAccessExpContext"` → `"IndexAccessContext"`.

**효과**: `[ERR]` 31 → **28**. `Cannot resolve mapping key` **3 → 0**, `Ticks.sol` **3 → 0**
(이 파일은 이제 오류 0). 증가한 파일 0개, `[ANTLR]` 187 동일, 회귀 7/10/7 통과.

⚠ **남은 과제**: 폴백 ②(`엔트리 하나면 그걸 사용`)는 오타를 고친 지금도 남아 있다.
CLAUDE.md 의 fail-loud 방침에 어긋나므로 **도달 빈도를 측정한 뒤 제거를 검토**해야 한다.
또 intent 쪽 인덱스 접근(`IntentIndexAccess`)은 `Update`/`Refine` 의 이 블록에 여전히
걸리지 않는다 — `DebugInitializer` 는 둘을 함께 처리하므로, 의도된 분리인지 확인이 필요하다.

---

## F25. 전처리에 '빈 블록 `{ }` 확장' 단계가 없었다 (도구, 부분 해소)

**파일**: `tools/preprocess_contraction.py`(`_expand_empty_block` 신규) + 소스 8곳
**Guardian 상태**: ❗원본(`SolidityGuardian/temp/preprocess_contraction.py`)에도 없다.

**증상**: `Unsupported expression context in interactiveExpressionStatement` 중 3건 —
OZ upgradeable 관용구인 storage gap 이 **함수 바로 뒤**에 올 때.
```solidity
function _beforeTokenTransfer(...) internal virtual { }   // ← 한 줄 빈 본문
...
uint256[44] private __gap;                                 // ctx=simpleStatement 로 오분류
```

**근본 원인**: `find_function_context` 는 위로 거슬러 올라가며
"닫힌 만큼 상쇄하고 남은 첫 `{`" 를 찾는데, **한 줄에 `{` 와 `}` 가 같이 있으면**
`open > 0` 인 순간 곧바로 `break` 하고 같은 줄의 `close` 를 무시한다.
→ **이미 닫힌 함수**를 "열려 있다" 고 착각하고, 뒤따르는 상태변수를 함수 내부 문장으로 분류한다.
(실측: 7개 대상 중 틀리는 3건이 정확히 오류 3건과 일치. 나머지 4건은 `{`/`}` 가 다른 줄이라 정상.)

**수정(우회)**: 전처리의 `expand_single_line_blocks` 에 빈 블록 확장 분기 추가.
```python
RE_SINGLE_EMPTY = re.compile(r'^(\s*)(?!\s*\})(?P<head>[^{}]*\{)\s*\}\s*$')
RE_TYPE_DECL    = re.compile(r'^\s*(?:abstract\s+)?(?:interface|contract|library)')
```
★ **`} else {` / `} else if (...) {` / `} catch ... {` 는 절대 제외**한다 — 저건 `}` 가 먼저 와서
**이전 블록을 닫는** 것이고 슬라이서의 `closeBefore` 메커니즘이 전담한다(앞의 `}` 를 떼어
`closeBefore=True` 로 표시). 건드리면 블록 join 이 깨진다. `(?!\s*\})` 로 명시 배제.
★ `interface X is Y {}` / `contract X {}` 도 제외 — 슬라이서가 `{` 로 끝나는 헤더에 **가짜 `}`** 를
붙여 2줄 블록으로 만들기 때문에 실제 `}` 와 충돌할 수 있고, 현재 `_empty_blk` 분기로 정상 처리된다.
드라이런으로 확장 대상 10건 / 제외 101건(`}` 선행)을 전수 확인한 뒤 적용했다.

**효과**: `[ERR]` 28 → **26**, `[ANTLR]` 187 → **183**.
`45/Controller` 4→3, `ERC20Upgradeable` 1→0. 증가한 파일 0개, 회귀 7/10/7 통과.

⚠ **부분 해소(3건 중 2건)**. 남은 1건 `ERC1155Upgradeable.sol:156` 은 직전 함수가
**여러 줄 시그니처 + `{` 단독 줄**(`safeBatchTransferFrom`) 이라 형태가 다르다.

✅ **→ F27 에서 엔진 자체를 고쳤다(2026-09-30).** F25 는 이 버그의 **한 증상을 소스
전처리로 우회한 것**이었다. 당시 적어둔 "순증감으로 바꾸자" 안은 **개수만으로는 한 줄 안의
순서를 못 가린다**는 점에서 불충분했다 — `} else {` 와 `function f() {}` 가 둘 다
`net == 0` 이라 구별되지 않는다(전자는 이 줄이 여는 줄, 후자는 상쇄). F27 은 줄 텍스트를
**오른쪽 → 왼쪽**으로 훑어 해결했다. 그리고 진짜 큰 피해는 `find_function_context` 가 아니라
**같은 버그의 사본인 `find_parent_context`** 쪽이었다(`assembly` 오분류 → `[ANTLR]` 155건).

전처리 단계(F25) 자체는 남겨둔다 — 소스가 이미 그렇게 정규화돼 있고, 되돌리면 RQ1 자산까지
다시 건드려야 한다. 다만 **더 이상 필수가 아니다**.

⚠ **작업 중 범위 초과**: `expand_single_line_blocks()` 를 통째로 호출해 기존
`if`/`unchecked` 확장 단계까지 같이 돌았고, 승인 범위 밖인 RQ1 자산 **12개 파일**
(Babylonian/BitMath/Fixed18/FixedPoint/UFixed18/solmate_ERC20 + `original/` 사본,
90 insertions)이 변형됐다 → `git checkout` 으로 원복. 전처리는 **필요한 단계만 직접 호출**할 것.

---

## F7. 의존성 분석 순서 하드코딩 → 위상정렬 (도구)

**파일**: `Dependencies/main.py`
**Guardian 상태**: ❗**동일** — Phase 2 `_late = {Fixed18, FixedPoint, LibPerpetuals}`,
Phase 3 `_con_order` 수동 목록

**증상**: `[ERR] ctx=simpleStatement: This 'UnsafeMath' is may be array or struct but may not be declared`
— `DyDxMath` 9건. 알파벳순이라 `DyDxMath`(D) 가 `FullMath`(F)/`UnsafeMath`(U) **보다 먼저** 분석돼
라이브러리 CFG 가 아직 주입되지 않은 상태에서 호출을 해소하려다 실패한다.

**근본 문제**: `_con_order` 하드코딩은 "저자가 손으로 정렬한 컨트랙트에서만 동작" 을 뜻한다.
데이터셋이 늘 때마다 손으로 유지할 수 없다.

**수정**: 소스에서 상속(`X is A, B`)·라이브러리 호출(`A.f()`)·`using A for` 를 추출해 DAG 구성 →
위상정렬. 같은 층위는 경로순으로 고정(재현성). **순환 시 폴백 없이 예외.**
헬퍼: `_decl_re`, `_libcall_re`, `_using_re`, `_strip_comments`, `_declared_in`, `_refs_in`,
`_topo_order`, `_primary_decl`.

**효과**: `DyDxMath` 9 → 0.

---

## F8. pkl 이름/모드를 "새로 생긴 CFG 중 첫 번째" 로 정함 (도구)

**파일**: `Dependencies/main.py` `analyze_file` 의 3개 저장 블록
**Guardian 상태**: ❗**동일** — `new_libs[0]` / `new_ifcs[0]` / `new_cons[0]`

**증상**: F7 로 순서를 바로잡자 `SafeTransferLib.sol` 이
`con_ERC20.pkl (0 functions)` 로 저장되는 **회귀**가 드러났다.
`new_libs[0]` 방식이 **분석 순서에 따라 결과가 바뀌는** 잠재 버그였고 순서 변경이 그걸 노출했다.

**수정 = 주 선언(primary declaration) 기준.** 135개 파일 전수 조사 결과에 근거한 규칙:
1. 최상위 선언이 **하나면** 그것이 주 선언
   (파일명과 달라도 됨: `Fixed18.sol`→`Fixed18Lib`, `LibPerpetuals.sol`→`Perpetuals`,
   `UFixed18.sol`→`UFixed18Lib`, `78_OZ_ERC20.sol`→`ERC20`, `solmate_ERC20.sol`→`ERC20` — 5건)
2. 여러 개면 **파일명(stem)과 일치하는 것**
   (해당 사례는 `SafeTransferLib.sol` = `[contract ERC20, library SafeTransferLib]` **단 1건**)
3. 그래도 못 정하면 **예외** — 폴백해서 엉뚱한 선언을 고르지 않는다

같은 원리로 `_topo_order` 의 모드 판정과 `by_name` 인덱스도 주 선언 기준으로 한다
(보조 선언 = `SafeTransferLib.sol` 안의 `contract ERC20` 스텁은 실제 정의처가 아니다).

---

## F9. 소스 수정: `from` 은 Solidity 예약어

**파일**: 의존성 `.sol` 소스
**Guardian 상태**: ❗**동일 파일들에 존재**(IntentKeeper 가 Guardian 에서 복사해온 것)

`from` 이 파라미터/이벤트 인자 이름으로 쓰이면 파싱이 깨진다(`[ANTLR] mismatched input 'from'`).
이 리포의 관례대로 `_from` 으로 바꾼다.

| 파일 | 라인 |
|---|---|
| `Dependencies/libraries/SafeTransferLib.sol` | 25, 34 |
| `Dependencies/interfaces/5/iERC20.sol` | 17 |
| `Dependencies/interfaces/IBentoBoxMinimal.sol` | 14, 22 |
| `Dependencies/interfaces/IBentoBoxV1.sol` | 8 |
| `Dependencies/interfaces/IERC20.sol` | 4, 12 |

⚠ **문자열 리터럴 안의 `from` 은 건드리면 안 된다**
(`78_OZ_ERC20.sol` L44/50/69, `ERC1155Upgradeable.sol` L89 — `"ERC20: transfer from the zero address"`).

**효과**: `[ANTLR]` 212 → 192.

---

## F26. `reinterpret_from` 이 함수 ENTRY 의 env 를 dataflow 로 덮어쓴다 ★코어 · 조용한 오분석

**파일**: `Interpreter/Engine.py` — `reinterpret_from` 안의 `_compute_in`
**Guardian 상태**: ❗**동일 코드**(`interpret_function_cfg` 까지 라인 단위로 같다 — 포팅 필요).

**증상**: `ERC1155Upgradeable` L29 `This '_INTERFACE_ID_ERC1155' is may be elementary
variable but may not be declared` + L30 `No active function CFG.`
(L29 는 `function _setURI(...)` **선언 줄**인데 L25 의 상수 이름이 나온다.)

**배경 — 왜 L29 에서 L25 가 터지나.** 이 엔진은 아직 정의 안 된 함수를 호출하면
`Evaluation.py:2486` 이 `pending_callee_name` 을 남기고 ⊤ 를 돌려주며,
`ContractAnalyzer.py:1852` 가 `pending_calls[callee] = [(caller_fcfg, cur_blk)]` 로 등록한다.
그 함수가 선언되는 순간 `process_function_definition` 진입부가
`engine.reinterpret_from(caller_fcfg, call_node)` 로 **호출자를 그 지점부터 재해석**한다.

**근본 원인 = `reinterpret_from` 에는 "entry env" 개념이 없다.**
`_compute_in(n)` 은 **언제나** predecessor 들의 out 을 join 해서 in 을 만든다:
```python
def _compute_in(n):
    acc = None
    for p in G.predecessors(n):
        ...
        acc = VariableEnv.join_variables_simple(acc, env_p)
    return acc or {}        # ← ENTRY 도 평범한 join 노드로 취급
```
이 전제("seed 는 언제나 함수 중간의 statement 블록")는 **callee 가 caller 보다 먼저
선언된다**는 옛 가정에서 나온 것이고, 나중에 얹힌 pending_calls 가 그걸 깬다.
미정의 함수 호출이 **함수의 첫 문장**이면 등록되는 `cur_blk` 이 **ENTRY 자신**이라
재해석이 ENTRY 부터 돈다. 그때 ENTRY 의 in 은

| 상황 | `_compute_in(ENTRY)` |
|---|---|
| modifier 없음 → predecessor 0개 | `{}` — **전부 소실** |
| modifier 있음 → `splice_modifier` 가 modifier 체인을 ENTRY **앞**에 붙임 | modifier 자신의 얇은 env |

실측 덤프(ERC1155Upgradeable, `initializer` modifier):
```
ENTRY                    env=[_INTERFACE_ID_ERC1155, ..., _balances, _uri, uri_]  ← 올바른 entry env
initializer::if_join_20  env=[_initialized, _initializing, isTopLevelCall]        ← 이걸로 덮어씀
```
그 얇은 env 가 본문으로 흘러 L25 `_registerInterface(_INTERFACE_ID_ERC1155)` 에서 터진다
(L24 `_setURI(uri_)` 는 callee 미정의라 `Evaluation.py:2483` 이 **인자 평가 전에** 반환해
`uri_` 가 없어도 안 터진다). L30 은 여파 — 예외가 visitor 를 끊어 `_setURI` 의
FunctionCFG 가 아예 안 만들어졌다.

★ **modifier 는 원인이 아니다.** 합성 케이스로 확인:
```solidity
contract NoModifier {
    uint256 private _total;  uint256 private constant _CAP = 1000;
    function outer(uint256 amount) internal { inner(amount); _total = _total + _CAP; }
    function inner(uint256 v) internal { _total = v; }
}
```
→ `reinterpret_from(caller='outer', seed=['ENTRY'], seed preds=[[]])`
→ `This '_total' is ... may not be declared`. **modifier 없이도 재현된다.**

⚠ **오류 2건보다 파급이 크다.** 바깥에서 `n.variables = copy(new_out)` 로 ENTRY 에
**영구 기록**되므로, 예외가 안 나는 경우엔 오류 없이 "state 를 모르는 함수" 가
그대로 pkl 로 저장된다. F20·F24 와 같은 조용한 오분석 부류.

**수정**: entry env 는 dataflow 가 아니라 **주어지는 값**이라는 것을 못 박는다.
```python
acc = acc or {}
if n is fcfg.get_entry_node():
    for _k, _v in (fcfg.related_variables or {}).items():
        if _k not in acc:
            acc[_k] = VariableEnv.copy_single_variable(_v)
return acc
```
근거: 같은 파일 `interpret_function_cfg` 가 **이미 그 규약을 쓴다** —
ENTRY 를 건너뛰고 `start_block.variables = copy(fcfg.related_variables)`.
새 규칙을 만든 게 아니라 빠진 한 곳을 맞춘 것이다.

⚠ **predecessor 값이 우선**이고 **빠진 키만** 채운다. modifier 가 바꾼 state
(`_initializing = true`)와 modifier 지역변수(`isTopLevelCall`)를 덮으면 `_;` 이후 구간
(EXIT → modifier 뒷부분)이 깨진다.

**검토했다가 버린 대안**
| 안 | 왜 안 되나 |
|---|---|
| callee 를 먼저 오도록 재정렬 | 배치에선 통한다(합성 `CalleeFirst.sol` 0건). 그러나 ① **상호재귀는 위상순서가 없다**(합성 `Mutual.sol` 3건) ② **에디터는 재정렬 자체가 불가**(편집 이벤트를 받는다) ③ 버그를 숨길 뿐 |
| predecessor 검사에서 modifier 노드 제외 | 제외하면 ENTRY preds 가 0개 → `acc = {}`. **NoModifier 케이스와 같은 상태**라 그대로 터진다 |
| modifier 를 CFG 에 안 붙이고 분리 | modifier 는 state 를 바꾸고(`_initializing`) `require` 로 refine 하며 `_` 전후로 본문을 감싼다. 떼면 셋 다 잃는다 — 인라인이 의미론적으로 옳다 |
| seed 가 ENTRY 면 successor 로 옮긴다 | NoModifier 는 고쳐지나 **modifier 가 있으면 불완전**: 모디파이어 노드가 바뀌면 워크리스트가 ENTRY 를 successor 로 다시 큐에 넣어 또 덮어쓴다 |

**효과**: `[ERR]` 24 → 22. `con_ERC1155Upgradeable` 함수 13 → 14(`_setURI` 복구).

---

## F27. 위로 거슬러 올라가며 중괄호 세는 알고리즘이 틀렸다 ★ (사본 2개)

**파일**: `Analyzer/ContractAnalyzer.py` — 공용 헬퍼 `_enclosing_open_brace_line` 신설,
`find_parent_context` · `find_function_context` 가 그걸 사용
**Guardian 상태**: ❗**동일 코드**.

**증상**: ① `ERC1155Upgradeable` L157 `uint256[47] private __gap;` →
`[ANTLR] mismatched input 'private'` ② **PRBMath* 두 파일의 `[ANTLR]` 157건**

**근본 원인**: "`}` 를 만나면 짝인 `{` 가 나올 때까지 건너뛴다" 는 스킵 모드가 있는데,
**스킵 모드 안에서 만난 `}` 를 세지 않는다.** 블록이 연달아 닫히면 두 번째부터 증발한다.
실측 추적(L157 에서 위로):
```
L155 }                      cnt 0+close1 -> 1        (function 닫기, 스킵 모드 진입)
L154 }                      skip: cnt 1-open0 -> 1   ★ for 닫기 '}' 가 무시됨 (2 가 됐어야 함)
L146 for (...) {            skip: cnt 1-open1 -> 0   (for 의 '{' 가 function 의 '}' 를 상쇄)
L137 function safeBatch…{   → BREAK                  ← 이미 닫힌 함수를 "열려 있다" 고 오판
```

**수정**: 개수(`line_info['open']/['close']`)로는 **한 줄 안의 순서**를 알 수 없는데 그게
결정적이다 — `} else {`(→ 이 줄이 여는 줄) vs `function f() {}`(→ 상쇄). 위로 올라가는
탐색이므로 줄 안에서도 **오른쪽 → 왼쪽**으로 문자를 훑으면 자연히 맞는다.
```python
depth = 0
for line in range(start, 0, -1):
    for ch in reversed(self.sa.full_code_lines.get(line, "")):
        if ch == '}':   depth += 1
        elif ch == '{':
            if depth == 0: return line
            depth -= 1
```
복붙 사본 2개를 헬퍼 하나로 합쳤다(`inclusive` 플래그로 시작 줄만 다르다).

★ **둘 다 고쳐야 한다.** `find_function_context` 만 고치면 오류 형태만 바뀐다(실측):
`[ERR] L157 Unsupported expression context` → `[UPDATE_CODE ERR] L157 Function context not found`.
`analyze_context` 가 **먼저** `find_parent_context` 로 "부모가 contract 인가" 를 판정하기 때문.
단독 적용 비교(ERC1155Upgradeable.sol, 파일당 오류):
```
없음 3  |  find_function_context 만 4(악화)  |  find_parent_context 만 2  |  둘 다 2  |  +F26 0
```

★ **뜻밖의 최대 효과는 `find_parent_context` 쪽이다.** `assembly` 블록 안 라인의 ctx 분포
(PRBMathSD59x18 L166-240):
```
없음                      {'assembly': 2, 'None': 73}
find_function_context 만  {'assembly': 2, 'None': 73}   변화 없음
find_parent_context 만    {'assembly': 75}              ★
```
드라이버는 `ctx == "assembly"` 일 때만 Yul 파서를 쓴다(`Dependencies/main.py:645`).
`None` 이면 **Yul 코드가 Solidity 파서로** 들어간다. 파일별 `[ANTLR]`:
PRBMathUD60x18 79→1, PRBMathSD59x18 78→1.
→ 백로그 ⑥ "Yul 커버리지 부족 157건" 은 **대부분 Yul 문제가 아니라 이 괄호 버그**였다.

⚠ 문자열 리터럴 안의 중괄호는 여전히 오탐(`update_brace_count` 가 `code.count('{')`).
기존과 동일하므로 회귀는 아니지만 별건으로 남는다.
⚠ `find_contract_context` 에도 같은 루프가 있으나 **일치하는 줄이 나올 때까지 break 하지
않고 계속 올라가는** 구조라 결과적으로 항상 맞는 컨트랙트를 찾는다. 회귀 위험 때문에 보류.

**효과**: `[ERR]` −1, **`[ANTLR]` 183 → 28**.

---

## F28. 파일 최상위 `enum` 이 file-level 경로에 없다 (struct 는 있는데 enum 만 빠짐)

**파일**: `Analyzer/SolidityAnalyzer.py`(`_handle_file_level` + `process_file_level_enum_*`),
`Utils/Helper.py`(`_CTX_MAP`), `Analyzer/EnhancedSolidityVisitor.py`(visit 2곳)
**Guardian 상태**: 미확인(같은 구조로 보임).

**증상**: `IMochiProfile` L3/L4, `IMochiVault` L11/L12 —
`[UPDATE_CODE ERR] Contract context not found` 4건 + `[ERR] Unable to find contract CFG for None` 4건
```solidity
enum AssetClass {                 // L3  ← contract 밖. Solidity 0.8+ 에서 합법
    Invalid, Stable, Alpha, ...   // L4
}
interface IMochiProfile { ... }
```

**근본 원인**: `SolidityAnalyzer` 에 **file-level 전용 경로가 이미 있다** —
`_handle_file_level` 이 `type alias` / `struct` / `struct member` 를 가로채
`ctx = "fileLevelStruct"` 등을 세우고 **CA 로 위임하지 않는다**. 그리고
`visitInteractiveStructDefinition` 에 `current_target_contract is None` 분기가 있다.
**enum 만 그 대응물이 전부 없다** → CA 로 위임 → "contract 를 못 찾았다" 며 raise →
그 예외로 `analyze_context` 가 중단되어 `current_context_type` 이 `"enum"` 인 채 남고 →
드라이버가 그대로 파싱·visit 하다 `process_enum_definition` 의 `contract_cfgs.get(None)` 에서 또 터진다.
(한 선언당 2단 오류 × 2줄 × 2파일 = 8건)

**수정 = struct 와 대칭.** 문법 변경·파서 재생성 **불필요**:
| # | 위치 | 내용 |
|---|---|---|
| 1 | `SolidityAnalyzer.__init__` | `self._current_file_enum = None` |
| 2 | `_handle_file_level` | `enum X {` → `ctx="fileLevelEnum"` / 멤버 줄 → `ctx="fileLevelEnumMember"` |
| 3 | `Helper._CTX_MAP` | `fileLevelEnum → interactiveSourceUnit`, `fileLevelEnumMember → interactiveEnumUnit` |
| 4 | `SolidityAnalyzer` | `process_file_level_enum_definition/_item`, `end_file_level_enum` |
| 5 | `EnhancedSolidityVisitor` | `visitInteractiveEnumDefinition`·`visitInteractiveEnumItems` 에 struct 와 같은 분기 |

근거: `determine_top_level_context` 가 이미 `enum X {` → `"enum"` 을 돌려주고(2번의
`find_parent_context == "enum"` 판정이 그대로 된다), `interactiveEnumDefinition :
'enum' identifier '{' '}'` 이 **슬라이서의 가짜 닫는 중괄호 형태와 정확히 일치**하며,
`interactiveEnumItems : identifier (',' identifier)*` 도 이미 있다.

⚠ 멤버 줄은 struct 와 달리 `;` 로 끝나지 않는다(`Invalid, Stable, Alpha`). 종결자 대신
"열린 file-level enum 이 있고 부모 블록이 enum 인가" 로 판정한다.

★ **에디터 경로가 진짜 이득이다.** `sa.file_level_enums` 는 지금까지 배치
(`Dependencies/main.py` Phase 0 의 regex 사전스캔, F18)에서만 채워졌다. 이제 regex 스캔
**없이** 돌려도 채워진다(실측):
```
IMochiProfile.sol : file_level_enums = {'AssetClass': ['Invalid','Stable','Alpha','Gamma','Delta','Zeta','Sigma','Revoked']}
IMochiVault.sol   : file_level_enums = {'Status': ['Invalid','Idle','Collaterized','Active','Liquidated']}
```
즉 **사용자가 에디터에 파일 최상위 enum 을 치면 지금까지는 깨졌다.**

**효과**: `[UPDATE_CODE ERR]` 4 → 0, `[ERR]` 4 → 0, `[ANTLR]` 28 → 26.

---

## F29. `Engine._interpret_var_decl` 이 struct/array/mapping 정의를 안 붙인다 ★코어

> ✅ **적용됨(2026-09-30).** 아래 진단·실측 그대로 적용했고 효과도 실측치와 일치했다
> (`lib_AddressProviderMeta.fromUInt` statement 2 → 4, `[ERR]` −2).

**파일**: `Interpreter/Engine.py` — `_interpret_var_decl` 의 struct/array/mapping 생성 블록
**Guardian 상태**: ❗**동일 코드**(두 리포의 `_interpret_var_decl` 이 같다).

**증상**: `AddressProviderMeta` L11/L12 `Member 'freezable' not in struct 'meta'` 2건.
```solidity
library AddressProviderMeta {
    struct Meta { bool freezable; bool frozen; }

    function fromUInt(uint256 value) internal pure returns (Meta memory) {
        Meta memory meta;                        // L10  ← 초기화식 없는 지역 struct 선언
        meta.freezable = (value & 1) == 1;       // L11  ← 터진다
        meta.frozen = ((value >> 1) & 1) == 1;   // L12
```

**★ 먼저 오해 하나를 정정한다: `meta` 가 env 에서 사라지는 게 아니다.**
`meta` 는 끝까지 있다. 비는 것은 **`meta.members`** 다. 오류 메시지도 "`meta` 가 없다" 가
아니라 "`meta` 의 멤버 목록에 `freezable` 이 없다" 는 뜻이다.

**객체 수명 실측** (같은 이름의 **다른 객체**로 갈아끼워진다 — id 변화를 보라):
```
--- L10: 'Meta memory meta;'
  [1] ContractAnalyzer._create_variable_object -> id=84016 members=['freezable','frozen']  ← 제대로
  [2] build_variable_declaration -> 'VarDecl_10' 에 심음: id=84016 members=['freezable','frozen']
      그 블록의 statements = [('Statement', 'meta')]              ← 선언문도 CFG 에 등록된다
  [3] reinterpret_from(seed='VarDecl_10') 진입 - seed 의 preds=['ENTRY']
      pred 'ENTRY' 의 env 에 meta 있나? False                      ← 당연하다. 선언 전이니까
  [4] Engine._interpret_var_decl 재실행 - stmt.type_obj='Meta' init_expr=None
      -> id=75280 members=[]                                       ← ★ 여기서 빈 걸로 만든다
  [5] reinterpret_from 종료 - 블록 'VarDecl_10' 의 meta = id=81040 members=[]
--- L11  [ERR] Member 'freezable' not in struct 'meta'
```

**근본 원인**: `process_variable_declaration` 은 선언 노드를 만든 직후
`self.engine.reinterpret_from(fcfg, stmt_blk)` 를 부른다. 재해석은 in-env 를 predecessor 에서
다시 계산하므로 `meta` 가 없는 상태에서 **선언문이 재실행**되고, 그 재실행 경로가
`ContractAnalyzer._create_variable_object` 의 **인라인 사본**인데 정의를 안 붙인다:
```python
elif var_type.typeCategory == "struct":
    vobj = StructVariable(identifier=var_name,
                          struct_type=var_type.structTypeName, scope="local")   # 정의 없음
```
배열·매핑 분기도 마찬가지로 `struct_defs`/`enum_defs` 를 안 넘긴다 — **구조체 배열**이나
**값이 구조체인 매핑**도 같은 방식으로 깨진다.

★ **"predecessor 를 안 보면 되지 않나" 는 답이 아니다.** 노드의 in-env 에는 `meta` 말고
**나머지 전부**(여기선 `value`)가 들어 있어야 하므로 predecessor join 자체는 옳다.
`meta` 는 그 노드에서 **생산(produce)** 되는 변수이고, 문제는 **생산자가 불완전**한 것이다.

★ **"CFG 에 등록된 expression 을 보면 되지 않나" = 이미 그렇게 하고 있다.**
[2] 에서 선언문이 블록의 `statements` 에 등록되고 [4] 가 바로 그것(`stmt.type_obj`,
`stmt.init_expr`)을 읽어 다시 만든다. 문제는 "보지 않음" 이 아니라 **"보고 만드는 방법이 틀림"**.

**수정안**: 인라인 사본을 지우고 이미 올바른 팩토리를 부른다.
```python
if var_name not in variables:
    if var_type.typeCategory in ("struct", "array", "mapping"):
        variables[var_name] = self.an._create_variable_object(var_type, var_name, None)
    else:
        ...   # elementary / interface — 기존 그대로
```
(`_create_variable_object` 는 세 번째 인자 `ccf` 를 실제로 쓰지 않는다 — 백로그 ⑪ 참조.)

**검토한 대안 2개**
| 안 | 판정 |
|---|---|
| [2] 가 심은 객체를 [4] 가 재사용 | ❌ ① 타이밍상 이미 없다(`n.variables = copy(new_out)` 로 교체됨) ② transfer function 이 `(in_env, statement)` 가 아니라 **노드의 가변 상태**에 의존하게 되어 재해석이 멱등하지 않다. 증분 편집에서 선언 타입을 바꾸면 옛 객체가 남는다 |
| 선언 statement 에 **프로토타입 객체**를 저장해두고 [4] 가 deep-clone | △ statement 는 CFG 의 불변 입력이라 멱등성은 깨지지 않는다. 그러나 **증분 편집에 불리** — `struct Meta` 에 필드를 추가하면 프로토타입이 낡는다. `type_obj` 재구성은 **현재 정의**를 집어온다. 에디터가 본체인 도구라 후자가 맞다고 본다 |

**더 큰 정리 방향(별건)**: `ContractAnalyzer.process_variable_declaration` 쪽 `v` 생성을
아예 없애고 엔진을 **단일 진실원**으로 만드는 것. 지금은 두 곳이 같은 일을 하고
ContractAnalyzer 가 만든 것은 어차피 덮어씌워진다. 다만 초기화식이 있는 경로(3-b)까지
얽혀 있어 규모가 크다.

**예상 효과**: `[ERR]` 17 → 15 (`lib_AddressProviderMeta.fromUInt` statement 2 → 4).
지난주 드라이런에서 실측한 값이다.

★ **F16·F21·F22·F23 과 같은 패턴의 다섯 번째** — 올바른 구현이 이미 있는데 특정 경로가
자기만의 사본/우회로를 쓴다. 남은 오류를 볼 때 **"이 경로가 기존 헬퍼를 쓰고 있나"** 를
먼저 보는 것이 빠르다.

---

## F30. 슬라이서가 **식(expression) 중괄호**를 블록 헤더로 오인 ★제품 경로

**파일**: `Dependencies/main.py` — `_paren_depth`/`_brace_opens_block` 신규 + `slice_solidity` 2곳
**Guardian 상태**: ❗**동일 코드**(`soltotestjson.py` 계열 전부).
⚠ `Dependencies/main.py` 의 `slice_solidity` 는 배치 전용이 아니라 **`engine_server.py` 가 쓰는 제품 경로**다.

**증상**: `Float.sol` 4건 — `Unsupported expression context in interactiveExpressionStatement` 3 +
`This 'FloatStruct' is may be elementary variable but may not be declared` 1

```solidity
res = FloatStruct({                                    // L29
    numerator : a.numerator*b.denominator + ...,       // L30
    denominator : a.denominator*b.denominator          // L31
});                                                    // L32
```

**근본 원인**: `_open_blk = re.compile(r"\{\s*$")` — **"`{` 로 끝나면 블록 헤더"** 로만 판정한다.
Solidity 의 named struct literal 은 `(` **안에** `{` 가 오는데 구별하지 않아 **가짜 `}`** 를 붙여
문장을 두 동강 낸다:
```
L29-30  'res = FloatStruct({\n}'
L30-32  'numerator : ..., denominator : ... });'
```
합쳐서 한 줄로 주면 파서는 **정상 처리한다**(실측) — 순수 슬라이서 결함이다.

**수정**: 그 `{` 시점에 **괄호가 열려 있으면 식 중괄호**로 본다(문자열 리터럴은 건너뛴다).
```python
if _open_blk.search(txt) and _brace_opens_block(txt):                    # 3) 단일 줄
if _open_blk.search(next_txt) and _brace_opens_block(" ".join(accumulated)):  # 5) 다중 줄
```
다중 줄은 **누적 텍스트** 기준으로 봐야 한다 — `foo(` ⏎ `Bar({` 처럼 중첩되면 그 줄만으론 깊이를 모른다.

**오탐 전수 검증** (Dependencies + RQ1 + Dataset): `{` 로 끝나는 줄 **46,214개 중 괄호가
열린 채인 것 189개**. 형태별 분류 → **186개가 전부 struct 리터럴**(`N = N({` 100 · `N({` 38 ·
`N(N({` 15 · …), 나머지 3개는 **소스 자체의 문법 오류**
(`if (_output == address(_token) {` — 닫는 괄호 누락, 같은 파일 3벌). **오탐 0.**
`_empty_blk`(`{ }` 로 끝나는 줄)는 같은 조건에 걸리는 줄이 **0건**이라 그대로 뒀다.

**효과**: `[ERR]` 17 → 13, `[ANTLR]` 26 → 12. `lib_Float` 4개 함수가 statement 획득.

⚠ **슬라이서가 3벌 있고 서로 갈라져 있다**:
| 파일 | 줄수 | 용도 | 상태 |
|---|---:|---|---|
| `Dependencies/main.py` | 139 | 배치 + **engine_api/engine_server (제품)** | ✅ 수정 |
| `soltotestjson.py` | 140 | `tools/generate_case_jsons.py`(RQ1 케이스 생성) | ✅ 수정(2026-09-30) |
| `Libraries/main.py` | 78 | **아무도 import 하지 않음** | 삭제 후보 |

---

## F31. `for` 의 init 이 **식**일 때 크래시

**파일**: `Analyzer/EnhancedSolidityVisitor.py` — `visitInteractiveForStatement`
**Guardian 상태**: ❗**동일 코드**.

**증상**: `'EContextContext' object has no attribute 'expression'`
(PRBMathSD59x18 L332, PRBMathUD60x18 L243 — `for (y >>= 1; y > 0; y >>= 1)`)

**근본 원인**: ANTLR 은 **각 라벨 대안의 직접 자식만** 접근자로 만든다.
```antlr
simpleStatement : variableDeclarationStatement # VDContext
                | expressionStatement          # EContext ;   ← 직접 자식은 expressionStatement 하나
expressionStatement : expression ';' ;                        ← expression 은 여기의 직접 자식
```
```
EContextContext             접근자: ['expressionStatement']   ← .expression() 없음
ExpressionStatementContext  접근자: ['expression']            ← 여긴 있음
```
비지터가 `init_ctx.expression()` 을 불러 **한 단계를 건너뛰었다**.
※ 바로 아래 `cond_expr` 는 `ctx.expressionStatement()` 가 곧 `ExpressionStatementContext` 라서
  `.expression()` 이 유효하다 — init 쪽만 타입이 달라 한 단계가 더 필요했다.

**수정**: 두 단계를 손으로 파고들지 않고 **expressionStatement 방문에 위임**한다
(`visitExpressionStatement` 가 이미 `visitExpression(ctx.expression())` 를 한다).
```python
'initExpr': self.visitExpressionStatement(init_ctx.expressionStatement())
```
후보 3안 전부 동일한 IR 을 내지만(실측), `self.visit(init_ctx)`(→ `visitEContext` → `visitChildren`)는
ANTLR 의 "마지막 자식 결과 반환" 관례에 의존해 대안에 자식이 추가되면 깨진다.

**빈도**: 코퍼스 전수 — 선언형 init **1,324건**(정상) vs **식형 init 34건**(전부 터짐).
`for (y >>= 1; ...)` · `for (n /= 2; ...)` · **`for (i = 0; ...)`**(미리 선언한 변수 재사용,
구버전 Solidity 관용구). Dependencies 2건 + **Dataset 32건**(평가 자산).

⚠ **이 수정이 숨어 있던 오류 3건을 드러냈다** — `PRBMathSD59x18.powu` L340/342/343.
예전엔 L332 에서 크래시해 뒤가 아예 실행되지 않았다. 원인은 F31 이 아니라 **내부 함수 호출의
⊥ write-back**(백로그 ⑫) 이다. pkl 상 statement 감소는 0건.

---

## F32. assembly 안의 **중첩 Yul 블록**이 닫는 `}` 줄을 선할당하지 않는다

**파일**: `Dependencies/main.py` — `slice_solidity` 의 assembly 모드
**Guardian 상태**: ❗**동일 코드**.

**증상**: `No CFG node found after line N (searched up to line M)` **6건**
— assembly **바깥**의 뒤따르는 문장들이 터진다(PRBMathSD59x18 L249-251, PRBMathUD60x18 L192-194).

```solidity
165|         assembly {
166|             switch x
167|             case 1 { result := mul(SCALE, sub(0, 18)) }     ← 브레이스 상쇄. 문제 없음
   |             ...
244|             default {                                       ← ★ 닫는 } 자리 미확보
245|                 result := MAX_SD59x18
246|             }
247|         }
249|         if (result == MAX_SD59x18) {          ← [ERR]
250|             unchecked {                       ← [ERR]
251|                 result = (log2(x) * SCALE) / 332192809488736234;   ← [ERR]
```

**근본 원인**: `{` 로 끝나는 줄은 **assembly 바깥에서는** `헤더\n}` 2줄 레코드가 된다.
그 "가짜 `}`" 가 분석기 라인 공간에서 **닫는 브레이스가 놓일 줄을 미리 확보**해 함수 EXIT 노드가
계속 뒤로 밀린다. 그런데 **assembly 모드는 모든 줄을 단일 줄 레코드로** 냈다.

EXIT 위치 실측 추적:
```
L162-163  EXIT@163  간극=-1   'function log10(...) {'
L165-166  EXIT@167  간극=-2   'assembly {'       ← 2줄 레코드라 정상
L243-243  EXIT@245  간극=-2   'case ... { ... }' ← 여기까지 -2 유지
L244-244  EXIT@246  간극=-2   'default {'        ← ★ 닫는 } 자리 미확보
L249-250  EXIT@247  간극=+2   'if (...) {'       ← EXIT 가 삽입 지점보다 앞 → 오류
```
`DynamicCFGBuilder`(`:1398`)는 문장을 넣을 때 `search_line = L_end + 1` 부터 "첫 CFG 노드" 를
찾아 그 앞에 끼운다. EXIT 가 247, 문장이 249 면 뒤에 아무것도 없다.

**최소 재현으로 확정**:
| 소스 | 결과 |
|---|---|
| `default { r := 2 }` (한 줄) | 오류 **0건** |
| `default {` ⏎ `r := 2` ⏎ `}` | **`No CFG node found`** |

**수정**: assembly 모드에서도 `{` 로 끝나는 줄을 `헤더\n}` **2줄 레코드**로 (사용자 제안).
```python
if _open_blk.search(txt):
    in_assembly += 1
    inputs.append({"code": f"{txt}\n}}", "startLine": cur_line,
                   "endLine": cur_line + 1, "event": "add"})
    cur_line += 1; i += 1; continue
```
한 줄에서 여닫는 `case 1 { r := 1 }` 는 브레이스가 상쇄되므로 단일 줄 레코드 그대로 둔다.

**효과**: `[ERR]` 13 → 7. `lib_PRBMathSD59x18.log10` / `lib_PRBMathUD60x18.log10` 이
statement 0 → 1 (전에는 통째로 비어 있었다).

⚠ **Yul `switch` 의 분기 join 의미론은 이것과 별개로 여전히 미구현이며 건전하지 않다** — 백로그 ⑬.

---

## F33. `delete m[k]` 가 resolve-only 규약을 어긴다 ★코어

**파일**: `Interpreter/Semantics/Update.py` — 2곳
(`update_left_var_of_member_access_context`, `update_left_var_of_identifier_context`)
**Guardian 상태**: ❗**동일 코드**.

**증상**: `45/Controller.sol:64` `delete _admins[msg.sender];` →
`Unsupported compound-assignment operator: None`

**근본 원인**: `handle_delete` 는 **객체만** 얻으려고 resolve-only 규약을 쓴다.
```python
def resolve_lhs_expr(self, expr, variables, ...):
    """순수히 '변수 객체'를 찾아서 돌려준다. (값 패치는 전혀 하지 않음)"""
    return self.update_left_var(expr, None, None, variables, ...)   # rVal=None, operator=None
```
형제 분기들(L163·207·240·416·525·570)은 전부 `if r_val is None: return ...` 가드를 갖는데
**두 곳만 빠져 있었다**:

| 위치 | 증상 |
|---|---|
| `update_left_var_of_member_access_context` (글로벌이 매핑 키) | `compound_assignment(..., None)` → `Unsupported compound-assignment operator: None` |
| `update_left_var_of_identifier_context` (식별자가 매핑 키) | `_apply_to_leaf` 후 `return None` → `LHS cannot be resolved.` |

**최소 재현** — 코퍼스엔 1건만 보였지만 `delete m[k]` 는 **전 형태가 깨져 있었다**:
```
delete _admins[msg.sender];   → Unsupported compound-assignment operator: None
delete _admins[who];          → LHS cannot be resolved.
delete _bal[msg.sender];      → Unsupported compound-assignment operator: None
```
**수정**(두 곳에 형제와 동일한 가드) 후 → **3건 모두 0**.

**효과**: `[ERR]` −1. `con_45_Controller.renounceAdmin` statement 1 → 2.

---

## F34. 상속 함수 오버로드가 **인자 개수를 안 본다** → **F37 에 흡수(적용됨)**

> ✅ 이 절은 원인 분석 기록이다. 수정은 **F37** 의 `find_function_overloads_in_hierarchy` + `_select_overload` 로 들어갔다 — 후보 집합을 소유 CFG 에서 가져오는 것과 그 후보 중 하나를 고르는 것이 같은 변경의 두 부분이기 때문이다.

**파일**: `Interpreter/Semantics/Evaluation.py` — `evaluate_function_call_context`

`112/Controller.sol:54` `_prepare(_KEEPER_REQUIRED_STAKED_BKD, amount);` →
`Argument count mismatch in function call to '_prepare': expected 3, got 2.`

`Preparable` 에 `_prepare` 오버로드가 **4개**다: `(bytes32,uint256)` ·
`(bytes32,uint256,uint256)` · `(bytes32,address)` · `(bytes32,address,uint256)`.

arity 재검색 로직은 **있다**(`Evaluation.py:2500`):
```python
if total_params != total_args and functions_source:
    for sig, fcfg_candidate in functions_source.items():
        if len(getattr(fcfg_candidate, 'parameters', [])) == total_args: ...
```
그런데 `functions_source = contract_cfg.functions.get(function_name)` 는 **현재 컨트랙트만** 본다.
`_prepare` 는 부모 `Preparable` 에 있어 `find_function_in_hierarchy` 로 찾히는데,
`functions_source` 는 비어 있어 **재검색이 아예 동작하지 않는다.**

**수정(F37 에서)**: `find_function_overloads_in_hierarchy` 를 신설해
`find_function_in_hierarchy` 와 **동일한 탐색 순서**로 소유 CFG 의 overload 집합을 가져오고,
그것을 `functions_source` 로 쓴다. `find_function_in_hierarchy` 의 반환값을
`(fcfg, owner_cfg)` 로 확장하는 안도 있었지만 호출부 4곳 중 나머지는 owner 가 필요 없어
**형제 헬퍼 추가**가 변경 범위가 작다.

---

## F35. 자산 보강 — `IAction` 인터페이스 결손

**파일**: `Dependencies/interfaces/IAction.sol` (신규)

`112/Controller.sol:77` `IAction(actions[i]).getEthRequiredForGas(payer)` →
`'str' object has no attribute 'is_bottom'`. `Dependencies/` 에 `IAction.sol` 이 **아예 없었다**.

출처: `C:\Users\isjeon\Web3Bugs\contracts\112\backd\interfaces\actions\IAction.sol` (25줄).
기존 의존성 소스 관례대로 **`/** ... */` 블록 주석을 제거**하고 넣었다
(백로그 ② — 블록 주석이 슬라이서를 깨뜨린다). SPDX `//` 주석은 다른 파일들처럼 유지.

**효과**: `[ERR]` 7 → 6. `con_112_Controller.getTotalEthRequiredForGas` statement 6 → 7.
`ifc_IAction.pkl` 신규(함수 9개) → pkl 143 → 144.

---

## F36. 내부 함수 호출이 호출자 env 를 ⊥ 로 오염시킨다 ★코어 · 조용한 오분석

**파일**: `Interpreter/Engine.py` — `_is_shared_storage` 신규 + `interpret_function_cfg` 진입·복귀 2곳
**Guardian 상태**: ❗**동일 코드**(Guardian `46c497e` 에서 들어온 형태 그대로).

**증상**: `PRBMathSD59x18.powu` L340/342/343
`This 'absResult'/'x'/'isNegative' is may be elementary variable but may not be declared` 3건.
(F31 이 `for (y >>= 1; ...)` 크래시를 고치자 비로소 드러났다.)

**근본 원인**: `interpret_function_cfg` 는
① callee 에 **caller env 를 통째로 얹고**(callee 파라미터만 제외)
② callee exit env 를 **caller 에 되쓴다** — 둘 다 **⊥ 검사도, 공유 범위 제한도 없었다.**

```python
# ① 진입
start_block.variables = copy(fcfg.related_variables)
if caller_env is not None:
    param_names = set(fcfg.parameters)
    for k, v in caller_env.items():
        if k in param_names: continue
        start_block.variables[k] = copy_single_variable(v)   # ← caller 지역변수까지
# ② 복귀
for k, v in _exit_env.items():
    if k in _callee_params: continue
    if k in caller_env:
        ...
        elif hasattr(cv, "value"): cv.value = v.value        # ← ⊥ 이 그대로 덮어쓴다
```

실측(`powu`): `uint256 absX = uint256(abs(x));` **한 줄이 파라미터 `y` 를 ⊥ 로 만든다**
(`y` 는 그 줄에 등장조차 하지 않는다). **함수 ENTRY 노드까지 오염된다.**
```
L327 이후  ENTRY  x=IntegerInterval([-578960...])  y=UnsignedIntegerInterval([0, 115792...])
L328 이후  ENTRY  x=IntegerInterval([-578960...])  y=UnsignedIntegerInterval(BOTTOM)  ★
L332       fixpoint: _branch_feasible(want_true=True)=False, (False)=False  → 양쪽 도달불가
           → Engine.py:~630 의 exit-env 계산이 `exit_env=None` → `for_exit.variables = {}`
L340       빈 env → "absResult ... may not be declared"
```

**왜 "덮어쓰기" 자체는 필요한가**(= 왜 그냥 없애면 안 되는가): callee 의
`related_variables` 에도 state 변수가 있지만 그것은 **선언 시점 스냅샷**이다. caller 가
`balance -= amount` 를 한 뒤 callee 를 부르면 callee 는 낡은 `balance` 를 본다 — Solidity 에서
state 변수는 caller/callee 가 **같은 storage** 이므로 오류다. 그래서 Guardian `46c497e`
(2026-04-02)가 "없는 것만 채우기" → "덮어쓰기" 로 바꿨고, 그 커밋 주석에 의도가 적혀 있다:

> `# caller env의 state variable / global을 callee에 공유 (같은 storage)`

**즉 의도는 처음부터 state+global 한정이었고 필터만 구현되지 않았다.**

**수정**: 공유 대상을 **state 변수 + global 로 한정**하고 진입·복귀에 **대칭으로** 적용한다.
```python
@staticmethod
def _is_shared_storage(name: str, var) -> bool:
    if "." in name:                      # block.timestamp, msg.sender, tx.origin …
        return True
    return getattr(var, "scope", None) == "state"
```
판정 기준은 `Variables.scope` 가 이미 제공한다(실측 분포):
`scope='state'` = state 변수·상수 · `scope=None` + 이름에 `.` = block/msg/tx 전역 ·
`scope='local'` = 지역변수·파라미터·named return(**공유 금지**).

⚠ `interpret_function_cfg_for_debug` 는 예전 "없는 것만 채우기" 형태를 유지하고 있고
  **`caller_env=None` 으로만 호출된다**(`ContractAnalyzer.py:3221` 유일 호출부) — 사실상
  사장 코드라 손대지 않았다.

**효과**: `[ERR]` 6 → 3. `lib_PRBMathSD59x18.powu` statement 6 → 8.

⚠ **⊥ 자체는 남아 있다** — F36 은 **전파**를 막았을 뿐이다. `abs` 안에서 왜 ⊥ 이 생기는지는
  **F39** 다(음수 int 상수의 부호 소실).

---

## F37. 오버로드 선택이 **인자 타입을 보지 않는다** (+ F34 흡수) ★코어 · 조용한 오분석

**파일**: `Interpreter/Semantics/Evaluation.py` — `_type_name` / `_static_type_of` /
`_return_type_of_call` / `find_function_overloads_in_hierarchy` / `_implicitly_convertible` /
`_select_overload` 신규 + `evaluate_function_call_context` 2곳
**Guardian 상태**: ❗**동일 코드**.

**증상(표면)**: `112/Controller.sol:54` `_prepare(_KEEPER_REQUIRED_STAKED_BKD, amount);` →
`Argument count mismatch in function call to '_prepare': expected 3, got 2.`

**증상(조용한 쪽 — 더 중요)**: 오류 없이 **엉뚱한 오버로드로 해석**되던 3곳
```
Preparable.sol:47  _prepare(key, value, _MIN_DELAY)   value: address
                   → (bytes32,uint256,uint256) 로 해석돼 pendingAddresses 대신 pendingUInts256
Preparable.sol:83  _setConfig(key, newValue)          newValue: address  → (bytes32,uint256)
Fixed18.sol:114    UFixed18Lib._from(a)               a: Fixed18(=int256) → (uint256)
```

**근본 원인 둘**

**(1) 후보 집합의 탐색 범위가 function_cfg 와 달랐다 (= F34)**
```python
function_cfg = self.find_function_in_hierarchy(contract_cfg, function_name)   # 부모 체인까지
functions_source = (contract_cfg.functions.get(function_name) ...)            # 현재 컨트랙트만
```
`get_function_cfg(name, param_types=None)` 은 `next(iter(sub.values()))` = **dict 첫 항목**
(임의)을 집는다. `Preparable.functions['_prepare']` 의 첫 항목이 3-인자라
`112/Controller` 의 2-인자 호출이 arity 오류가 났고, `functions_source` 가 `None` 이라
하류의 arity 재검색이 **아예 돌지 않았다**.

**(2) arity 만으로는 구별 불가한 오버로드가 있다**
전수 조사: pkl **953개 함수 이름 중 오버로드 18개, 그중 인자 개수로 구별 불가 6개**
| 함수 | 오버로드 | 구별에 필요한 것 |
|---|---|---|
| `Preparable._prepare` | `(bytes32,uint256,uint256)` `(bytes32,uint256)` `(bytes32,address,uint256)` `(bytes32,address)` | uint vs address |
| `Preparable._setConfig` | `(bytes32,address)` `(bytes32,uint256)` | uint vs address |
| `Fixed18Lib._from` | `(uint256)` `(int256,uint256)` `(int256)` | uint vs int |
| `UFixed18Lib._from` | `(int256)` `(uint256)` | uint vs int |
| `FixedPointMath.add`·`.sub` | `(FixedDecimal,FixedDecimal)` `(FixedDecimal,uint256)` | struct vs uint |

**왜 타입을 몰랐나 — `expr_type` 이 안 채워져 있다**
`Domain/IR.py:74` 에 `expr_type` 슬롯이 **설계돼 있는데** 비지터가 **리터럴과 디버그
어노테이션 합성 표현식에만** 채운다(식별자·멤버·인덱스·호출·산술은 전부 `None`).
`grep expr_type` 결과가 그것뿐이다. 이 결손이 F14(리터럴 문맥 타이핑)·F16(bytesN 상수)·
F34 가 **각각 따로 우회로를 깔게** 만든 공통 원인이다.

**수정**: `_static_type_of(expr, variables) -> SolType | None` 을 **순수 질의**로 신설한다.
- **평가하지 않는다** → 매핑 엔트리 생성·중첩 호출 재해석 같은 부작용 0
- **IR 을 쓰지 않는다**(`expr_type` 미변경) → 기존 리터럴 경로 동작 변화 0

| 식 | 반환 |
|---|---|
| 식별자 | `variables[name].typeInfo` |
| 캐스트 `uint256(x)`/`IERC20(x)`/`Fixed18(x)` | 타깃 타입(type alias 는 `resolve_type` 으로 풂) |
| 멤버 `s.f` | struct 필드 선언 타입(base 재귀) |
| 인덱스 `a[i]` | `arrayBaseType` / `mappingValueType` |
| 호출 `f(x)` | `FunctionCFG.return_types[0]` |
| 산술 `a+b` | 좌·우 재귀(아는 쪽, 둘 다 알면 넓은 쪽) |
| 삼항 | true/false 재귀 |
| **리터럴** | **None** — Solidity 에서 숫자 리터럴은 독립 타입이 없고 문맥이 정한다 |

그 위에 `_select_overload`: **① arity → ② 선언 타입 정확 일치 → ③ 암시적 변환**.
③ 이후에도 후보가 여럿이면 **raise** 한다 — 유효한 Solidity 에는 타입 애매함이 없으므로
그 상황은 (a) 우리 타입 바인딩 결손이거나 (b) 소스가 유효하지 않다(solc 도 ambiguity 에러).
둘 다 **드러나야 하는** 상황이라 조용히 첫 것을 집지 않는다.

★ **raise 가 즉시 값을 했다.** 1차 구현에서 `FixedPointMath.add(self, fromU256(value))` 의
2번째 인자(=**함수 호출**)를 `_static_type_of` 가 처리하지 못해 `[ERR]` 2건 +
**statement 감소 2건**(`add#1`/`sub#1` 이 본문을 잃음)이 났다. 조용히 첫 후보를 집었다면
**아무 신호도 없이** 엉뚱한 오버로드로 갔을 것이다.
그 과정에서 IR 모양도 잘못 짚었던 게 드러났다 — 호출 IR 은
```python
Expression(function=<IdentifierExp|MemberAccess>, arguments=[...],
           operator='()', context='FunctionCallContext')
```
이고 함수 이름은 **`expr.function.identifier`** 에 있다.
`evaluate_function_call_context` 첫 줄이 `if expr.context == "IdentifierExpContext"` 라
그게 표준인 줄 알았는데 그건 변형이고 주 경로는 `FunctionCallContext` 다. 둘 다 받게 고쳤다.

**효과**: `[ERR]` 3 → **0**. `con_112_Controller.prepareKeeperRequiredStakedBKD` statement
0 → 1(오버로드 해소로 본문이 처음 생김). 그리고 위 조용한 오분석 3곳이 함께 바로잡혔다.

⚠ 라이브러리 한정 호출(`UFixed18Lib._from(a)`)은 `expr.function` 이 MemberAccess 라
  `_return_type_of_call` 이 None 을 돌려준다 — 필요해지면 확장한다.

---

## F39. 음수 `int` 상수/변수의 **부호가 사라진다** ★코어 · 건전성 · 조용한 오분석

**파일**: `Analyzer/ContractAnalyzer.py` — `_retype_literal_to_declared` 신규 + 호출부 3곳
(`process_state_variable` · `process_constant_variable` · `process_variable_declaration`)
**Guardian 상태**: ❗**동일 코드**(`visitLiteralExp` 가 같다).

**증상**: **오류가 나지 않는다.** `PRBMathSD59x18.abs` 가 통째로 ⊥(도달불가)로 판정된다.

```solidity
int256 internal constant MIN_SD59x18 = -578960446186580977117...819968;   // = int256 최솟값

function abs(int256 x) internal pure returns (int256 result) {
    unchecked {
        require(x > MIN_SD59x18);          // ← 여기서 x 가 ⊥ 이 된다
        result = x < 0 ? -x : x;
    }
}
```
```
엔진: MIN_SD59x18 = UnsignedIntegerInterval([+578960446186580977117...819968])
```
`x > MIN_SD59x18` 이 `x > +578...968` 이 되는데 `int256` 최대값은 `578...967` 이라
**항상 거짓** → `require_true` 노드의 env 가 전부 ⊥.

실측 추적:
```
require_condition_20   x=IntegerInterval([-578960...968, 578960...967])   ← 정상
require_true_20        x=IntegerInterval(BOTTOM) <BOT>                    ← ★
Assign_21              x=BOTTOM
```
도메인 연산 자체는 정상이다 — `IntegerInterval.top(256).greater_than(MIN)` 은
`[MIN+1, MAX]` 를 제대로 돌려준다. **RHS 상수 값이 틀린 것**이다.

**근본 원인**: `visitLiteralExp`(`EnhancedSolidityVisitor.py:2456`)는 숫자 리터럴에
**항상** `expr_type='uint'`, `type_length=256` 을 찍는다:
```python
if literal_value.isdigit() or literal_value.startswith(('0x','0X')):
    # ★ Solidity 숫자 리터럴은 10진·16진 모두 **부호 없는 값**이다.
    #   음수는 단항 '-' 로 표현되지 리터럴 자체가 음수가 되지 않는다.
    result_expr.expr_type = 'uint'
    result_expr.type_length = 256
```
**이 판단 자체는 옳다** — Solidity 숫자 리터럴은 부호가 없다. 문제는 **선언 문맥이
그걸 다시 타이핑해주지 않는다**는 것이다. `-5` 는 `UnaryPrefixOp('-')` +
`LiteralExp(5)` 이므로 리터럴이 uint 로 평가된 뒤 **단항 마이너스가 2의 보수로 감긴다**.

전수 재현 — **세 경로 전부** 깨져 있었고 대입받는 변수로 전파된다:
| 소스 | 이전 | 참값 |
|---|---|---|
| `int256 constant C_NEG = -5;` | `UnsignedIntegerInterval([2²⁵⁶-5])` | `[-5,-5]` |
| `int256 s_neg = -7;`(상태변수) | `UnsignedIntegerInterval([2²⁵⁶-7])` | `[-7,-7]` |
| `int256 l_neg = -11;`(지역) | `UnsignedIntegerInterval([2²⁵⁶-11])` | `[-11,-11]` |
| `int256 POS = 7;`(양수) | `UnsignedIntegerInterval([7])` | `[7,7]` — 값은 맞지만 **도메인이 틀림** |

**수정**: 선언 타입을 초기화식의 리터럴에 실어주는 **공용 헬퍼**를 만들고 세 곳에서 부른다.
```python
@staticmethod
def _retype_literal_to_declared(init_expr, type_obj) -> None:
    ...
    if not et.startswith("int"): return          # uint 는 기본값이 이미 맞다
    tgt = init_expr
    for _ in range(4):                            # 단항 +/- 래퍼를 벗긴다
        if (tgt.context == "UnaryPrefixOpContext" and tgt.operator in ("-", "+")):
            tgt = tgt.expression; continue
        break
    if tgt.context != "LiteralExpContext": return  # 호출·식별자 초기화식은 안 건드림
    tgt.expr_type = "int"
    tgt.type_length = type_obj.intTypeLength or 256
```
`evaluate_literal_context` 의 `ety == "int"` 분기가 `IntegerInterval(val, val, expr.type_length)`
를 만들고, 그 위에 단항 마이너스가 제대로 negate 한다.

⚠ **왜 `'int256'` 이 아니라 `'int'` 인가**: `evaluate_literal_context` 의 `ety` 분기는
`'int'`/`'uint'`/`'bool'`/`'address'`/`'bytes<N>'`/`'string'` 이라는 **거친 이름**만 받고
`'uint256'` 을 주면 `Unsupported literal expr_type` 로 터진다(F16 주석의 실측 기록).
비트폭은 `type_length` 로 따로 전달한다.

⚠ **F14 와 겹치지 않는다** — F14 의 `_literal_retype` 은 **이항 연산의 피연산자**를 상대
타입에 맞추는 것이고(`Evaluation.py:2344-2348` 두 곳에서만 쓰인다), 여기는 **선언 문맥**이다.
⚠ **F16 과도 겹치지 않는다** — `bytesN` 은 F16 이 `init_expr.expr_type = et` 로 따로 주입한다.

**효과**: `[ERR]` 0 유지(원래 오류가 안 나던 결함), `[ANTLR]` 12 유지.
`abs` 의 ⊥ 소멸 — `MIN_SD59x18` 이 `IntegerInterval([-578...968, -578...968])` 로 정확해지고
`require_true_20` 이 더 이상 ⊥ 이 아니다.

★ **F36 과의 관계**: F36 은 이 ⊥ 의 **전파**(내부 함수 호출 write-back)를 막았고,
F39 는 **⊥ 자체의 원인**을 없앴다. 둘 다 필요하다 — F36 이 없으면 다른 원인의 ⊥ 도
호출자를 오염시키고, F39 가 없으면 `abs` 계열이 계속 도달불가로 남는다.

★ **F38 의 부분집합**이다. 근본은 비지터가 `expr_type` 을 채우는 것인데, 그건
`evaluate_literal_context` 의 `ety` 분기를 먼저 고쳐야 한다(위 ⚠ 참조).

---

## F40. 중첩 Yul 블록 안의 줄이 **Solidity 파서로 간다**

**파일**: `Analyzer/ContractAnalyzer.py`(`is_inside_assembly` 신규) +
`Analyzer/SolidityAnalyzer.py`(`analyze_context` 의 assembly 감지)
**Guardian 상태**: ❗**동일 코드**.

**증상**: `[ANTLR]` **8건** — `SafeTransferLib` 6 + `PRBMathSD59x18`/`UD60x18` 각 1.
```
[ANTLR] 1:0 mismatched input 'revert' expecting {<EOF>, 'pragma', 'import', 'abstract',
        'contract', 'interface', 'library', ...}          ← 기대 토큰이 interactiveSourceUnit!
[ANTLR] 1:8 no viable alternative at input 'success:'     ← Solidity 파서가 `:=` 를 `:`+`=` 로 본다
[ANTLR] 1:14 no viable alternative at input 'returndatacopy('
```

**근본 원인**: `analyze_context` 의 assembly 감지가 **직근 부모만** 봤다.
```python
parent = self.contract_analyzer.find_parent_context(start_line)
if parent == "assembly":            # ← 직근 부모 하나
```
`find_parent_context` 는 자기를 감싸는 **가장 가까운** `{` 의 종류를 돌려준다. 그런데:
```solidity
function didLastOptionalReturnCallSucceed(bool callStatus) private pure returns (bool success) {
    assembly {
        let returnDataSize := returndatasize()     ← 부모가 "assembly" ✅
        if iszero(callStatus) {
            returndatacopy(0, 0, returnDataSize)   ← 부모가 "if"  ❌
            revert(0, returnDataSize)              ← 부모가 "if"  ❌
        }
        switch returnDataSize
        case 32 {
            success := iszero(iszero(mload(0)))    ← 부모가 "case" ❌
        }
    }
}
```
중첩 Yul 블록 안의 줄은 부모가 `"if"`/`"case"`/`"default"` 라 검사를 통과하지 못하고
ctx 가 `simpleStatement` 등으로 분류돼 **Solidity 파서**로 간다(`revert` 의 기대 토큰
집합이 `interactiveSourceUnit` 인 것이 결정적 증거).

★ F32 가 "닫는 `}` 줄 선할당" 을 고쳐 CFG 구성 오류(`No CFG node found`)는 없앴지만,
**컨텍스트 분류는 별개 문제**로 남아 있었다.

**수정**: 직근 부모 하나가 아니라 **괄호 체인을 위로 걸어올라간다**.
```python
_ASM_BOUNDARY = frozenset({"functionDefinition", "constructor", "fallback", "receive",
                           "modifier", "contract", "abstract contract", "interface",
                           "library", "struct", "enum", "event"})

def is_inside_assembly(self, line_number: int) -> bool:
    line = line_number
    for _ in range(64):                 # 중첩 깊이 상한 — 무한 루프 방어
        line = self._enclosing_open_brace_line(line, inclusive=False)
        if line is None: return False
        ctx = self.determine_top_level_context(self.sa.full_code_lines.get(line, ""))
        if ctx == "assembly":            return True
        if ctx in self._ASM_BOUNDARY:    return False
    return False
```
F27 에서 만든 `_enclosing_open_brace_line` 을 **반복 적용**하는 것이라 새 괄호 로직이 없다.
함수/컨트랙트/struct 경계를 만나면 중단한다 — 그 밖은 assembly 일 수 없다.

**효과**: `[ANTLR]` 12 → **4**. statement 증가 3건 —
`lib_SafeTransferLib.didLastOptionalReturnCallSucceed` **1 → 4**,
`lib_PRBMathSD59x18.log10` 1 → 2, `lib_PRBMathUD60x18.log10` 1 → 2. **감소 0건.**

**남은 `[ANTLR]` 4건 — 엔진이 아니라 소스/전처리 쪽이다**
| 파일 | 건수 | 원인 | 수정 방향 |
|---|---:|---|---|
| `ERC1155Upgradeable` | 3 | `uint256[] memory array = new uint256[](1);` — **`array` 가 intent 문법의 예약어**(`ceil`/`floor` 와 같은 부류, F17(B)) | 소스 rename `array` → `_array`. **문법은 불변**(사용자 결정: IntentChecker 제출본) |
| `TickMath` | 1 | `else r = ratio << (127 - msb);` — **중괄호 없는 else** 가 한 줄에 문장까지 갖고 있다 | 전처리(`tools/preprocess_contraction.py`)에 `else <stmt>;` → `else {` / `<stmt>;` / `}` 확장 추가 |

⚠ 이 4건은 **Yul 과 무관**하다. Yul 커버리지 문제로 분류돼 있던 것이 F27·F32·F40 으로
전부 걷히고 나니 남은 것은 예약어 충돌과 전처리 결손이었다.

---

## F41. 소스 수정: `array` 는 intent 문법의 예약어

**파일**: `Dependencies/contracts/ERC1155Upgradeable.sol` (3줄)
**Guardian 상태**: ❗**동일 파일에 존재**.

```
[ANTLR] 1:17 mismatched input 'array' expecting Identifier
[ANTLR] 1:0  extraneous input 'array' expecting {...}
[ANTLR] 1:7  extraneous input 'array' expecting {...}
```

`Parser/Solidity.g4:337` 의 **디버그 값 문법**에 리터럴 키워드로 박혀 있다:
```antlr
| 'array' '[' ( signedNumberLiteral (',' signedNumberLiteral)* )? ']'   # DebugIntArray
```
ANTLR 은 파서 규칙의 리터럴 문자열을 **암묵적 토큰**으로 만들고 그것이 `Identifier` 보다
우선하므로, `array` 는 **식별자가 될 수 없다.** `ceil`/`floor`(F17(B))와 같은 부류다.

**수정**: 이 리포 관례대로 `_` 를 붙인다. **문법은 불변**(사용자 결정: IntentChecker 제출본).
```solidity
function _asSingletonArray(uint256 element) private pure returns (uint256[] memory) {
-    uint256[] memory array = new uint256[](1);
-    array[0] = element;
-    return array;
+    uint256[] memory _array = new uint256[](1);
+    _array[0] = element;
+    return _array;
}
```

**전수 조사**: 문법의 리터럴 키워드 중 흔한 식별자가 될 수 있는 것들
(`array`·`ceil`·`floor`·`interval`·`percentOf`·`symbolicAddress` …)을
Dependencies + RQ1 + Dataset 전체에서 조회한 결과 — **식별자로 쓰인 것은 이 3줄뿐**이고
나머지 히트(`ceil` 2·`floor` 2·`interval` 1·`array` 6)는 **전부 주석 안**이었다.

**효과**: `[ANTLR]` 4 → 1.

---

## F42. 전처리: 중괄호 없는 `else`

**파일**: `tools/preprocess_contraction.py`(`_expand_single_line_else` 신규) +
`Dependencies/libraries/TickMath.sol` 1곳
**Guardian 상태**: ❗원본(`SolidityGuardian/temp/preprocess_contraction.py`)에도 없다.

```
[ANTLR] 1:0 extraneous input 'else' expecting {...}
```
```solidity
if (msb >= 128) {
    r = ratio >> (msb - 127);
}
else r = ratio << (127 - msb);        // ← 한 줄에 else + 문장
```
슬라이서/분류기는 `else` 가 **블록을 여는 것**을 전제한다.

**수정**: 전처리에 확장 단계 추가.
```python
RE_SINGLE_ELSE = re.compile(
    r"^(\s*)(\}\s*)?else(?![A-Za-z0-9_])(?!\s*(?:\{|if(?![A-Za-z0-9_])))\s*(?P<stmt>.+;)\s*$")
```
`else stmt;` → `else {` / `    stmt;` / `}`

★ **`} else {` / `else {` / `else if` 는 절대 제외**한다 —
`(?!\s*(?:\{|if(?![A-Za-z0-9_])))` 로 명시 배제. 경계 케이스 실측:
| 입력 | 결과 |
|---|---|
| `else r = ratio << (127 - msb);` | ✅ 확장 |
| `} else r = 1;` | ✅ 확장 (선행 `}` **보존** — 슬라이서의 `closeBefore` 경로를 그대로 타야 한다) |
| `} else {` · `else {` · `} else if (x > 0) {` · `else if (x == 1) y = 2;` · `elsewhere = 3;` | ✅ 제외 |

**전수 조사**: Dependencies + RQ1 + Dataset 에서 확장 대상 **85건**, 제외된 `else` 줄
2,500여 건(`} else {` 2051 · `} else if (...) {` 등) — **오탐 0**.
그중 **Dependencies + RQ1 자산에는 `TickMath.sol:129` 한 건뿐**이라 그 파일에만 적용했다
(Dataset 84건은 미적용 — 필요해지면 별도 판단).

⚠ **`expand_single_line_blocks()` 를 통째로 부르지 않고 `_expand_single_line_else` 만
직접 호출했다** — F25 에서 통째 호출이 `if`/`unchecked` 확장까지 돌려 RQ1 자산 12개를
의도 없이 변형시킨 사고가 있었다. **필요한 단계만 부른다.**
⚠ 쓰기 시 줄끝이 LF 로 바뀌어 CRLF 로 복원했다(리포 관례). `git diff --stat` 로 의도한
3줄만 바뀐 것을 확인했다.

⚠ **미처리로 남긴 형태**: 중괄호 없는 **`else if <stmt>;`** (Dataset 에 16건).
`else if` 는 슬라이서의 `interactiveIfElseUnit` 경로가 전담하므로 별도 판단이 필요하다.
Dependencies + RQ1 에는 **0건**.

**효과**: `[ANTLR]` 1 → **0**.

---

## F43. `refine_intervals_for_comparison` 의 `<` / `>` 가 **반대쪽 경계**를 쓴다

**파일**: `Interpreter/Semantics/Refine.py:400-414`
**Guardian 상태**: ❗**동일 코드가 존재할 가능성이 매우 높다**(이 함수는 IntentChecker 시절부터
있던 것). 포팅 시 **최우선** — 조용한 과소근사라 결과만 보면 알 수 없다.

### 증상

예외가 **전혀** 나지 않는다. `[ERR]` 0 · `[ANTLR]` 0 과 아무 문제 없이 공존했다.
대신 **루프 본문이 영원히 해석되지 않았다**:

```solidity
uint256 acc = 0;
uint256 i = 0;
while (i < n) {          // ← n 이 비-싱글톤 구간이면 true 분기가 항상 infeasible
    acc = acc + i;       //   → 이 두 줄의 노드 state 가 영원히 `{}`
    i = i + 1;
}
```
계측 결과:
```
[FEASIBLE?] want=True  -> False   env={n: [0,10], i: BOTTOM}
[FEASIBLE?] want=False -> True    env={n: [0,10], i: [0,0]}
[FIXPOINT ENTER] while_cond_10 … transfer_function 1회 … [FIXPOINT EXIT]
```

### 원인

```python
if op == '<':
    if B.min_value != NEG_INFINITY:
        A = clamp_max(A, B.min_value - 1)        # ← B.min 을 쓴다
    if not A.is_bottom() and A.max_value != INFINITY:
        B = clamp_min(B, A.max_value + 1)        # ← A.max 를 쓴다
```

`a < b` 가 **성립 가능한** 값만 남기는 올바른 정제는
```
a ≤ B.max − 1     (b 가 될 수 있는 최댓값보다 작아야 한다)
b ≥ A.min + 1     (a 가 될 수 있는 최솟값보다 커야 한다)
```
코드는 둘 다 반대쪽 경계를 쓴다. `>` 도 대칭으로 같은 실수(`B.max+1`/`A.min−1` ←→ `B.min+1`/`A.max−1`).

★ **왜 지금까지 안 드러났나**: 우변이 **리터럴**이면 `B.min == B.max` 라서 공식이 **우연히 맞는다.**
`x < 100` 같은 비교는 전부 정상이었고, `while (i < n)` 처럼 양변이 변수일 때만 깨진다.

| A | B | op | 수정 전 | 올바름 |
|---|---|---|---|---|
| `[0,0]` | `[0,10]` | `<` | **⊥** | `[0,0]`, `[1,10]` |
| `[0,5]` | `[3,7]` | `<` | `[0,2]` (a=3,4 유실) | `[0,5]`, `[3,7]` |
| `[5,5]` | `[0,10]` | `>` | **⊥** | `[5,5]`, `[0,4]` |
| `[0,10]` | `[5,5]` | `<` | `[0,4]` ✅ | `[0,4]` (싱글톤 → 우연히 맞음) |

방향은 **과소근사(too narrow)** = feasible 상태를 버린다. 추상해석에서 이것은 **unsound** 하며
디버거에서는 거짓 ⊥ / 죽은 분기 오판 / 루프 미진입으로 나타난다.

### 수정

```python
if op == '<':
    if b_iv.max_value != INFINITY:
        A = clamp_max(A, b_iv.max_value - 1)
    if a_iv.min_value != NEG_INFINITY:
        B = clamp_min(B, a_iv.min_value + 1)
    return both_bottom_if_either(A, B)

if op == '>':
    if b_iv.min_value != NEG_INFINITY:
        A = clamp_min(A, b_iv.min_value + 1)
    if a_iv.max_value != INFINITY:
        B = clamp_max(B, a_iv.max_value - 1)
    return both_bottom_if_either(A, B)
```

- ⚠ **양쪽 모두 원본(`a_iv`/`b_iv`)으로 제약한다.** 한쪽 정제 결과를 반대쪽 제약에 다시 쓰면
  중복 적용돼 과도하게 좁아진다(수정 전 코드가 그랬다).
- `<=` / `>=` 는 `<`/`>` ∪ `==` **합성**이라 자동으로 같이 고쳐진다.
  검산: `A<=B` → `[A.min, min(A.max, B.max)]`, 서로소면 양쪽 ⊥. `==`/`!=` 는 손대지 않았다.
- `both_bottom_if_either` 신규: 한쪽이 ⊥ 면 조건 자체가 성립 불가이므로 양쪽 ⊥.
  `_branch_feasible` 이 어차피 False 를 주므로 feasibility 판정은 같고, **화면에 ⊥ 로 보이는
  변수 집합**만 달라진다(도달 불가 env 라면 양쪽 ⊥ 가 정직 — `_set_bottom_env` 와 같은 관점).
  분리 가능한 하위 결정이며, 빼도 F43 본체는 성립한다.

### 검증

- 단위 13 케이스(unsigned) + 2 케이스(signed) 전부 기대값 일치.
  *검증 중 **내 기대표 3개가 틀렸고 구현이 맞았다**— `clamp_min/clamp_max` 는 구간을 좁히는
  방향으로만 작동하므로 `A.min` 이 3→1 로 넓어질 수 없다.*
- **구조 무변화**: pkl 드라이런 함수 974→974, statement 1288→1288, 사라진 함수 0,
  statement 감소 0, `[ERR]`/`[ANTLR]`/`[UPDATE_CODE ERR]` 전부 0.
  → **pkl 재생성 불필요**(pkl 은 CFG 구조만 담고 추상값은 담지 않는다).
- **`temp/` 회귀 19종 / 119 checks 전부 pass, FAIL 0.** 출력이 바뀐 것은 전부 **개선**:

| | 변화 |
|---|---|
| `test_back_edge` | `loopDelta` 가 비로소 나옴. `s: [100,100]`(루프 미진입 잔존값) → 건전한 widening 값 |
| `test_delete_cfg` | `b: [None,None]` = **거짓 ⊥** → `b: [2,2]` 로 교정 |
| `test_safety_net` | 도달 노드 증가(`b` 값 5~6개 → 7개). `update` vs `fresh` 일치 불변식 유지 |

### 영향 범위

양변이 모두 비-리터럴인 `<`/`>`/`<=`/`>=` 비교 수 (트리거 **상한** — 변수가 해석 중
싱글톤이면 영향 없음):

| | 비교 | 한쪽 리터럴 | ★양변 비-리터럴 | 그중 루프 헤더 |
|---|---|---|---|---|
| Dependencies | 344 | 209 | **135** (32파일) | 19 / 26 |
| RQ1 | 1038 | 539 | **499** (122파일) | **82 / 92** |
| Dataset | 13087 | 6191 | **6896** (1090파일) | 1274 / 1389 |

**RQ1 루프 헤더 92개 중 82개가 비-리터럴 경계** = RQ1 의 루프는 사실상 전부 본문을 해석하지
않은 채 평가되어 왔다. 논문 평가 수치에 직접 영향이 있다.

---

## F44. `loopDelta` 가 **narrowing 이전** 스냅샷을 쓴다

**파일**: `Interpreter/Engine.py` (`fixpoint` 의 loopDelta 기록부)
**Guardian 상태**: 미확인(loopDelta 자체가 IQDebug 쪽 기능).

`loopDelta` 를 `join_node.fixpoint_evaluation_node_vars` 로 계산했는데, 그 속성은
**widening 루프 안에서만** 갱신된다(`fixpoint` 의 `while WL` 블록). narrowing 패스는 갱신하지
않으므로 **수렴 전 값**이고, widening 이 넣은 `INFINITY` 가 그대로 남아 `[0,inf]` 로
직렬화됐다. 같은 변수를 노드 state 로 보면 `[0,2^256-1]` 이라 **한 화면에 두 표기가 섞인다**
(N3 배선 중 발견).

**수정**: 최종 상태인 `join_node.variables` 를 우선 사용한다.
```python
fixpoint_env = getattr(join_node, "variables", None) or getattr(
    join_node, "fixpoint_evaluation_node_vars", None)
```
join 노드의 transfer 는 identity 라 in == out 이고, `transfer_function` 이 narrowing 패스의
매 방문마다 `.variables` 를 써 준다. narrowing 이 join 에 도달하지 못한 경우 `.variables` 는
마지막 widening 값이므로 **예전 동작과 같아진다 — 별도 폴백이 필요 없다.**

**효과**: `temp/test_back_edge`·`test_delete_cfg` 의 loopDelta 가 노드 state 와 일치.
⚠ **`inf` 누출 자체는 안 고쳤다** — 아래 "아직 안 고친 것" 참조. F44 는 "delta 가 자기
join 노드와 일치한다" 는 **관계**를 고친 것이고, 그 join 노드가 `inf` 를 들고 있으면 delta 도
`inf` 로 나온다(그게 정합이다).

---

## F45. widening 이 타입 최댓값이 아니라 `INFINITY` 를 넣는다 (⑯)

**파일**: `Domain/Interval.py` — `IntegerInterval.widen`/`is_top`, `UnsignedIntegerInterval.widen`
**Guardian 상태**: ❗**동일 코드가 존재할 가능성이 높다**(도메인은 IntentChecker 시절 것).

`widen()` 이 자라는 쪽 경계에 `±INFINITY`(= `float('inf')`)를 넣었다. 반면 산술
(`add` → `_fit_unsigned`)은 **타입 최댓값**으로 맞춘다. 그래서 **같은 ⊤ 가 경로에 따라 다르게**
직렬화됐다 — 실측(`if`-in-`for` 의 `acc`):

```
for_join_7 / for_cond_7 / for_body_7 / for_exit_7 / if_*   acc = [0,inf]        ← widening 경유
Assign_9 / Assign_12 / Return_13                           acc = [0,1157…935]   ← add 경유
```

표시만의 문제가 아니다 — F43 의 가드 `if b_iv.max_value != INFINITY:` 가 `inf` 를 보면
**정제를 건너뛴다**. `inf` 는 "상한을 모른다" 는 뜻이라 건전하지만, `uint256` 변수의 상한은
**알고 있다**(`2^256-1`). 즉 `inf` 는 타입 정보를 버리는 표현이고, 애초에 `uint256` 의 추상값
집합에 `inf` 는 **없다**.

★ 코드 스스로 이미 타입 경계 쪽이었다. `IntegerInterval.top()` 은
`[-2^(bits-1), 2^(bits-1)-1]` 을 돌려주고 주석에 *"내부적으로는 '이론적 -∞, +∞' 대신 실제
비트 범위를 사용합니다"* 라고 적혀 있다. **`widen()` 만 예외**였다.

**수정 (사용자 결정: (b) 타입 있는 구간은 `inf` 를 갖지 않는다)**
```python
# UnsignedIntegerInterval.widen
type_max = 2 ** self.type_length - 1
new_max = type_max if self.max_value < current_interval.max_value else self.max_value

# IntegerInterval.widen
type_min = -2 ** (self.type_length - 1)
type_max = 2 ** (self.type_length - 1) - 1
```

**부수 효과로 기존 불일치 하나가 같이 고쳐졌다.** `UnsignedIntegerInterval.is_top()` 은 처음부터
`max == 2^bits-1` 을 검사했으므로 **widen 된 unsigned 구간은 자기 `is_top()` 이 False** 였다.
반대로 `IntegerInterval.is_top()` 은 `±INFINITY` 를 봤으므로 `IntegerInterval.top(256).is_top()`
이 **False** 였다. 후자도 타입 경계로 맞췄다. (둘 다 **호출자가 0개**인 죽은 코드라 증상은
없었지만, 쓰기 시작하면 바로 틀리는 자리였다.)

`narrow()` 의 `if cur_max == INFINITY: cur_max = new_interval.max_value` 특례는 **죽은 가지**가
되지만 남겨 뒀다 — 뒤의 `min(...)` 이 같은 일을 하므로 동작은 동일하고, `theoretical_top()`
경유로 `inf` 가 들어올 여지가 남아 있어 방어로 둔다. `Refine` 의 `!= INFINITY` 가드도 같은
이유로 남겼다(이제 발동하지 않음 = 정제가 항상 적용됨, 의도한 정밀도 이득).

**참조 전수**: `INFINITY` 는 **3파일 17곳**뿐이다 — `Domain/Interval.py` 12, `Engine.py` 1(주석),
`Refine.py` 4(F43 가드). widening 호출처는 `Engine.py` 2곳, narrowing 1곳, **전부 `fixpoint` 안**.

**효과**: 직렬화 출력에서 `inf` **완전 소거**. `temp/` 119 checks pass, `inf` 포함 출력 0건.

---

## F46. `traverse_loop_nodes` 가 **모든** `loop_exit_node` 에서 절단한다 (⑮)

**파일**: `Interpreter/Engine.py` — `traverse_loop_nodes`, `is_node_in_loop`
**Guardian 상태**: ❗동일 코드 존재 가능.

```python
for succ in G.successors(cur):
    if getattr(succ, "loop_exit_node", False):   # ← 어느 루프의 exit 이든 절단
        continue
```
루프가 하나면 맞다(그 플래그가 달린 건 자기 탈출 노드뿐). **중첩이면 내부 루프의 exit 에도 같은
플래그가 달려 있고, 그게 바깥 루프 본문 한가운데 놓인다.** 그래서 바깥 `loop_nodes` 에서
`for_incr_*`(증감)과 back edge 가 빠지고, `fixpoint` 의 전파 조건
(`if succ not in loop_nodes: continue`)에 걸려 **바깥 fixpoint 가 1회만 돌았다**.

실측(`while`-in-`for`, 배선은 정상인데도): `while_exit_9` state `{}`, `for_incr_7` state `{}`,
`for_exit_7` = 진입 환경 그대로. ★ `n` 이 `[0,5]` → **`[0,0]`** 으로 오염됐는데 이건 **그 자체로는
올바른 정제**다(`i >= n` 이고 `i=[0,0]` 이면 `n` 은 0). 틀린 `i` 가 올바른 규칙을 타고 더 틀린
값으로 번진 것이다.

**수정**: **그 루프 자신의 exit**(자기 헤더의 False 엣지)만 절단점으로 본다.
```python
own_exit = {s for s in G.successors(loop_head)
            if G[loop_head][s].get("condition") is False}
...
    if succ in own_exit: continue
```
`is_node_in_loop` 의 선행 검사 `if loop_exit_node: return False` 도 제거했다 — 내부 루프의 exit 는
**바깥 루프 본문에 속한다**. (호출자 0개라 위험 없음.)

### 왜 flat 인가 — hierarchical 을 측정하고 버렸다

이 방식은 Bourdoncle 분류의 **flat(iterative) chaotic iteration** 이다. 대안인
**hierarchical(recursive)** — 안쪽 루프를 만나면 `fixpoint(inner)` 를 재귀 호출해 완전히
수렴시키고 통째로 한 걸음으로 쓰는 표준 기법 — 을 프로토타이핑해 **직접 비교했다**:

| | 수정 전 | **flat (채택)** | hierarchical |
|---|---|---|---|
| 빈 state 노드 | 2~4개 | **없음** ✅ | 없음 ✅ |
| `n` 오염 | `[0,0]` ❌ | `[0,5]` ✅ | `[0,5]` ✅ |
| 바깥 카운터 `i` | `[0,0]` ❌ | **`⊤`** ⚠ | `[0,5]` ✅ |
| 최외곽 `loop_delta` | **없음** ❌ | 생성 ✅ | 생성 ✅ |
| 2중 루프 시간 | 1.6s | **4.1s** | **47.4s** (14배) |
| 3중 루프 | 2.2s | **7.0s** ✅ | **크래시** |

hierarchical 크래시: `process_while_statement → reinterpret_from → fixpoint(재귀)` →
`ValueError: This 'j' is may be elementary variable but may not be declared`.
증분 빌드 **중간 상태**에서 재귀 fixpoint 가 아직 환경이 갖춰지지 않은 노드를 건드린다.
이 엔진은 **본문 한 줄 추가될 때마다** 전체를 다시 돌리므로 비용이
(줄 수) × (바깥 × 안쪽)으로 곱해지고, "quick" 디버거 정체성에 정면으로 반한다.

⚠ **flat 의 알려진 비용**: 바깥 루프 카운터가 ⊤ 로 남는다. **건전하다**(⊤ 는 과대근사).
원인은 안쪽 루프의 **back edge 가 이미 widen 된 바깥 변수를 안쪽 join 으로 되돌려 넣기**
때문이고, 그래서 바깥 조건 노드의 refine(`i < n` → `[0,4]`)이 매 바퀴 무효화된다.
narrowing 예산(`N_MAX=30`)을 20배로 늘려도 **전혀 바뀌지 않았다** — 예산 문제가 아니다.
widening 범위를 "루프가 실제로 수정하는 변수" 로 좁히는 가설도 검토했으나 **기각**했다:
구간 도메인의 widening 은 componentwise 가 정의 그 자체이고, 더구나
`new_max = INFINITY if self.max_value < current.max_value else self.max_value` 라서
**값이 안 커지면 애초에 widen 되지 않는다**(사용자 지적).

**효과**: 중첩 루프에서 빈 state 노드 0, `n` 오염 해소, 최외곽 `loop_delta` 생성.

---

## F47. `build_for_statement` 가 `pre` 의 기존 후속을 잇지 않는다 (⑭) + join 무한루프

**파일**: `Analyzer/DynamicCFGBuilder.py` `build_for_statement`,
`Interpreter/Engine.py` `_run_worklist`
**Guardian 상태**: ❗동일 코드 존재 가능.

### (1) 배선

`build_for_statement` 는 **`EXIT` 이라는 이름의 노드 하나만** 찾아 옮겼다:
```python
for succ in list(G.successors(pre)):
    if succ.name == "EXIT":
        funcExit = succ; G.remove_edge(pre, succ); break
```
`build_while_statement` 는 같은 자리에서 `old_succs = list(G.successors(cur_block))` 로 **전부**
떼어 `exit_` 에 다시 붙인다. **빌더 간 불일치**였다.

최상위 `for` 은 슬라이서가 닫는 중괄호를 미리 만들어 두는 덕에 `pre` 의 후속이 **항상 `EXIT`**
이라 우연히 맞았다. `for` 이 **다른 블록 안에서** 열리면 `pre` 의 후속은 그 블록의
continuation(`if_join_*`, `for_incr_*` …)이므로 두 가지가 동시에 깨졌다:
① 떼지 않은 헌 엣지가 남아 **루프를 우회하는 경로**가 됐다
② `exit_` 가 **나가는 엣지 없는 sink** 가 됐다

`for`-in-`if` 실측 — 루프는 올바르게 `acc=⊤` 를 계산했는데 뒷 문장은 우회 경로의 `[0,0]` 만
받아 **`acc=[7,7]`**:
```
if_true_7        → ['if_join_7', 'for_init_8']   ← ★비조건 노드인데 후속 2개
for_exit_8   L10 → []                            ← ★나가는 엣지 없음(sink)
```

**수정**: while 쪽과 동일하게 `old_succs` 를 전부 떼어 `exit_` 로 옮긴다. `funcExit` 특례는
제거 — `EXIT` 은 가능한 후속 중 하나일 뿐이다.

### (2) ⑭ 가 드러낸 잠복 결함 — join 노드 지연 처리 무한루프

⑭ 적용 직후 `for`-in-`if` 가 **10분 넘게 멈췄다**. `faulthandler` 스택:
`Engine.py:923 _run_worklist` 의 join 지연 로직.

```python
if getattr(node, 'join_point_node', False) and preds:
    unvisited_preds = [p for p in preds if p not in visited]
    if unvisited_preds and len(work) > 0:
        work.append(node); continue
```
루프 헤더 처리는 `exit_node` 를 **`visited` 에 넣지 않는다**(successor 만 큐에 넣는다).
⑭ 로 `for_exit_8 → if_join_7` 이 복원되자 `if_join_7` 이 `for_exit_8` 를 **영원히 미방문
선행**으로 보고 재큐잉했고, `work` 에 같은 join 이 두 개 쌓여 서로 `len(work) > 0` 을
만족시켜 빠져나오지 못했다. ⑭ 이전에는 그 엣지가 **끊겨 있었기 때문에** 가려져 있었다.

**수정**: 루프 처리 후 `visited.add(exit_node)`. `fixpoint` 가 그 env 를 이미 확정했으므로
(`exit_node.variables`) 뒤의 join 이 그대로 쓰면 된다.

### 검증

중첩 6형태 전부 `dangling` 0 · 비조건 노드 다중후속 0 · `acc=⊤`(루프 효과가 뒷 문장에 도달):

| 형태 | 수정 전 | 수정 후 |
|---|---|---|
| `for`-in-`for` | sink + 우회, `acc=[0,0]` | ✅ `acc=⊤` |
| `for`-in-`while` | sink + 우회, `acc=[0,0]` | ✅ `acc=⊤` |
| `for`-in-`if` | sink + 우회, **`acc=[7,7]`** | ✅ `acc=⊤` |
| `while`-in-`for` | 배선 OK, `acc=[0,0]`(F46) | ✅ `acc=⊤` |
| `if`-in-`for` · `for`+이후문장 | ✅ | ✅ |

pkl 드라이런 함수 974→974, statement 1288→1288, 감소 0, `[ERR]`/`[ANTLR]`/`[UPDATE_CODE ERR]` 0.
`temp/` 119 checks pass.

---

## F48. `for_init` 노드에 `src_line` 이 없다 (⑰)

**파일**: `Analyzer/ContractAnalyzer.py` (`process_for_statement` 의 init 노드 생성)
**Guardian 상태**: 동일 코드 존재 가능(단, 증상은 프론트 전용).

`for (uint256 i = 0; i < n; i++)` 의 init 노드는 **실제로 존재하고 실행되는 문장**(`i = 0`)인데
`src_line` 을 받지 못해 기본값 `-1` 로 남았다(`line_info` 는 헤더 라인을 **cond 노드에만**
매핑). N2 궤적에서 Step Forward 가 `for_init_8(L-1)` 에 착지해 **코드 창에 하이라이트되는 줄이
없었다** — 디버거에서 Step 을 눌렀는데 커서가 사라지는 셈.

**수정 (사용자 결정: "반복문이 시작되는 위치 자체가 단위")**
```python
init_node = CFGNode(f"for_init_{self.current_start_line}",
                    src_line=self.current_start_line)
```
⚠ `line_info[line_no]["cfg_nodes"]` 에는 **넣지 않았다** — 그쪽은 intent 부착·삭제 수술이 쓰는
"라인 → 대표 노드" 매핑이라 cond 하나만 유지해야 한다. `canBreakpointOnLine(for 줄)` 은
지금도 `true` 이므로 변화 없다(init·cond 둘 다 금지 플래그가 아니다).

**효과**: `for_init_8(L-1)` → `for_init_8(L8)`. `for` 줄에서 한 번 멈춘 뒤 다음 step 이 루프를
접는다(N2).

---

## F49. `@IReturn` — 캐스트 경로 대칭화 + view/pure 게이트

**파일**: `engine_api.py`(`_derive_debug_slots`), `Analyzer/ContractAnalyzer.py`(`process_ireturn_cast`)
**Guardian 상태**: `process_ireturn_cast` 는 동일 코드 존재 가능. 슬롯 도출은 IQDebug 전용.
⚠ **논문 미기재 결정(사용자)** — 구현에만 둔다.

### 배경: 왜 `@IReturn` 이 **옳은** 처리인가

`Vault(a).f()` 는 런타임 타입 검사가 **없는 순수 캐스트**다(`new Vault()` 만 실제 생성).
컴파일 결과는 "주소 `a` 에 `f()` 셀렉터로 external call" 이고 **`a` 에 배포된 임의의 코드가
실행**된다. 즉 **callee 코드는 타입 시스템이 결정하지 않는다** — `IFoo(a).f()` 와 의미론적으로
동일하다. 상태변수 `Vault v` 도 20바이트 주소 슬롯일 뿐이라 같다.

반면 **mutability 는 결정된다**:

| | 주소에 뭐가 있든 보장되나 |
|---|---|
| 부작용 없음 (view/pure) | ✅ **보장** — solc 가 **STATICCALL** 을 내므로 상태 변경 시 revert |
| 반환값 | ❌ 보장 안 됨 |

★ 그래서 "부작용 없음이 보장된 외부 호출의 **반환값만** 사용자 가정으로 받는다" 는 구조가
원리적으로 맞다. `@IReturn` 을 view/pure 로 제한한 것은 우연이 아니다.

★ **한때 "코드를 아니까 분석해야 한다" 는 방향을 검토했으나 기각**했다. 캐스트가 런타임
보장을 주지 않으므로 **코드를 아는 게 아니다**(유일한 예외는 `new Vault()` 로 만든 인스턴스).
분석으로 가려면 cross-contract 모드(state 변수 비공유, `msg.sender`/`msg.value` 재바인딩,
백로그 ⑪)가 필요하고 RQ1 8곳의 verdict 가 바뀌는데, 얻는 건 callee storage 가 통제 불가라
**⊤ 투성이 상태**뿐이다.

### (1) 캐스트 경로가 변수 경로와 비대칭이었다

| 형태 | 수정 전 | 수정 후 |
|---|---|---|
| `v.f()` (구체 컨트랙트 **변수**) | ✅ 슬롯 생성 | ✅ |
| `IFoo(a).f()` (인터페이스 **캐스트**) | ✅ 슬롯 생성 | ✅ |
| `Vault(a).f()` (구체 컨트랙트 **캐스트**) | ❌ **슬롯 없음 → ⊤, 제어 불가** | ✅ |

변수 경로가 이미 됐던 이유: `EnhancedSolidityVisitor` 의 contract 분기가 구체 컨트랙트 타입
변수에도 `typeCategory="interface"` + `interfaceName=<컨트랙트명>` 을 붙이고,
`_find_interface_name_for_var` 가 그걸로 판정한다. **캐스트 경로만** `interface_names` 로만
검사했다 — 슬롯 도출(`classify`)과 어노테이션 처리(`process_ireturn_cast`) **양쪽에서**.

**수정**: 양쪽 모두 "알려진 interface **또는** (라이브러리가 아닌) 알려진 contract" 로 넓혔다.
```python
# engine_api._derive_debug_slots
castable_types = {nm for nm, c in contract_cfgs.items()
                  if c is not None and not isinstance(c, LibraryCFG)}
#   classify: identifier / typeName 양쪽 분기에서 castable_types 도 IReturn 으로
# ContractAnalyzer.process_ireturn_cast
_is_callable_type = (interface_name in self.interface_names
                     or (_cfg is not None and not isinstance(_cfg, LibraryCFG)))
```
⚠ **라이브러리는 제외** — `Lib.f()` 는 external call 이 아니라 **하강 해석** 대상이다.

### (2) 슬롯 도출에 view/pure 게이트가 없었다

`ContractAnalyzer.process_ireturn_*` 는 `mutability not in ("view","pure")` 면 raise 하는데
그 게이트가 **슬롯 도출에는 없었다**. 그래서 비-view 외부 호출도 프론트에 슬롯으로 제시되고
**사용자가 값을 넣는 순간 거부**됐다.

**수정**: `ireturn_rejected(label)` 신규 — 스캔 시 `label → (선언타입, 함수명)` 을 기록해 두고,
`contract_cfgs[타입].get_function_cfg(함수)` 의 `mutability` 가 view/pure 가 **아님이 확정**된
슬롯만 제외한다.
- **fail-open**: 타입·함수를 못 찾으면 제외하지 않는다(기존에 되던 슬롯을 없애지 않는다).
- `mutability=None`(키워드 없음 = nonpayable)은 위 게이트와 **동일하게 거부**로 본다.
- 오버로드는 **하나라도 view/pure 면 허용**한다(어느 오버로드인지 슬롯 단계에서는 모른다).

### `address.call` 류 — 변경 없음 (확인만)

`Evaluation.py:1842-1860` 이 `transfer`/`send`/`call`/`delegatecall`/`staticcall` 을
`_cast_interface` 검사보다 **앞에서** 처리하고 `BoolInterval.top()` 을 돌려준다.
→ `@IReturn` 경로로 갈 수 없고, 슬롯 스캐너도 주소 변수를 인터페이스로 분류하지 않아
슬롯이 안 생긴다. **무조건 ⊤ 이고 그게 맞다**(사용자 결정) — 어느 함수가 호출되는지조차
모르고, 대개 `fallback` 일 텐데 fallback 은 보통 view/pure 가 아니다.

### 검증

`scratchpad/synth/XC.sol` — 6가지 호출을 한 함수에 넣고 `@IReturn` = 100 주입:

| 줄 | 형태 | 하강 | 결과 |
|---|---|---|---|
| L36 | `own()` internal | ✅ | `[3,3]` |
| L37 | `baseRate()` abstract contract 상속 | ✅ | `[10,10]` |
| L38 | `MathLib.twice(s)` library | ✅ | `[30,30]` |
| L39 | `v.rate()` 구체 컨트랙트 변수 | ❌ | `[130,130]` (@IReturn) |
| L40 | `Vault(a).rate()` 구체 컨트랙트 캐스트 | ❌ | **`[230,230]`** ← F49 로 새로 됨 |
| L41 | `IRate(a).rate()` 인터페이스 | ❌ | `[330,330]` (@IReturn) |

`scratchpad/synth/XC2.sol` — view/pure 게이트:
```
IReturn  m.peek()        ← view    ✅ 슬롯
IReturn  t.balanceOf()   ← view    ✅ 슬롯
         m.bump()        ← 비-view ❌ 제외 (예전엔 제시 후 거부)
         Mutable(a).bump() ← 비-view ❌ 제외
```

`temp/` 119 checks pass · FAIL 0. pkl 드라이런 함수 974→974, statement 1288→1288,
`[ERR]`/`[ANTLR]`/`[UPDATE_CODE ERR]` 0.

⚠ **전수 조사: 평가 자산에 캐스트 형태의 구체 컨트랙트 호출은 0건**이다
(Dependencies 0 / RQ1 0 — 캐스트 72건 전부 인터페이스). RQ1 의 구체 컨트랙트 접촉 8곳
(`collateralToken` ×5, `token` ×3 / `ERC20`·`ERC20Upgradeable`·`LPToken`)은 전부 **변수 형태**로
이미 슬롯이 생기던 것들이다. → **기존 평가 수치에 영향 없음.**

⚠ **단일 라인 블록은 여전히 미지원** — `function peek() public view returns (uint256) { return x; }`
처럼 본문이 한 줄이면 컨텍스트 추적이 깨져 `Contract context not found for line N` 으로 터진다.
`tools/preprocess_contraction.py` 의 `expand_single_line_blocks` 가 전처리로 풀어주는 것이 전제다.

---

## F50. callee 상태 보존 (N4 Step Into 의 엔진 측 전제)

**파일**: `Interpreter/Engine.py` — `update_statement_with_variables`,
`interpret_function_cfg`, `interpret_function_cfg_for_debug`, 신규 `_fcfg_owner` /
`_capture_callee_states`
**Guardian 상태**: IQDebug 전용 기능(Step Into). Guardian 에 포팅할 필요 없음.

### 왜 필요한가 — callee 상태는 계산 직후 **버려진다**

`interpret_function_cfg` 는 `_reset_node_vars` 로 callee 노드 env 를 백업·초기화하고 해석한 뒤
끝에서 `blk.variables = saved_vars` 로 **전부 원복**한다. freeze 는 **타겟 함수만** 한다
(`interpret_function_cfg_for_debug` 가 restore 직전에 fcfg 를 pickle).

실측으로 확인:
```
half(10), half(1000) 두 지점 / 총 4회 해석
fcfg 객체 id 집합 = {1987269306128}      ← 함수당 FunctionCFG 가 **하나**, 모든 호출 지점이 공유
호출 1~4 복귀 후 상태 = 4회 모두 동일     ← 전부 `_reset_node_vars` 가 백업한 baseline
```
즉 호출마다 **계산은 각각 되지만**(인자가 `related_variables[param].value` 로 바인딩됨)
**결과가 보관되는 자리가 없다**.

### 수정

1. **호출 지점 추적** — `update_statement_with_variables` 진입에서 `self._call_site_node = node`.
   ★ 여기 두는 이유: `_run_worklist` 는 평문 노드에 `transfer_function` 을 **쓰지 않고** 이
     함수를 직접 부른다. 두 경로가 모두 `node=` 를 넘기므로 이 한 곳이 유일한 공통 지점이다.
   ★ `interpret_function_cfg` 는 **진입 시점에 로컬로** 받아둔다 — callee 해석이 그 필드를
     자기 노드로 덮으므로, 중첩 호출에서도 각 프레임이 자기 호출 지점을 쥔다.

2. **복귀 직전 포착** — restore 루프 **앞에서** `_capture_callee_states(fcfg, _call_site_node)`.
   `_capture_debug_fcfg` 게이트가 켜졌을 때만 동작하므로 CLI/배치 경로는 무영향.

3. **키 = (호출 지점, 컨트랙트, 함수)**. 함수 이름만으로 잡으면 같은 callee 를 두 지점에서
   부를 때 마지막 호출이 앞을 덮는다. 실측 — `Math.mulDiv` 를 두 곳에서 부른 경우:

   | 호출 지점 | `a` | `b` | `d` | `p` |
   |---|---|---|---|---|
   | `_pct` 안 (`Return_20`) | `[1,100]` | `[10000,10000]` | `[1000,1000]` | `[10000,1000000]` |
   | `withdraw` 직접 (`VarDecl_27`) | `[0,5000]` | `[10,1000]` | `[10000,10000]` | `[0,5000000]` |

   같은 호출 지점의 반복 해석(fixpoint)은 **마지막 = 수렴값**이 남는 게 맞다.

4. **pickle 하지 않는다** — `{노드명: {변수: 직렬화값}}` 만 떠낸다. 프론트가 필요한 게 그것뿐이고
   구조(노드·엣지)는 호출 횟수와 무관하게 한 번만 직렬화하면 된다. 전수 pickle 은 측정상
   evaluate 의 2%·1.5MB 였지만 **호출 수에 선형**으로 늘어난다(측정: callee 2개·루프 1개 함수에서
   99회 하강).

5. **소속 컨트랙트 역색인** (`_fcfg_owner`) — `FunctionCFG` 에는 소속 컨트랙트 역참조가 **없다**
   (`ContractCFG.contract_name` 만 있다). 의존성 CFG 는 이미 pkl 로 피클돼 있어 필드를 추가해
   줄 수도 없다. 그래서 `contract_cfgs` 를 런타임에 훑어 `id(fcfg) → (컨트랙트, 함수)` 색인을
   만든다. `interpret_function_cfg_for_debug` 진입마다 무효화 → evaluate 1회당 최대 1회 구축.

### 계약 (`engine_api` ↔ `types/result.ts`, 동시 변경)

```python
NodeFlag = Literal[..., "call", "opaque"]
@dataclass
class CalleeRef:
    key: str          # "<호출노드>#<컨트랙트>.<함수>" (opaque 면 "")
    label: str        # "Math.mulDiv"
    enterable: bool
    contract / name / reason / start_line / end_line
CFGNode.callees: list[CalleeRef] | None
CfgWithStates.callees: dict[str, CFG]      # → AnalysisResult["callees"]
```

### 직렬화

- **enterable 목록은 실제로 하강한 것**(포착분)에서만 만든다 — 정적 스캔으로 추정하면
  "호출처럼 보이지만 하강하지 않은 것" 을 잘못 enterable 로 표시한다.
- **opaque 목록은 외부 호출 스캔**(`_external_call_nodes`)에서 만든다 — 외부 호출은 하강 자체가
  없어 포착되지 않는다. 판정은 **`_external_call_roots` 공용 헬퍼**로 `@IReturn` 슬롯 도출과
  **같은 규칙**을 쓴다(두 곳에 각각 적으면 조용히 어긋난다).
- callee CFG **에도** `node_callees` 를 넘겨 **중첩 Step Into** 가 되게 했다.
  ⚠ `node_callees` 는 **노드명** 키다. 노드명에 라인번호가 박혀 한 파일 안에서는 충돌하지
    않지만, pkl callee 가 다른 파일의 같은 라인번호 노드명을 가지면 이론상 겹친다(실무상 희박).
- callee 에는 **`loop_delta` 를 붙이지 않는다** — ledger 는 **라인 번호** 키라서 pkl callee 의
  라인이 사용자 파일 라인과 겹치면 엉뚱한 delta 가 붙는다.

### Step Into 지원 범위

| | Step Into |
|---|---|
| internal · 상속(`super`/`Parent.f()`/abstract) · library · `this.f()` | ✅ |
| 주소 간접 외부 호출 전부 | ❌ opaque + 사유 표시 (F49 참조) |

### ⊤ 비율 실측 — callee 가 타겟보다 **더 정밀하다**

"callee 는 ⊤ 가 많아 Step Into 가 무의미할 것" 이라는 우려를 재 봤다
(mapping 상태변수 + `require` 가드 + library + internal helper, 슬롯 4개 시드):

| | 변수칸 | ⊤ | ⊥ |
|---|---|---|---|
| 타겟 `withdraw` | 57 | **8 (14.0%)** | 0 |
| callee `_pct` | 30 | **0 (0.0%)** | 10 |
| callee `mulDiv` | 14 | **0 (0.0%)** | 0 |

구조적 이유 셋: ① 인자가 caller 에서 **이미 정제된 값**으로 바인딩된다 ② state 변수를 caller 와
**공유**한다(F36, 같은 storage) ③ 입력이 정밀하면 callee 안에서 새로 ⊤ 가 생길 이유가 없다.
⊤ 가 많아지는 건 **외부 호출**인데 그건 Step Into 대상이 아니다.

★ 그리고 Step Into 가 **새 정보**를 준다 — `_pct` 의 `if_true_17`(= `whole == 0` 가드)이 전부 ⊥
= **이 입력에서는 죽은 코드**다. 타겟 함수 화면에서는 한 줄 호출로만 보여 알 수 없다.

### 검증

`temp/` 119 checks pass · FAIL 0. pkl 드라이런 함수 974→974, statement 1288→1288,
`[ERR]`/`[ANTLR]`/`[UPDATE_CODE ERR]` 0. `tsc --noEmit` 0 errors.
회귀 출력 변화는 `smoke_engine_api` 의 결과 키 목록에 `callees` 가 추가된 것뿐(의도된 계약 확장).

**S4 프론트도 완료** (2026-10-01, `PROGRESS2.md §A.2` N4 참조) — `cursor` → Frame 스택,
`stepInto`/`stepOut`, Call Stack 패널, 프레임별 breakpoint, callee 소스 조달.
실측: `withdraw → _pct → Math.mulDiv` 3단 하강/복귀, 복귀 시 호출 지점 정확, opaque 는 이동 없음.
**엔진 측 잔여는 없다.** 재귀 감지·표시는 보류(백로그 ⑨), `MAX_CALL_DEPTH=16` 은 UI 안전 상한.

---

## 부수 도구

### `Dependencies/check_pkl.py` (신규)

pkl 건강검진. 모든 pkl 을 로드해 **본문이 빈 채 저장된 함수**를 보고한다.
재생성 로그의 `[ERR]` 는 *파싱/평가* 실패만 잡으므로, 결과물 쪽에서 한 번 더 확인한다.

⚠ 현재 판정 기준이 거칠다 — OpenZeppelin 훅(`_beforeTokenTransfer` 등 소스에서도 `{}`)과
modifier 를 "손상" 으로 오분류한다. **소스 본문과 대조하도록 개선 필요**(미적용).

---

## 아직 안 고친 것 — `[ERR]` **0** · `[UPDATE_CODE ERR]` **0** · `[ANTLR]` **0**

**전부 0이다** (2026-09-30 재생성). pkl 144 · 함수 974 · statement 1288.
각 단계에서 **statement 감소 0건 · 사라진 함수 0개**. 회귀 `temp/` 16종 188 checks 통과.

**`[ERR]` 이력**: 552 → 246(F3~F6) → 224(F10) → 212(F11,F12) → 198(F13) → 197(F14)
→ 138(F15) → 112(F16) → 76(F17) → 69(F18) → 54(자산 보강) → 49(F20) → 35(F21)
→ 35(F22, 기여 0) → 31(F23) → 28(F24) → 26(F25) → 24(기준선 재측정¹)
→ 22(F26) → 21(F27) → 17(F28) → 13(F30·F31·F33²) → 7(F32) → 6(F35) → 3(F29·F36) → **0**(F37).
F39·F40·F41·F42 는 `[ERR]` 0 을 유지한다(전부 오류가 안 나던 결함 / 파싱 결함).

**`[ANTLR]` 이력**: 212 → 192(F9) → 187 → 183 → 26(F27) → 12(F30) → 4(F40) → 1(F41) → **0**(F42).

¹ regen30 의 26 중 2건(`Qualified type ... not found in library`)은 **콜드스타트 전용**이었다
  (interface=Phase 1 이 library=Phase 2·3 보다 먼저 분석돼 첫 회차에만 `lib_*.pkl` 이 없다).
² 17 − 7(해소) + 3(F31 이 드러낸 ⊥ write-back = F36) = 13.

### ⚠ 카운터가 전부 0 이라는 것이 "다 고쳤다" 는 뜻은 아니다

**F43 이 그 산증인이다.** `[ERR]` 0 · `[ANTLR]` 0 상태에서 **모든 루프의 본문이 해석되지
않고 있었다.** `[ERR]` 은 던져진 예외만, `[ANTLR]` 은 파싱 실패만 센다. 오류 없이 조용히
틀리거나 미구현인 것:

| | 상태 |
|---|---|
| ⑭⑮⑯⑰ (2026-10-01 발견) | ✅ **전부 수정됨** → F47 · F46 · F45 · F48 |
| **중괄호 한 줄 블록** (`if (c) { stmt; }`) | 슬라이서 미지원. `tools/preprocess_contraction.py` 의 `expand_single_line_blocks` 가 전처리로 풀어주는 것이 전제 — 안 풀면 `Variable 'i' not declared` 로 터진다 |
| **flat fixpoint 의 바깥 카운터 ⊤** | 중첩 루프에서 바깥 루프 카운터가 ⊤. **건전**(과대근사)하지만 정밀. hierarchical 은 14배 느리고 크래시 → 기각(F46) |
| **⑬** Yul 제어흐름(switch/if/for) 미구현 | 분기 join 없이 **순차 적용** → unsound. (B) 방향 결정됨 |
| **⑨** 재귀 가드 없음 | 직접·상호 재귀에서 `RecursionError` + 노드 env 오염. **근본 해법 검토 중(보류)** |
| **F38** `expr_type` 구조적 채우기 | F14·F16·F39 의 우회로를 걷어내는 근본 수정 |
| **⑪** `_create_variable_object` 의 `ccf` 미사용 | cross-contract 하강 시 호출자 컨트랙트 기준으로 struct 정의 탐색 |
| **중괄호 없는 `else if <stmt>;`** | Dataset 16건 (Dependencies+RQ1 0건). `interactiveIfElseUnit` 경로라 별도 판단 |
| **Dataset 의 `else <stmt>;` 84건** | 전처리 단계는 준비됨, 적용은 미판단 |
| **navigation N4** | `PROGRESS2.md §A.2` 참조 (N1·N2·N3·N5·N6·N7 완료) |

### 알려진 별건 (백로그)

**① custom error 미구현** (문법 `*` 누락 + visitor 스텁 + ctx 분류 누락)
- `Parser/Solidity.g4:131` 의 `(errorParameter (',' errorParameter))?` 에 반복 `*` 누락 →
  **파라미터 0개 또는 정확히 2개만** 파싱. `eventDefinition`(L173)은 정상.
  같은 누락이 `assemblyFlagString`(L500)에도 있다. **파서 재생성 필요**.
- `visitErrorDefinition` 이 빈 스텁 → `visitBasicType(ctx, type_obj)` 에서 `TypeError`.
- `analyze_context` 의 contract/library 분기에 `error` 없음 → `stateVariableDeclaration` 오분류.
- 등록 자체는 불필요(`revert Foo(a,b)` 는 이미 동작).
- 규모: 선언 106건 중 19건 파싱 실패. **`Dependencies/` 안은 0건**.

**② 블록 주석이 슬라이서/분류기를 깨뜨린다 ★신규**
`/** ... */` NatSpec 이 뒤따르는 선언과 한 레코드로 합쳐져
`analyze_context` 의 `startswith(...)` 분류가 전부 빗나간다(IERC721 실측: `[ERR]` 12 + `[ANTLR]` 25).
문법엔 `COMMENT : '/*' .*? '*/' -> channel(HIDDEN)` 이 있으므로 **파서가 아니라 슬라이서 문제**다.
`Dependencies/` 에서는 해당 3파일의 주석을 지워 우회했지만,
**사용자가 에디터에 NatSpec 을 입력하면 같은 문제가 난다** — 도구 결함으로 남아 있다.
수정 후보: (a) `analyze_context`/`determine_top_level_context` 가 **주석을 제거한 사본**으로
분류하고 파싱은 원문으로(ANTLR 이 HIDDEN 처리) — 최소 변경 · (b) 슬라이서에서 제거.

**③ intent 문법의 `ceil`/`floor`/`array` 예약** — F17(B). 소스 rename 으로 우회.
**문법은 불변**(사용자 결정: IntentChecker 논문 제출본).

**④ C3 선형화 미준수** — 조상 탐색이 선언 순서. Solidity 는 `is A, B` 에서 B 가 더 파생.

**⑤ `check_pkl.py` 판정 기준 오류 (확정)** — `require` 만 있는 modifier 는 조건 노드로
모델링되어 `statements=0` 이 **정상**인데 손상으로 오분류한다(실측: `onlyGovernance` 는
nodes=7 + `cond_expr` 정상). 판정을 "소스에 본문이 있는데 statements=0" 으로 바꿔야 한다.

**⑥ Yul 커버리지** — `switch`/`case`, `returndatacopy`, `revert`, `success :=` 에서 `[ANTLR]`.
PRBMath* 두 파일이 `[ANTLR]` 157건(전체 187 중)을 차지한다.
`_BINARY_OPS` 가 `sgt`→`>`, `sdiv`→`/`, `sar`→`>>` 로 **signed 를 뭉갠다**.

**⑦ `scan_*` 3종이 비재귀** — `scan_type_aliases`/`scan_interface_names`/
`scan_file_level_structs` 가 `d.glob("*.sol")` 라 `contracts/112/`·`interfaces/5/` 를 놓친다.
`scan_file_level_enums`(신규)만 `rglob`.

**⑧ 기타**
- `Evaluation._literal_is_address` 에 길이 검사 없음.
- 일반 state variable 의 `bytes32 x = "plain"` 은 여전히 **원문 str**(F16 은 `constant` 한정).
- `_load_parent_pkls` 의 pkl 로드가 `except Exception: pass` — 실패가 조용히 묻힌다.
- ~~**⑭ 음수 `int` 상수의 부호가 사라진다**~~ → ✅ **F39 로 적용됨(2026-09-30)**. 아래는 원인 기록.
  ```solidity
  int256 internal constant MIN_SD59x18 = -578960446186580977117...819968;
  ```
  ```
  엔진: MIN_SD59x18 = UnsignedIntegerInterval([+578960446186580977117...819968])
  ```
  **부호가 사라지고 도메인도 Unsigned** 가 된다. 그래서 `abs` 의
  `require(x > MIN_SD59x18)` 이 `x > +578...968` 이 되는데 `int256` 최대값은 `578...967`
  이라 **항상 거짓** → `abs` 가 통째로 ⊥(도달불가)로 판정된다. **오류는 안 난다.**
  최소 재현(`int*` 상수 전부 해당):
  | 소스 | 현재 | 참값 |
  |---|---|---|
  | `int256 constant NEG = -5;` | `UnsignedIntegerInterval([2²⁵⁶-5])` | `[-5,-5]` |
  | `int256 constant POS = 7;` | `UnsignedIntegerInterval([7])` | `[7,7]`(도메인 틀림) |
  | `int256 constant NEG_BIG = -2²⁵⁵;` | `UnsignedIntegerInterval([2²⁵⁵])` | `[-2²⁵⁵,-2²⁵⁵]` |
  **원인**: `-5` 는 `UnaryPrefixOp('-')` + `LiteralExp(5)` 인데 리터럴에 **선언 타입이
  전달되지 않아** uint 로 평가된 뒤 단항 마이너스가 2의 보수로 감긴다. F16 주석이 이미
  경고했던 지점이다 — "`uint256` 으로 덮으면 `evaluate_literal_context` 가 모른다" 라서
  `int` 계열은 아예 안 채웠다.
  **수정안(프로토타입 검증 완료)**: `process_constant_variable` 에서 `et.startswith('int')`
  일 때 리터럴(단항 마이너스면 그 피연산자)에 `expr_type = 'int'` 를 실어준다 →
  세 케이스 전부 `IntegerInterval` 로 정확해진다.
  ⚠ 적용 전 확인 필요: `process_state_variable` · 지역변수 선언 · 대입에도 같은 문제가 있을
  가능성이 높다(F14 `_literal_retype` 가 일부만 커버). **F38 의 부분집합**이다.
- ~~**⑮ `find_parent_context` 는 직근 부모만 본다 → 중첩 Yul 블록이 Solidity 파서로 간다**~~
  → ✅ **F40 로 적용됨(2026-09-30)**. `is_inside_assembly` 가 괄호 체인을 위로 걸어올라간다.
  `[ANTLR]` 12 → 4.
- **⑫ 내부 함수 호출이 호출자 env 를 ⊥ 로 오염시킨다 ★★신규(2026-09-30) · 건전성/정밀도**
  ✅ **전파는 F36 으로 막았다.** 남은 것은 **⊥ 자체가 왜 생기는가** = 위 ⑭(F39).
  아래는 원인 분석 기록으로 남긴다.
  `Engine.interpret_function_cfg` 는 ① callee 에 **caller env 를 통째로 얹고**(callee 파라미터만 제외)
  ② callee exit env 를 **caller 에 되쓴다** — 그런데 **⊥ 검사가 없다**.
  ```python
  # ① 진입
  start_block.variables = copy(fcfg.related_variables)
  for k, v in caller_env.items():
      if k in param_names: continue
      start_block.variables[k] = copy_single_variable(v)     # caller 지역변수까지 들어간다
  # ② 복귀
  for k, v in _exit_env.items():
      if k in _callee_params: continue
      if k in caller_env:
          ...
          elif hasattr(cv, "value"): cv.value = v.value      # ⊥ 이 그대로 덮어쓴다
  ```
  실측(`PRBMathSD59x18.powu`): `uint256 absX = uint256(abs(x));` **한 줄이 파라미터 `y` 를 ⊥ 로
  만든다**(`y` 는 그 줄에 등장조차 하지 않는다). **함수 ENTRY 노드까지 오염된다.**
  ```
  L327 이후  ENTRY  x=IntegerInterval([-578960...])  y=UnsignedIntegerInterval([0, 115792...])
  L328 이후  ENTRY  x=IntegerInterval([-578960...])  y=UnsignedIntegerInterval(BOTTOM)  ★
  L332       fixpoint: _branch_feasible(True)=False, (False)=False  → 양쪽 분기 도달불가
             → fixpoint 의 exit-env 계산(`Engine.py:~630`)이 `exit_env=None` → `for_exit.variables = {}`
  L340       빈 env → "absResult ... may not be declared"  (잔여 3건)
  ```
  `abs` 는 `unchecked { require(x > MIN_SD59x18); result = x < 0 ? -x : x; }` 다 — 그 안에서
  ⊥ 이 생기는 것 자체가 오판일 가능성이 있다(decision #42 의 BOTTOM 전파와 맞물림).
  **오류 3건보다 파급이 훨씬 크다** — 오염된 env 는 대부분 **오류 없이** 조용히 pkl 로 저장된다.
  수정 후보: **(다)** `abs` 내부에서 ⊥ 이 생기는 원인부터 확인 → **(가)** write-back 에서 ⊥ 를
  거른다 → **(나)** callee 로 넘기는 env 를 state+인자로 좁힌다(지금은 caller 지역변수까지 들어가
  이름 충돌로 오염된다). **(다) 부터 볼 것** — ⊥ 자체가 오판이면 write-back 은 죄가 없다.
- **⑬ Yul 제어흐름 미구현 + 순차 적용 = 건전하지 않다 ★★신규(2026-09-30)**
  `EnhancedYulVisitor` 의 `visitYulSwitchStatement`/`visitYulIfStatement`/`visitYulForStatement` 가
  전부 `return None`("현재 미지원"). 그런데 슬라이서가 줄 단위로 자르므로 `case`/`default` 줄이
  **독립 Yul 문장으로 파싱되어 대입이 순차 적용**된다 — 분기 join 이 아니라 **마지막이 이긴다.**
  ```solidity
  switch x / case 1 { r := 100 } / case 2 { r := 200 } / default { r := 7 }
  참값   r ∈ {100, 200, 7}
  분석기 r = [7, 7]        ← 싱글턴. 100·200 을 포함하지 않음 → **unsound**
  ```
  `@Post r == 7` 이 `x == 1` 에서 실제로는 위반인데 **Satisfied** 로 보고된다.
  **진행 방향 = (B)**(사용자 결정, 2026-09-30): assembly 블록을 **한 트리**로 Yul 비지터에 넘겨
  `visitYulSwitchStatement` 가 분기 CFG + join 을 만든다. 사용자가 지적한
  "`case 1 { ... }` 는 사실 `case 1 {`/`}` + 본문 2개" 라는 분해가 바로 그 CFG 구성이다
  (슬라이서에서 쪼개면 경계는 생기지만 join 이 없어 순차 적용이 남는다).
  준비 상태: `yulUnit : yulStatement* EOF` 최상위 규칙과 자식을 순서대로 도는 `visitYulBlock` 이
  이미 있다. 대가: 증분 편집 단위가 블록 전체가 되고 assembly 내부 라인-레벨 intent 는 불가
  (사용자: assembly 에 intent 를 다는 것은 원래 어렵다 — 허용).
  최소 안전판(구현 전 임시): 미지원 제어흐름을 만나면 그 블록이 건드리는 변수를 ⊤ 로 올린다.
- **재귀 가드가 아예 없다 ★신규(2026-09-30)** — `function f() { f(); }` 나 상호재귀
  (`ping`→`pong`→`ping`)가 들어오면 `evaluate_function_call_context` →
  `interpret_function_cfg` → `_run_worklist` → `_interpret_func_call` → … 가 종료 조건 없이
  돌아 Python 재귀 한도에서 `RecursionError` 로 죽는다. `except RecursionError` 도 없다.
  **두 리포 모두 없고, Guardian git 이력 295 커밋 전수 조회에서도 0건**(`call_stack`,
  `active_calls`, `in_progress`, `RecursionError`, `setrecursionlimit`, `recursion_guard`,
  `_calling`, `_visiting` — `git log -S` 전부 0).
  더 나쁜 것: `_reset_node_vars(fcfg)`(`Engine.py:814`)가 **그 함수 CFG 의 모든 노드 env 를
  `{}` 로 비우므로**, 재진입하면 바깥 워크리스트가 순회 중인 노드를 안쪽 호출이 밀어버린다
  — 한도에 닿기 전에 결과가 오염된다. 표준 처리는 호출 스택 집합 + 재진입 시 ⊤ 반환이고,
  `interpret_function_cfg` 진입부가 유일한 삽입 지점이다.
  ⚠ 의존성 코퍼스에는 재귀 함수가 없어 잔여 17건과 무관하다. F26 이 entry env 를 되살리면
  상호재귀가 **이 벽에 닿게** 되지만(합성 `Mutual2.sol`), 직접 재귀는 F26 이전에도 동일하게
  터진다(합성 `SelfRec.sol` — 패치 유무 무관). 즉 F26 이 만든 결함이 아니다.
- **`end_file_level_struct()` / `end_file_level_enum()` 이 아무 데서도 호출되지 않는다**
  — 한번 열린 `_current_file_struct` / `_current_file_enum` 이 영영 안 닫힌다.
  닫는 `}` 는 `_should_trigger_analysis` 가 걸러 `analyze_context` 까지 오지 않는다.
  지금은 멤버 판정이 `find_parent_context` 도 같이 보기 때문에 **우연히** 동작한다.
- **`_create_variable_object(type_obj, var_name, ccf)` 의 `ccf` 가 미사용** — 내부적으로
  `get_full_struct_enum_defs()` → `self.current_target_contract` 를 본다. 그런데
  `interpret_function_cfg` 는 cross-contract 하강 시 `current_target_function` 만 저장·복원하고
  `current_target_contract` 는 건드리지 않는다. 다른 컨트랙트 함수를 하강 해석할 때
  **호출자 컨트랙트 기준으로** struct 정의를 찾는다. `ccf` 를 실제로 쓰도록 정리할 것.
- 배열 읽기 경로에 `.elements[idx]` 직접 접근이 아직 `Evaluation` 4곳 / `Update` 3곳 남아 있다
  (F21 에서 3곳만 정리). `DebugInitializer` 의 `isDynamicArray` 분기 패턴으로 통일 검토.


---

## F51. 미지정 상태변수·환경값 = ⊤ (2026-10-20, 사용자 승인)

- **증상**: 지정하지 않은 상태변수가 Solidity 기본값 0(또는 initializer 값)으로, `block.timestamp`·`msg.value` 등이
  [0,0] 으로 시작했다. "막 배포된 컨트랙트" 라는 특정 시나리오를 고른 것이라 임의 호출 직전 상태(pre-state)로는 틀리다.
  IntentChecker 원고(debug 어노테이션은 "otherwise unknown" 값을 대체, 제약이 없으면 ⊤)와도 어긋났다.
- **수정**: `ContractAnalyzer.process_state_variable` — elementary 상태변수(initializer 유무 무관) = 타입 ⊤
  (`VariableEnv.top_from_soltype`), 고정 길이 numeric 배열 원소 ⊤(`initialize_default_by_base_type`), initializer 는
  CFG 기록용으로만 보존. `StaticCFGFactory._create_global_variables` — `block.*`·`tx.gasprice`·`msg.value` = uint256 ⊤.
  `ContractAnalyzer._narrow_msg_value_for_nonpayable` — non-payable 함수 진입 환경의 `msg.value` = [0,0] (EVM 강제).
  constant/immutable·로컬·named return 은 그대로.
- **남은 것**: 동적 배열 len 0 / `_sync_constructor_state`(생성자 결과를 상태변수 노드에 반영) / 무초기화식 immutable 미확인.

## F52. 정상 종료 상태 = EXIT 선행 ∪ RETURN 선행 (2026-10-21, 사용자 승인)

- **증상**: 빌더는 `return` 문을 RETURN sink 로 보내고 EXIT 로 잇지 않는다. 그런데 `Engine._force_join_before_exit` 는
  EXIT 선행만 join 했다 → (1) `return` 으로 끝나는 callee 의 **상태 변경이 호출자에 전달되지 않음**
  (`s = 5; return 1;` 뒤 호출자에서 `s` = 0), (2) 조기 return 만 가능한 callee(PRBMath.mulDivFixedPoint)는 EXIT 가 ⊥ →
  호출자 경로 사망(16_H_04 결과 없음, 16_H_06 Unreachable). 검증 계층 `_get_post_exit_node` 는 RETURN·EXIT 중
  **하나만** 골라 섞인 함수에서 @Post 가 한쪽 경로를 빠뜨렸다.
- **수정**: `Engine.normal_completion_preds`(EXIT 선행 ∪ RETURN 선행) 신설. `_force_join_before_exit` 는 그중 ⊥ 아닌
  선행만 join, 하나도 없으면 **명시적 ⊥ 환경**(빈 `{}` 이면 호출자가 호출 전 상태로 계속 진행하는 문제).
  `GuardianVerificationEngine._preds`(node 미지정) = EXIT ∪ RETURN 선행, `_exit_env` 는 ⊥ 선행 제외(전부 ⊥ 면 기존대로).
  RETURN/EXIT 노드는 그래프에서 분리 유지(표시·출처 추적용), 합치기는 계산으로만.
- **검증**: 실험 `g()` 뒤 `s` = 5 / temp 회귀 16종 통과(test_engine_fixes 의 소스 문자열 검사 1건을 새 불변식으로 갱신) /
  16_H_04 → Warning(IntentChecker 일치), 16_H_06 → Violated(IntentChecker 는 Warning).
- **⚠ 드러난 결함 (미수정, 보고됨)**: 소수점 지수 리터럴 `0.0005e16` 이 정수로 평가되지 않고 문자열로 남는다 →
  `require(x <= M)` 가 불가능 판정 → ⊥. 예전엔 위 버그가 RETURN 경로 ⊥ 를 버려서 가려졌다. 45_H_01 이 Violated → Unreachable.

## F53. 10진 숫자 리터럴을 문법 규칙으로 판정하고 정확히 계산 (2026-10-21, 사용자 승인)

- **증상**: `visitLiteralExp` 가 `isdigit()`(또는 `0x`)일 때만 숫자로 분류해 `0.0005e16`·`1e18`·`1_000` 이 `'string'` 이 됐다.
  문자열이 상수 값이 되어 `require(x <= 0.0005e16)` 이 불가능 판정 → 경로 ⊥ (web3bugs_45_H_01 `BORROW_RATE_MAX_MANTISSA`).
  F52 이전엔 RETURN 경로의 ⊥ 가 버려져서 가려져 있었다.
- **수정**: `EnhancedSolidityVisitor.visitLiteralExp` — `ctx.literal().numberLiteral().DecimalNumber()` 이면 밑줄 제거 후
  `Fraction` 으로 정확히 계산, 정수가 아니면 예외(Solidity 도 정수 타입에 못 씀), `literal` 을 정수 문자열로 정규화하고
  `expr_type='uint'`. 단위 리터럴(`365.25 days`)은 기존 `visitLiteralSubDenomination` 이 같은 방식.

## F54. 외부 호출 반환값·미해결 멤버 폴백 제거 (2026-10-21, 사용자 승인: "폴백 금지, 근본 원인 해결")

- **증상**: (1) 외부 호출 반환 타입을 `ifc_<T>.pkl` 에서만 찾아, T 가 contract 이거나(`Ownable(...).owner()`) public 상태변수
  getter 면 실패 → 호출부 2곳이 `UnsignedIntegerInterval.top()` 으로 대체 → address 자리에 숫자 → `require(account != address(0))`
  불가능 → 36_H_02 Unreachable. (2) 해석 못 한 멤버를 `"symbolic(...)"` 문자열로 반환(2곳) → 산술에서 엉뚱하게 크래시(52_H_23).
- **수정**: `Evaluation._lookup_interface_return` 재작성 — interface pkl → 로드된 contract/library CFG(부모 체인) → public 상태변수
  getter 순으로 선언 반환 타입의 ⊤ 를 만든다(`_top_of_type`). void 면 None(값 없음), **끝내 못 찾으면 예외**(의존성 누락).
  uint ⊤ 대체 2곳 제거, @IReturn 체인 중간 호출의 조용한 `break` 도 예외로. 미해결 멤버 2곳(library member / 일반 member)
  → 예외. 부수: `getattr(baseVal, 'bits', 256)` → `baseVal.type_length`(interval 에 `bits` 속성이 없어 늘 256 이던 잠복 버그).
- **영향**: 지금까지 조용히 ⊤/문자열로 넘어가던 경우가 예외로 드러난다(의도). 드러나는 것은 근본 원인(데이터·정의 누락)으로 처리.

## F55. 디버깅 실행 격리 — 실행용 fcfg 사본·snapshot 의미·try/finally (2026-10-21, 리팩터링 Phase 1a, `REFACTOR_PLAN.md`)

- **증상**: (1) `interpret_function_cfg_for_debug` 가 live fcfg 노드를 직접 reset/해석/restore 해서, 실행마다 지워지지 않는
  `before_envs`·`return_vals`·`ireturn_registry` 가 다음 회차와 **구성 시점 값**을 섞었다. 특히 CFG 구성 때 평가한
  `RETURN.return_vals`(파라미터 ⊤ 기반, `DynamicCFGBuilder.py:701`)가 남아 있으면 `_get_post_exit_node` 가 그것을 골라
  **@Post 반환값 검증이 디버깅 입력을 무시**했다(web3bugs_3_H_04: 반환 ⊤ vs 2000 → Warning). @IReturn 값도 다음 회차(미지정)에 남았다.
  (2) `SnapshotManager.snapshot()` 이 객체의 *최초 등록 시점* 상태를 복사해, restore 가 등록 이후의 정상적인 구성 변경까지
  되돌렸고, flush 중 새로 등록된 객체(디버그 값으로 패치된 것)는 복원하지 않았다.
  (3) `interpret_function_cfg`·`fixpoint` 의 컨텍스트·플래그 복원이 정상 경로에만 있어, 예외 한 번이면 `_record_enabled=False`·
  callee 컨텍스트가 남았다. (4) `engine_api._states_cache` 키에 타깃 스코프가 없었다.
- **수정**: (1) `Engine.interpret_function_cfg_for_debug` — pickle 사본(`run`)을 만들어 그 위에서 해석, 노드 env·before_envs·
  return_vals 초기화, live `ireturn_registry` 는 사본으로 넘긴 뒤 비움, 사본이 곧 freeze 결과, `run._live_origin` 으로 line_info
  identity 조회(`_function_start_line`). (2) `Utils/Snapshot.py` — snapshot = 현재 상태, restore = store 전체(새로 등록된 것은
  등록 스냅 = 패치 직전). (3) `interpret_function_cfg` 본체를 `_interpret_callee_body` 로 분리하고 복원은 finally,
  `fixpoint` → `_fixpoint_impl` 래핑으로 `_suppress_stmt_records`·`_in_widening_mode` finally 복원. (4) `_values_key(values, scope)`.
- **검증**: temp 회귀 12종 통과. 마이크로 37/37 동일, @IReturn 누수 테스트(`__isolation_ireturn__`) 이전 leaked=True → False.
  48 케이스(legacy) 46/48 동일: 3_H_04 Warning→**Violated**(입력 기준 반환 3000 ≠ 2000, 올바른 판정. IntentChecker 기대값은 Warning),
  58_H_02 EXIT.return_vals 의 구성 시점 잔여 항목 제거(판정 불변). rebuilt 동일 + return_vals 잔여 제거 2건. 시간 565s→432s.

## F56. library·super·this 호출의 오류 삼키기 제거 (2026-10-21, 리팩터링 Phase 1b)

- **증상**: `Evaluation.evaluate_library_function_call_context` / `_super_` / `_this_` 가 callee 해석 중 **모든 예외**를 잡아
  `"symbolic_library_error(...)"` 문자열·⊤·`"symbolic_this_error(...)"` 로 반환했다(내부 버그가 값으로 위장). library·this 는
  예외 시 컨텍스트도 복원하지 않았다. 또 세 경로 모두 `interpret_function_cfg` 호출 **전에** 컨텍스트를 callee 로 바꿔,
  `interpret_function_cfg` 가 caller 를 callee 자신으로 보고 @IReturn registry 전파가 끊겼다.
- **수정**: 세 경로에서 try/except 와 사전 컨텍스트 전환 제거 → 전환·복원(finally)은 `interpret_function_cfg` 가 담당, 오류는 전파.
- **검증**: temp 회귀 통과, 마이크로 38/38 동일, 48(legacy) 48/48 동일, rebuilt 47/48 — 42_H_01 이 `NetworkXError`(오염된
  컨텍스트 증상) → `ValueError: 'denominator' not in struct 'IDiscountProfile.discount_ret'`(실제 원인 노출, 미수정·보고).

## F57. IR 보존·기본 의미 정리 (2026-10-21, 리팩터링 Phase 2)

- **수정**
  - `assert`: `process_assert_statement(condition_expr, string_literal=None)` — visitor 는 조건만 넘겨 TypeError 로 assert 가 전혀
    동작하지 않았다(rebuilt numscout_HIT 결과 없음 → Violated, IntentChecker 와 일치).
  - `x--`: IR 에 실제 연산자(`++`/`--`) 저장(`handle_unary_incdec`). 예전 토큰(`unary_prefix/suffix`)은 `+=` 로 재해석됐다.
    해석기는 모르는 연산자·옛 토큰을 예외로(옛 pkl = 재빌드 필요). `Dependencies/objectfile` 3개(FullMath, CommonLibrary,
    LockeERC20) 재빌드. `evaluation/Dataset/deps` 의 11개는 deps 전체 재빌드 때.
  - 튜플 선언 `(a, b) = e`: 선언문 뒤에 슬롯 위치를 보존한 튜플 대입문을 IR 에 추가(`build_variable_declaration_tuple(tuple_lhs=)`).
    예전엔 init 식이 IR 에 없어 해석 시 모든 슬롯 ⊤ + 호출 부수효과 소실 + 그 안의 interface 호출이 @IReturn 슬롯에서 빠졌다.
  - `emit E(args)`: `emit` 문 IR(Statement/`add_emit_statement`/`build_emit_statement`/`Engine._interpret_emit`) — 인자 식 평가.
  - `unchecked { }`: 구성 시 문장 블록에 `unchecked_block` 표시(`ContractAnalyzer.is_inside_unchecked`, pkl 보존), 해석 중
    `Engine._unchecked` 로 unsigned 뺄셈을 wrap(`UnsignedIntegerInterval.subtract(wrapping=)`). 덧셈·곱셈·signed 는 이미 overflow ⊤.
  - 정수 명시적 변환(`uintN(x)`, `intN(x)`): 2의 보수 wrap(`Evaluation._wrap_integer_range`). 예전엔 범위 밖을 clamp(불건전)하고
    범위와 안 겹치면 ⊥(도달 불가 오판).
  - ⊥ 오용 제거(D2): 빈 동적 배열 `.length` ⊥→⊤ / 정적 배열 미구체화 원소 ⊥→typed ⊤(`bottom_from_soltype` 삭제) /
    디버그 배열 패딩 ⊥→typed ⊤(`_create_unspecified_array_element`) / 구간으로 못 바꾸는 비교 상대 ⊥→"정제 안 함" /
    `BoolInterval.widen` 무조건 ⊤→join(유한 격자) / `BytesSet.is_bottom` 추가 + `_make_bottom` 의 bytesN ⊥.
- **검증**: temp 회귀 16종 통과. 마이크로 33/38 동일 + 의도한 5건(incdec·tuple·emit·assert·unchecked). 48 legacy: **판정 변화 0**,
  상태 변화 = emit 노드 추가 / 정수 변환 wrap(35_H_08·10·12 등 범위가 넓어지거나 좁아짐) / 튜플 초기화(79_H_02 주소 ⊤→실제) /
  @IReturn 슬롯 추가(52_H_04·34: 튜플 선언 안 interface 호출) / ⊥ env 의 bytesN 표시 None→⊥ / bool widening 정밀도.
- **⚠ 드러난 결함(미수정·보고)**: numscout_BoostToken_* — `.length` 가 ⊥ 이던 동안 죽어 있던 루프 본문이 살아나
  `_rOwned[_excluded[i]]`(길이 모르는 address 동적 배열 원소로 mapping 인덱싱)가 AddressSet 으로 평가 → `sub` 에서 AttributeError.
  (이전엔 그 앞의 `IUniswapV2Router02.WETH()` 반환형 미해결 오류)

## F58. CFG 구성 단계에서 추상 평가 제거 (2026-10-21, 리팩터링 Phase 3a, D1/D3)

- **수정**: `ContractAnalyzer.process_*`(변수 선언·튜플·Yul let/:=·대입·++/--·delete·호출·if/else if/else·while·for·return·
  revert·require/assert·emit) 에서 식 평가·refine·update·`reinterpret_from`·`_sync_constructor_state` 제거 → 노드·엣지·
  Statement IR 만 만든다. `DynamicCFGBuilder` 의 env 인자(true_env/false_env/join_env/else_env/false_base_env)·노드 env 복사·
  join 재계산·`RETURN.return_vals` 기록·삭제 경로의 재해석 제거. `Engine.reinterpret_from` 삭제(150줄). pending 메커니즘
  (`pending_calls`/`pending_callee_name` 등록·`flush_pending_calls`) 제거. `delete` 의 타입별 0 의미를 해석기로 이동
  (`Engine._delete_composite`/`_zero_of`; 예전 해석기는 피연산자에 정수 0 대입). @During/@Post 의 "구성 시점 상태로 즉시
  검증" 폴백 → annotation 오류(D4-b), @Post 는 함수 이름으로 fcfg 를 찾아 부착(직전 처리기가 포인터를 비운 경우).
- **검증**: temp 회귀 16종(삭제 테스트 3종은 `reinterpret` 인자만 제거) 통과. 마이크로: 구성 중 실패하던 forward call·
  상호/자기 재귀·struct 생성자·함수 뒤 상태변수 선언이 구조 완성(자기 재귀 fact(a∈[0,3]) = [1,6], refine 으로 종료).
  48 legacy: 40/48 동일, 새 판정 EthereumGod Satisfied·WANGMI Violated(일치)·45_H_01 Warning, 3_H_04 의 중복 가짜 결과
  (구성 시점 상태로 검증한 같은 @Post) 제거. rebuilt: Nokon·WANGMI·56_H_02·62_H_08 실행됨. **시간 451s→130s, 242s→40s.**
- **⚠ 드러난 결함(미수정·보고)**: rebuilt 58_H_02 — `} else {` 중괄호 소실(F-B)로 컨트랙트 수준 `event` 줄이 함수 안 문장으로
  분류. 42_H_01 `'denominator' not in struct 'cf'`. for 루프 `continue` 후 흐름 unsound(마이크로 break_continue: return 의
  n=[0,2]). memory struct 생성자 `P(a)` 후 멤버 소실. 한 줄 블록 `{ stmt; }` 은 slicer 미지원. `process_payable_function_call`/
  `process_function_call_options` 가 `pass`(옵션 호출 문장이 조용히 사라짐). `address.transfer/send` 미해결 멤버 예외.

## F59. 블록 스코프·지역변수 기본값·튜플 슬롯·env 복사 성능 (2026-10-22, 리팩터링 Phase 3b, D2/D8)

- **수정**
  - D8 블록 스코프: 빌더가 if/else-if join(앵커 = cond)·while/for exit(앵커 = 루프 join)에 `scope_anchor` 기록. 해석기가
    분기 join·루프 exit(fixpoint 직후)·back-edge 에서 블록 진입 시점 이름만 남긴다(`Engine._restrict_scope/_scope_keys`).
    shadowing: 구성 시 텍스트 스코프로 감지(`ContractAnalyzer.shadowing_info`, 상태변수·파라미터 포함) → 선언 실행 시 바깥
    binding 을 `name␟shadow@id` 키로 보관 → 블록 종료/함수 종료(`_restore_all_shadows`)에서 복원. 종료 노드가 없는 블록
    (bare `{`·unchecked·do·try/catch) 안의 shadowing 은 명시적 미지원 오류. 보관 키는 직렬화·top_vars·callee 상태에서 제외.
  - 지역변수 선언 = 실행마다 새 binding(루프 반복 간 이전 값 잔류 제거), 초기화식 없으면 Solidity 기본값(0/false/address(0)/
    빈 동적 배열/0 으로 채운 struct·정적 배열; 예전 ⊤).
  - 기본값 변경으로 드러난 침묵 처리 수정: 튜플 대입 우변이 튜플이 아니면 조용히 건너뛰던 것 → 예외 / 다중 반환 외부 호출이
    첫 반환 타입만 돌려주던 것(`_lookup_interface_return`, `_assemble_ireturn_value`) → 반환 타입별 리스트(+@IReturn `[i]`) /
    삼항식 분기 값 join 불가 시 `"symbolicConditional(...)"` 문자열 → 튜플은 원소별 join, 그 외 예외 / 괄호식 `(x)` 갱신 =
    안쪽 식 / 튜플 식의 빈 슬롯 위치 보존(`visitTupleExp` 가 `_split_tuple_slots` 사용: `(, , lpSupply) = f()` 가 0 번째 값을 받던 버그).
  - 성능: `VariableEnv.copy_single_variable` 이 Variables/GlobalVariable/EnumVariable 을 `copy.deepcopy` 하던 것 → 값만 새로
    만들고 타입 메타데이터 공유(`_copy_value`), 배열·mapping 타입 메타데이터도 공유. 3_H_05 459s→48s(정밀해진 for 루프 반복).
- **검증**: temp 16종, 마이크로 40/40(새 케이스 shadow_if_block x=[1,1] 복원, shadow_state_var EXIT s=[1,2]), 48 판정 = Phase 3a 와
  동일(legacy·rebuilt), 상태 변화 = 블록 밖 변수 제거·기본값 0. 시간 legacy 145s / rebuilt 69s.

## F60. "평가 안 됨" 과 ⊥ 분리 — 해석 후 노드 분류 (2026-10-22, 리팩터링 Phase 3c, D2)

- **수정**: `Engine._classify_unvisited`(디버깅 해석 종료 후): ENTRY 에서(sink 에서 멈추는) 경로가 없는 노드 → 명시적 ⊥,
  경로가 있는데 해석되지 않은 노드 → 예외(해석기 결함을 빈 상태로 내보내지 않음). 루프 fixpoint 가 수렴 후에도 값이 한 번도
  흘러 들어오지 않은 노드(루프 안 infeasible 분기)를 바깥 worklist 처럼 명시적 ⊥ 로 둔다(예전엔 `{}`).
- **검증**: temp 16종 통과. 48 판정 변화 0(legacy·rebuilt), 상태 변화 = 미해석 노드 `{}`→⊥(modifier splice 복제 노드·루프
  안 infeasible 분기). 마이크로 break_continue 는 이제 **예외**("if_true_8 reachable but not interpreted") — 보고 목록 (2)
  `continue`/`break` 흐름 결함이 조용한 오답 대신 드러남.

## F61. 내부 호출 통합 진입점·on-demand 해석·modifier 디버깅 시점 합성 (2026-10-22, 리팩터링 Phase 4a, D3/D4/D6)

- **수정**
  - `Engine.invoke_callee`: 일반·library·super·this 4 binder 를 하나로. 인자는 callee `related_variables` 를 고치지 않고 바인딩
    (`interpret_function_cfg(param_bindings=)`) — 실행 간 누수·super/this 의 caller env 영구 병합 제거. 복합 타입 인자는 내부
    호출에서 참조 의미(callee 종료 시 caller 원 객체에 되쓰기, this 는 복사). 마이크로 memory_alias p.v=[2,2](기대값).
  - 이름 호출 미해결: uint ⊤ 반환(+pending 기록) → interface/contract 이름이면 형변환으로, 함수 타입 변수면 명시적 미지원(D9,
    Phase 5), 그 외 "undefined function" 예외(컴파일 오류급). library 함수 못 찾음·super/this 못 찾음·인자 수 불일치 → 예외
    (예전 심볼 문자열·⊤). `Lib.f()` 로 직접 부른 라이브러리 본문의 형제 호출을 그 라이브러리에서 찾음(예전 사용자 컨트랙트에서
    찾다 ⊤). `Lib.S(args)`(라이브러리 구조체 생성자)를 구조체로(예전 심볼 문자열) — `_construct_struct` 로 공통화.
  - modifier: 구성 시점 splice·존재 검사 제거, `fcfg.modifier_invocations=[(이름, 인자 식)]` 기록(visitor 가 인자 수집,
    modifier CFG 에 `parameters` 순서 기록). 디버깅 해석 때 실행 사본 위에서 합성(`Engine._compose_modifiers`): modifier
    CFG 복제(문장·조건·엣지 속성 포함), 파라미터·지역변수 `이름$변수` 로 변경, 인자 바인딩 노드(함수 헤더 줄), `_` 자리에 안쪽
    본문, 안쪽 정상 종료 → `_` 다음, modifier ERROR → 함수 ERROR. 내부 호출 callee 도 합성본(실행 1 회 캐시). `_` 2 개 이상 →
    명시적 미지원. 생성자의 기반 생성자 호출은 합성 제외(Phase 7).
- **검증**: temp 16종 통과, 마이크로 modifier_before counter=[6,6]·modifier_after 정상(예전 각각 미실행·함수 손실).
  48: 판정 변화 = legacy numscout_HIT(→ "undefined function 'balanceOf'": contraction 소스가 상속 없이 `balanceOf` 호출 —
  케이스 데이터가 컴파일 불가, 보고) / legacy SwordCrowdsale(legacy 모드가 objectfile 의 옛 `Ownable` 과 소스 `Ownable` 을 같이
  올려 modifier 에 `_` 가 2 개 — 중복 정의, 보고). rebuilt 52_H_23: 옛 deps pkl 의 modifier 에 `parameters` 없음(재빌드 필요).
  나머지 = modifier 실행에 따른 상태 변화. 시간 legacy 145s→113s, rebuilt 69s→35s.

## F62. 리팩터링 Phase 4b — 기존 결함 수정 (2026-10-22, 사용자 승인)

- **for `continue`**: 감싸는 루프의 증감 노드를 거쳐 join 으로(예전 join 직행 — continue 경로에서 `i++` 누락, unsound).
  증감식 없는 `for(;;)` 은 body → join(예전 `add_edge(body, None)`).
- **`continue`/`break` 대상 = 문법적 최내곽 루프** (`DynamicCFGBuilder._enclosing_loop`, 중괄호 체인). 예전엔 역방향 DFS 가
  앞서 닫힌 안쪽 루프의 exit → 헤더로 들어가 안쪽 루프에 연결됐다(`for i { for j {…} if(c){continue;} }`). do-while 안은
  Phase 6 까지 명시적 오류, 루프 밖은 컴파일 오류. `find_loop_condition` 삭제.
- **`} else {` 중괄호**: slice 가 `closeBefore` 로 떼어낸 `}` 를 저장소에 되돌림(`SolidityAnalyzer._stored_text`) —
  예전엔 앞 블록의 `}` 가 소스 저장소에서 사라져 괄호 균형 기반 탐색이 한 단계씩 어긋났다(if/else 뒤 줄이 if 안으로, 함수 뒤
  `event` 가 함수 문장으로). `determine_top_level_context` 는 앞 `}` 무시. `engine_api._function_span` 보정 제거.
- **`address payable`**: 방문자에서 `address` 로 정규화(`isPayable` 표시). 예전엔 `== "address"` 초기화 분기를 모두 빗나가
  `"symbol_x"` 문자열 값 → `.transfer/.send` 해석 불가. address 변수 멤버는 값(AddressSet) 기준 처리.
  `.call/.delegatecall/.staticcall` = `[bool ⊤, bytes]`(예전 bool 하나 → 튜플 대입 실패). 옵션 호출 `f{value:v}(…)` 은 옵션을 벗긴
  호출로 평가(예전 무조건 uint ⊤). 호출 괄호 없는 옵션식 = 컴파일 오류.
- **하드코딩 의존성 경로 제거**: interface 반환 조회·@IReturn 조립·using-for 등록·qualified struct 해석이
  `Dependencies/objectfile`(·`Libraries/objectfile`) pkl 을 직접(일부 `except: pass`) 읽었다 → 프로젝트 deps 와 다른 판이 섞임
  (42_H_01 `FloatStruct` vs `float`). 로드된 `contract_cfgs`/`library_cfgs` 만 본다. using-for 대상 라이브러리가 없으면 예외.
- **입력 전제(입력 1개 = statement 1개)**: `Dependencies.main._one_statement_violation` — 한 줄 블록·다중 문장·중괄호 없는
  제어문을 슬라이서가 명시적 오류로 거부(예전엔 다음 줄과 조용히 합쳐짐). 의존성은 빌드 시 정규화
  (`preprocess_contraction.normalize_one_statement_per_line`).
- **contraction 생성기**: `contraction_target.order_declarations` — 최상위 선언 선언-후-사용 안정 위상정렬(순환은 예외).
  변화 = numscout_Nokon(`library SafeMath` 를 컨트랙트 앞으로), 케이스 재생성.
- **검증**: temp 16종 통과, 마이크로 continue/break 5종·address_members 확인. 48: transfer/send/staticcall 로 멈추던
  6건 진행(BoostToken×2·SwordCrowdsale Violated, 29_H_08/11 Warning, EthereumGod), 42_H_01 Warning, 113_H_05 Violated·58_H_02
  "No CFG node found" 해소, Nokon Warning 복귀. 드러난 것: w34·w36·w58·w65 deps 빌드가 `_topo_order` 순환으로 pkl 0개였음
  (폴백이 가림 — 설계 논의 중), 31_H_01 `.mul` 미해결.

## F63. 정수 암묵 변환·공통 타입 (2026-10-22, 리팩터링 Phase 4b, 사용자 승인 A)

- **문제(unsound)**: 저장 시 선언 타입으로 변환하지 않고, 산술은 왼쪽 피연산자 폭을 썼다. `(uint256 r0, …) = pair.getReserves()`
  의 r0 가 uint112 폭을 유지 → `r0 * price` 가 112비트에서 overflow → 112비트 ⊤(실제는 256비트). 루프 join 에서 폭 불일치
  예외로 드러남(web3bugs_70_H_04/10).
- **수정**: `Evaluation.coerce_to_declared` — 저장 지점(선언 초기화·대입 leaf(`Update._store`)·튜플·인자 바인딩·반환값
  (`Engine._coerce_return`)·struct 생성자 필드·디버그 입력(`DebugInitializer._coerce_debug`)·상태변수/상수 초기화식)에서
  선언 타입 폭·부호로 변환(값 범위 유지). 범위 밖이면 예외(Solidity 는 손실 있는 암묵 변환 불가 → 타입 오류/잘못된 디버그 입력).
  이항 산술·비트 연산은 `_unify_operand_types` 로 공통 타입(상수식은 상대 타입으로, 그 외 같은 부호면 넓은 폭).
  상수식의 단항 `-` = 부호 있는 값(예전 `return -1;`(int256) → 2^256-1).
- **검증**: temp 16종 통과, 마이크로 `int_width`(c=[2^200,3·2^200], 튜플 uint112→uint256 후 곱 정확, `1 + uint8` = 251).
  48: 70_H_04/10 복귀(legacy Warning/Violated), 드러났던 35_H_08/10/12·52_H_04/34·60_H_01 은 원인(디버그 입력 256비트 주입,
  상수 부호 반전) 수정 후 원래 판정. legacy 3_H_05 = 옛 pkl(`con_PriceAware`) 상태변수 초기값이 변환 전 256비트 → 재빌드 필요.
  rebuilt 44/48 동일(나머지 = Nokon 복귀·70_H_04/10 오류 해소·35_H_11 상태 변화).
- **보고(미수정)**: `_pow_fits` 과보수(`2**200` → ⊤, 정밀도), 범위 인덱스 쓰기 unsound(동적 배열 무시·정적 배열 강한 갱신·매핑 기존
  키만), 반환문 여러 개인 다중 반환 함수가 첫 경로 값만 반환(리스트 join 없음 → 폴백).

## F64. 전역 의존성 트리 폐기·로더 통합·증분 수술 fail loud (2026-10-22, 사용자 결정)

- **전역 트리 폐기**: `Dependencies/{objectfile,interfaces,libraries,contracts}`·`Libraries/`·`Analyzer/CFGSerializer{,Pickle}.py` 삭제.
  의존성은 프로젝트 단위(`evaluation/Dataset/deps/<p>`)만. 이유: 전역 풀의 동명이인(contract Roles vs library Roles)·판 혼입
  (OZ vs solmate ERC20, F62 의 `FloatStruct`)은 실제 컴파일 단위와 다른 인공 집합이라 생긴다.
- **로더 통합**: CLI(`main.load_dependencies`)와 서버(`engine_api._load_dependencies_into`, DepSpec 선별)가 따로 있어 동작이
  어긋났다(file-level enum 덮어쓰기 vs setdefault 등) → `Dependencies.main.load_project_deps` 하나로.
- **증분 수술 fail loud**: `EngineSession.update()` 가 미지원 구조(try/catch·do-while·break/continue 삭제·수정)를 전체 재빌드로
  보내고, 수술 중 예외도 `except Exception: _full_rebuild` 로 삼켰다 — 수술 버그가 결과에 드러나지 않는 폴백.
  → `IncrementalSurgeryError`: 미지원은 수술 전 거부(세션 불변), 수술 중 실패는 세션 오염 표시 후 모든 공개 메서드 거부.
  서버는 오염 세션을 캐시에서 제거.
- **검증**: temp 16종 통과(`test_safety_net` 은 거부 검증으로 변경 8/8, `test_engine_fixes` 의 로더 이름 검사 갱신),
  smoke(`deps/n_BoostToken` SafeMath 로드), micro 기대 실패 3개만, rebuilt 48/48 이전과 동일.
- **남은 것(보고)**: builder 수술 함수(`_delete_early_exit` 등)의 조용한 `return []` 중 "처리 불가" 경우가 여전히 no-op.

## F65. 노드 uid·실행 전 상태·표시 줄·callee 호출 키 (2026-10-22, 사용자 승인 F1/F2/U3)

- **노드 uid**: 직렬화 id 가 노드 이름(`Assign_4` = 생성 시점 줄)이라 증분 편집으로 줄이 밀리면 중복됐다(실측: 줄 삽입 후
  `Assign_4` 2개). intent live→frozen 대응·callee 상태·TopVar 도 이름 키라 같은 문제. → `CFGNode.uid`(uuid, 불변), 모든 키 uid.
  uid 없는 pkl 은 `__setstate__` 에서 재빌드 요구 오류.
- **IN 상태**: worklist 가 선행 join·scope 정리 직후 `in_variables` 저장(문장 노드·루프 헤더·φ). 루프 헤더 IN = back-edge
  선행 미방문 시점 = 루프 밖에서 들어오는 상태.
- **표시 줄**: if/else-if join 은 조건 줄에 생성·첫 `} else` 줄에 등록돼 화면 위치가 블록 중간이었다 → 체인을 닫는 `}` 줄 계산.
- **callee 호출 키 결함 2건**: ① 키 (호출 노드, 함수) — 같은 노드에서 같은 함수를 두 번 부르면 앞 호출이 사라짐 → 노드 안
  호출 식 순번 추가. ② callee 해석이 `_call_site_node` 를 자기 노드로 덮은 채 복귀 → 같은 문장의 다음 호출이 callee 의
  마지막 노드를 호출 지점으로 잡음(조건식 안 호출은 직전 문장 노드) → 복귀 시 복원, 조건 노드에서 지정.
- **검증**: temp 16/16, micro 동일, rebuilt 48 엔진 효과 48/48 동일.

## F66. mapping/array 저장소 의미론 — 이름 기반 입력 유지 + sound (2026-10-23, 사용자 승인)

- **증상**(재현 `temp/test_storage_soundness.py`): `m[i] = 9`(i 범위)가 무시됨, `arr[i] = 9`(i∈[0,1])가 두 원소를 모두 9 로,
  `m[i]`(i∈[1,5]) 읽기가 입력 `m[i]=100` 대신 기존 구체 키 `m[3]=200` 하나를 선택, ⊤ 주소 `m[b] = 80` 뒤 `m[a]` 가 입력 50
  그대로, `arr[i]` 입력을 경고 후 버림, `arr[5]` 를 읽으면 길이가 6, 디버그 안 준 리터럴 키 읽기가 AttributeError.
- **근본 원인**: 키 규칙이 DebugInitializer·Evaluation·Update 의 식별자/리터럴/멤버/이항식 경로마다 달랐고, 접근식 텍스트
  ("i")·구체 키("3")·가짜 키(`symbolic_index_<id>`, `func_result_<id>`, `AddressSet(⊤)`)를 같은 dict 키 공간에 섞어
  "같은 위치일 수 있는 셀" 관계가 없었다. 구체화한 원소 수를 배열 길이로 썼다.
- **수정**: `Interpreter/Semantics/Storage.py` 로 일원화(REFACTOR_PLAN §6 2026-10-23 (12)). 선택자 셀 + summary +
  must/may-alias(키 추상값 겹침) + LHS 경로 기반 weak update + 진입 제약(assume) 분리 + 선택자 무효화.
- **함께**: `variables_equal` 복합 타입 내용 미비교, mapping/array join 의 한쪽 키 유지, `@StateVar` 입력 오류 삼킴,
  디버그 입력 스냅샷 leaf 등록, `update_mapping_in_cfg`(읽기 중 영속 CFG 변경) 제거.
- **검증**: storage 29/29, temp 18 종, transaction 35/35, micro 동일, api 48 판정 동일·상태 변화 11건 원인 확인.

## F67. 디버그 슬롯 도출 footprint + 호출자 선택자 대응 (2026-10-23)

- **증상**: 케이스 디버그 입력 172개(37 케이스)에 엔진 슬롯이 없어 프론트에서 입력할 수 없었다(api 일치 15/48 vs record 경로 20/48).
- **근본 원인**: 슬롯 스캐너가 타깃 본문·타깃 컨트랙트 자신의 상태변수만 봤고, using-for 호출·구조체 경로·인터페이스 핸들을
  잘못 다뤘다. 또 callee 안의 `m[param]` 은 실행 중 callee 이름 키로 따로 잡혀 호출자 이름 입력과 연결되지 않았다.
- **수정**: footprint 기반 도출(내부 호출·modifier·라이브러리·super/this, 오버로드 전부) + 타깃 진입 이름 번역 + 조상 상태변수 +
  구조체 leaf 전개 + IReturn 튜플 원소. 실행 쪽 `Storage.callee_aliases`(파라미터 ↔ 호출자 순수 선택자 must-alias).
- **검증**: 누락 172 → 70(남은 것 = 구체 인덱스 48, 케이스 데이터 불일치 22), 일치 15 → 19, storage 32/32, temp 18 종 통과.

## F68. 남은 구현 묶음 + Yul P0-a (2026-10-23, 사용자 승인)

- **빈 본문 callee ⊥** (47_H_02·78_H_02): 진입 환경을 ENTRY 의 후속에 두어 ENTRY 가 빈 채였고, `_force_join_before_exit` 가 빈 본문
  (ENTRY → EXIT)의 유일한 정상 종료 선행 ENTRY 를 도달 불가로 보고 EXIT = ⊥ → 되쓰기로 호출자 상태변수 ⊥. → 진입 환경 = ENTRY.
- **다중 return tuple**: `_extract_return_value` 가 `.join` 없는 값(list)에서 첫 경로 값을 반환 → 원소별 join, 모양 불일치는 예외.
- **재귀**(D7): `invoke_callee` 가 호출 스택(`_func_key` = 소유·이름·시그니처)에 있는 함수면 요약 — 전이적 쓰기 집합의 상태변수 ⊤
  (컨테이너 단위), 쓰는 복합 파라미터 인자 ⊤, 반환 = 선언 반환 타입 ⊤. enum 은 ⊤ 표현이 없어 예외.
- **함수 포인터**(D9): 함수 타입 파싱(파라미터·반환·가시성), 변수 값 `FunctionRef ⊤`, 함수 이름 = 값, 호출 = internal 이면 상태변수
  전체 ⊤ + 복합 인자 ⊤, 반환 ⊤.
- **정적 배열 범위 밖 디버그 입력**: 인덱스가 전부 범위 밖이면 "잘못된 디버깅 입력" 오류(예전: 저장 안 되는 가상 셀 → 입력 소실).
- **library event**: `LibraryCFG.events`.
- **using-for 수신자 = 정적 타입** (31_H_01): 값으로 추측하던 것을 `_static_type_of` 로. 모르면 오류. 확장: 전역 멤버, `.length`,
  `.balance`, `x.f()`/`Lib.f()` 반환 타입. `"address.<member>"` 문자열 폴백 → 예외. 동적 bytes/string `.length` = ⊤.
- **함수 소유자 `owner_contract`**: `add_function_cfg`/`update_function_cfg`/생성자 등록 시 기록. `_fcfg_owner` 의 identity 역색인은
  pkl 사본(부모·using 라이브러리) 때문에 실패했다 — library 안 using-for 조회 실패, callee Step Into 누락(조용히).
- **상속 디스패치** (52_H_23): 인터페이스 함수 파라미터 미기록(전부 `()`), 탐색 = 소스 순서 깊이 우선(본문 없는 선언 선택).
  → `has_body`, `linearize`(C3, 이름 기준), `_dispatch` = 선형화 전체 오버로드 해소(선언 포함, 인자 수 = `parameter_types`) →
  본문 있는 첫 구현. super = 정의 컨트랙트 다음부터, `Base.f()` = Base 선형화, `this.f()` = 가장 파생된 것 기준.
- **StructDefinition.members** = {이름: SolType}: `_static_type_of` 의 struct 멤버 분기가 dict 를 기대해 늘 None 이었다.
- **Yul P0-a** (`YulEval.py`, `EnhancedYulVisitor`): 실측 결함 — `div(a,0)`·`mod(a,0)`·`sub(0,1)` = ⊥(경로 소멸), `slt` 부호 없음,
  `shl(s,v)` 순서 뒤바뀜, `uint8 r := 300` 예외, 미지원 builtin 대입이 조용히 사라짐(`size := extcodesize(a)` → size=0 →
  `isContract` false → 52_H_23 Unreachable), `if/switch` 조건 무시하고 본문 무조건 실행, `revert` 무시, 파싱 실패 줄(여러 줄 식,
  `assembly {` 머리 줄) 부분 트리로 조용히 진행. → EVM word 평가, 효과 표, revert = ERROR, cleanup, 제어 흐름·종료·파싱 실패 =
  실행 시 명시적 미지원 오류(본문 줄 미생성), 슬라이서 여러 줄 식 결합.
- **검증**: 프로브(빈 본문·tuple·재귀·함수 포인터·디스패치 4종·Yul 산술 8종·효과 6종), temp 전 스크립트 통과,
  rebuilt 48 일치 23 → 26 (`results_api_s13.json`).

## F69. 의존성 레지스트리 + 부모 정의 상속 (2026-10-23, 사용자 승인)

- **빌드 순서 의존 제거**: 선언별 조각 + 위상정렬 → 파일 단위 + 의존 폐포 고정점 재분석(`Dependencies/registry.py`). 순환 프로젝트
  (w34·w36·w58·w65) 빌드 성공. 단위 pkl 의 CFG 참조는 이름으로 저장, 로드 때 선언당 객체 하나.
- **조용한 건너뛰기 → 오류**: 부모 컨트랙트 미발견(`make_contract_cfg`·`make_abstract_contract_cfg`·`make_interface_cfg`),
  using 라이브러리 미발견 → `UnresolvedName(name)`. 미정의 타입 → `UndefinedType`(SourceError + UnresolvedName).
- **부모 struct·enum 정의 상속(A-2)**: 타입 판정은 부모 체인을 봤지만 변수 초기화는 자기 표만 봐서 `contract Basket is IBasket
  { PendingPublisher public p; }` 가 "struct def PendingPublisher is undefined". → 자식 생성 시 부모 정의를 가져옴(정의 `owner`),
  소유자가 다른 같은 이름은 오류.
- **의존성 전처리 최상위 재배치**: 같은 파일 뒤쪽의 enum·interface 를 앞에서 쓰는 경우.
- **검증**: rebuilt 48 일치 26 → 30, temp 전부 통과.

## F70. 의존성 Invalid·불일치 케이스 정리 (2026-10-23, 사용자 승인)

- **구간 비트 연산 unsound**: `|`·`^` 상한 = max(hi) (1|2 = 3 > 2). → 단일값 정확, `|` [max lo, 2^k−1], `^` [0, 2^k−1].
- **함수 문맥을 헤더 텍스트로 추측**: 위쪽 모든 줄을 훑어 `received = 1;` 을 receive 함수로, 헤더 텍스트 타입(`bytes32[]`)과
  등록 시그니처(`array`) 불일치로 오버로드를 못 찾음. → 블록 시작 줄에 등록된 함수의 ENTRY 로 식별. receive/fallback 등록.
- **try/catch 미연결**: 방문자 빈 껍데기, 없는 메서드 호출, try 줄이 함수 헤더로 분류, catch 1개만, `cond.__catch_attached`
  name mangling, join 미등록(본문이 join 뒤에 순차로), catch 줄 밀림.
- **문법**: `errorDefinition` 의 `*` 누락(파라미터 1·3개 error 불가), `revert()` 빈 괄호. 파일 수준 error/event.
- **정적 배열 길이**: 이름 상수(`uint256[N_COINS]`) 미지원 → 컴파일 타임 상수식 평가.
- **값만 있는 식 문장**(`a.status == false;`), **struct 이름 공백 분리**(`struct X{`), **여러 줄 for 헤더**(슬라이서가 괄호 안 `;` 에서 자름).
- **인자 intent** 를 호출 후 상태로 재평가 + 검증 중 callee 캡처 → internal error. **intent 미선언 이름** = internal error.
- **IReturn**: 지역 인터페이스 핸들 누락(SolidityGuardian 케이스에 있던 입력), using-for 함수 오분류(fail-open), struct 반환 미펼침, 값 종류 고정.
- **슬롯**: intent 만 참조하는 상태변수의 진입 값 슬롯 없음 → analyze 가 intent 를 받는다.
- **조용한 빈 결과**: 없는 타깃 컨트랙트 → 노드 0개 결과 → 오류.
- **검증**: rebuilt 48 일치 30 → 39, temp 전부 통과.

## F71. 희소 원소별 배열 + 지연 widening (2026-10-23, 사용자 승인)

- **정적 배열 전 원소 구체화**: 선언 때 `range(length)` 로 원소를 모두 만들었다 — `Observation[16777215]`(storage ring buffer, 쓰기 전엔
  비용 없음)에서 메모리 폭증. → 구체화된 원소만 `{index: 셀}`, 나머지는 summary(D). Read/join/비교를 의미상 값으로.
- **지연 생성 struct 원소**: qualified 이름(`Lib.S`)으로만 정의를 찾아 못 찾으면 조용히 멤버 없는 struct. → 마지막 이름까지, 없으면 오류.
- **지역 정적 배열**: 초기화식이 없으면 원소가 비어 접근이 범위 오류였다. → 0 (D = 0 셀).
- **widening 지연 상한 [1,20]**: 길이 ⊤ 루프가 20 회 지연(의도와 반대), `i < 1000` 도 20. → N̂ 를 얻으면 min(N̂, 300), 못 얻으면 2.
- **W_MAX 도달 시 비고정점 반환**: 작업 목록이 남은 채 루프를 빠져나와 narrowing → unsound 가능. → 강제 widening 으로 계속, 2·W_MAX 에서 오류.
- **검증**: rebuilt 48 판정 동일(41), 총 시간 640 s → 55 s, temp 전부 통과.

## F72. 어노테이션 문법 오류의 조용한 복구 (2026-10-25, 사용자 승인)

- **증상**: 디버그 입력·intent 한 줄이 문법에 맞지 않으면 `generate_parse_tree` 가 ANTLR 오류를 콘솔에 출력만 하고 복구된(일부 토큰이
  빠진) 트리를 돌려줬다 → visitor 가 `None.getText()` 로 죽거나, 복구 결과에 따라 다른 문장으로 조용히 처리될 수 있었다.
- **수정**: `ParserHelpers.annotation_syntax_errors(src)`(lexer·parser 오류 수집) 추가, `intentUnit` 파싱은 오류가 있으면
  `AnnotationSyntaxError`(Utils/Errors.py). engine_api `_queue_debug_values` 는 미리 검사해 그 슬롯의 입력 오류 Diagnostic 으로.
- **검증**: rebuilt 48 판정 동일(41), temp 전부 통과, 새 `temp/test_slot_roundtrip.py`.

## F73. IReturn 캐스트 조회가 주소를 보지 않음 (2026-10-25, 사용자 승인)

- **증상**: `IF(a).f()` 에 준 @IReturn 값이 같은 함수의 `IF(b).f()` 에도 들어갔다(조용한 오적용).
- **원인**: visitor 가 `IF(x)` 캐스트를 `TypeConversion(typeName, expression)` 으로 만든다. Pattern B 조회(`Evaluation.py`)는
  `FunctionCallContext` 형태를 기대해 죽은 코드였고, `InterfaceFunctionCallContext` 의 대체 조회가 키의 인터페이스·함수 이름만
  비교해 모든 캐스트 호출을 처리했다.
- **수정**: Pattern B 조건을 `TypeConversion` + 인터페이스로, 주소 = 캐스트 인자 식별자(식이면 조회 안 함 → ⊤). 대체 조회 삭제.
  호출 인자는 키에 넣지 않는다(함수당 값 하나 — 사용자 결정).
- **슬롯**: 호출 없는 함수 멤버 참조(`I.f.selector`)를 IReturn 슬롯으로 내던 것 제거(engine_api 스캐너).
- **입력 검증**: `process_ireturn_cast` 가 구체 컨트랙트 캐스트도 받던 것(S0) → 인터페이스가 아니면 명시적 오류(Pattern A 와 동일).
- **검증**: rebuilt 48 판정·메시지 동일(41), temp 전부 통과, 새 `temp/test_ireturn_cast.py`.

## F74. 파라미터·named return 이 상태변수를 가림 (2026-10-25, 사용자 결정)

- **증상**: `function f(uint256 want)` 와 상태변수 `address want` — 함수 CFG 생성 때 상태변수 주입이 파라미터를 같은 이름 키로 덮어써
  파라미터가 address 가 됐다(31_H_01 `.mul` 미해결). 타입이 같으면 오류 없이 조용히 틀린 값.
- **수정**: 근본 해법(선언 정체성 기반 환경)은 future work. 그 전까지 `UnsupportedShadowing` 명시적 오류(`make_function_cfg`,
  늦게 선언된 상태변수·상수 주입). 31_H_01 은 소스 rename(`want` → `_want`).
- **검증**: rebuilt 48 일치 42(31_H_01 Violated), 재빌드에서 부모 private 상태변수 충돌 4 단위 Invalid(케이스 영향 없음).
