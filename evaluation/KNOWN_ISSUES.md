# 알려진 엔진 오류

구현 세션 소유. 재현이 확인됐지만 아직 고치지 않은 오류를 적는다. 지원 범위 밖으로 **결정한** 구문은 여기가
아니라 `evaluation/reviews/round9_operations.md` §9 에 있다. 할 일 순서는 `evaluation/ROADMAP.md`.

수치 기준: 타깃 486(`evaluation/Dataset/targets_final.json`), 2026-11-06 빌드·파일럿.

---

## I1. 증분 `update` 가 `⊥` 를 낸다 — 조용한 오분석

**증상.** 앞에 문장을 추가하면서 뒤 문장을 지우는 편집을 한 번의 `update` 로 넣으면 EXIT 값이 `⊥` 다. 같은
소스를 새로 분석하면 정상이다. 예외도 진단도 없다.

```solidity
// 이전                               // 이후
if (n > 5) {                          total = 9;
    total = 1;                        if (n > 5) {
}                                         total = 1;
total = total + 2;                    }
```

`n=[0,10], total=[0,0]` 에서 `update` 후 `⊥`, 새 분석 `[1,9]`.

**확인한 것.** 한 줄·여러 줄 조건 모두 재현된다. 2026-11-06 의 `_insert_lines` 변경 전에도 재현됐다(무관).
앞에 추가만 하거나 뒤를 지우지 않으면 정상이다. **원인은 조사하지 않았다.** `update` 가 modify → delete →
add 순으로 적용하므로 그 순서와 CFG 수술의 연결부터 볼 것(`engine_api.update`).

**재현.** `temp/test_multiline.py` 의 `EDITS` 에 이 쌍을 넣으면 실패한다.

---

## I2. 식별자 `from` 이 키워드로 파싱된다 — 타깃 107 차단

**증상.** Solidity 에서 `from` 은 합법 식별자인데 `Parser/Solidity.g4` 의 `identifier` 는 `Identifier` 토큰뿐이고
import 규칙의 `'from'` 이 키워드가 된다. `function b(address from, address to)` 가 잘못 파싱되어, 엉뚱한
함수의 파라미터가 상태변수를 가린다는 `UnsupportedShadowing` 으로 나타난다.

**규모.** `미지원: 함수 … 가린다` 190 단위, 타깃 107. 파일럿의 의존성 로드 실패 119 의 대부분이다. 원인이 `from`
임을 최소 재현으로 확인했다(`from` → `_from` 이면 통과). 190 단위가 **전부** 이 원인인지는 아직 세지 않았다.

**해결 방향.** `identifier` 에 `'from'` 을 허용하고 파서를 재생성한다(코어, 승인 필요). `'error'`·`'revert'`·`'global'`·
`'receive'`·`'fallback'` 등 문맥 키워드도 같은 문제가 있는지 함께 볼 것. 옛 전처리는 `from → _from` 치환으로 피했다.

---

## I3. constructor 의 EXIT 줄이 따라오지 않는다 — S2 위반 102 함수

**증상.** constructor 본문이 있으면 EXIT 가 닫는 `}` 가 아니라 **첫 문장 줄**에 남아 착지 노드 둘이 한 줄에 놓인다.
일반 함수는 정상이다.

```
contract C {                      // constructor(uint256 x) {  → 3행
    ...                           //     a = x;                → 4행, Assign_4
    constructor(uint256 x) {      // }                         → 5행
        a = x;                    // EXIT 가 5행이 아니라 4행
```

**원인(유력, 미검증).** `_shift_cfg_meta` ③ 이 `iter_all_functions` 로 노드 줄을 옮기는데 constructor 가 여기서
빠지는 것으로 보인다. ① 은 줄 메타가 먼저 옮겨진 뒤라 동작하지 않는다.

---

## I4. `revert CustomError()` 가 한 줄에 착지 노드 둘을 만든다 — S2 위반 17 함수

**증상.** `revert_N` 과 `FuncCall_N` 이 같은 줄에 착지 가능한 노드로 생긴다. 예: `ModuleGlobals.onlyGov`.

---

## I5. 빌드·분석 실패 — 원인 미분류

2026-11-06 빌드에서 I2 외에 남은 항목. 개별 조사 전이다.

- `Unhandled expression context` 31 단위 (26 타깃). 예: `if` 줄.
- `numeric literal "0.95" does not evaluate to an integer` 10 단위. 상수 식 평가.
- `Function context not found` / `Contract context not found` 다수. I2 등 앞선 파싱 실패의 연쇄일 수 있다.
- 파일 수준 struct 미해석: `ReserveConfigurationMap` 24, `AdditionalRecipient` 5.
- `Type '…' is not defined as struct, enum, or contract`: 부모 컨트랙트의 struct 로 보인다.

---

## I6. 여러 줄 문장 안의 라인 주석이 뒤를 삼킨다 — 에디터 경로

**증상.** 슬라이서가 줄을 공백으로 잇는데 `//` 를 지우지 않아 `g(n, // first` ⏎ `1, 2);` 가 `function(⊤)` 이 된다. 예외가
없다. 데이터셋 경로는 `normalize_source` 가 주석을 비워 해당 없다. **에디터 입력만** 남는다.

solc 컴파일 게이트로는 막을 수 없다. 이 소스는 합법이기 때문이다. `engine_api.Diagnostic` 주석에 따르면 solc
게이트는 구현됐다가 꺼진 것이 아니라 **보류**된 상태고 코드가 없다.
