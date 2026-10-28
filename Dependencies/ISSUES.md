# Dependencies 사전분석 이슈

## 현재 상태 (2026-03-27)

### 정상 등록
- Interfaces: 46개 (IBasePoolV2, IVaderPoolV2 추가)
- Libraries: 17개 (Float 포함 전부 함수 body 빌드 완료, 0 empty)
- Contracts: 18개

### 정상적으로 0 functions인 interfaces
| Interface | 이유 |
|-----------|------|
| IMochi | `interface IMochi is IERC20 {}` — 빈 interface |
| IPreparable | event만 정의, 함수 없음 |
| IPooledCreditLineEnums | enum만 정의 |
| IPooledCreditLineDeclarations | struct/enum만 정의 |

---

## 해결된 이슈

### Issue A/B: FloatStruct file-level struct — ✅ 해결 (2026-03-26)
- Phase 0에서 file-level struct 사전 수집 (`scan_file_level_structs`)
- `sa.file_level_structs`에 주입
- `StaticCFGFactory.make_param_variable`에서 file-level struct fallback 추가
- Float library 11 functions 정상 빌드

### Issue C: float → FloatStruct rename — ✅ 해결 (2026-03-26)
- Dependencies 쪽 완료
- `web3bugs_42_H_01.sol` contraction에서 `float` → `FloatStruct` rename 완료

### Issue D: contracts/ 폴더 사전분석 — ✅ 해결 (2026-03-26)
- 18개 contract pkl 생성 완료
- parent pkl 로드로 상속 state variable 주입

### Issue E: IVaderPoolV2 interface — ✅ 해결 (2026-03-26)
- IVaderPoolV2.sol, IBasePoolV2.sol을 Dependencies/interfaces/에 추가 후 사전분석
- ExchangePair struct, Paths enum은 ILiquidityBasedTWAP.pkl에 이미 포함

---

## 미해결 이슈

### Issue F: ERC1155Upgradeable 사전분석 필요

**영향 케이스**: web3bugs_101_H_01 (LenderPool._calculatePrincipalWithdrawable)

**필요한 contract**:
- `ERC1155Upgradeable` — `balanceOf(address, uint256)` 함수 제공
- LenderPool이 ERC1155Upgradeable 상속 → `balanceOf` internal call

**현재 상태**:
- `ERC1155Upgradeable` pkl 미존재 → parent contract CFG 없음 → `balanceOf` 해석 불가
- NO_RESULT 상태 (intent 동작, debug annotation 반영, 하지만 balanceOf 결과가 top)

**해결 방안**:
1. ERC1155Upgradeable.sol contraction 후 Dependencies/contracts/에 추가 → 사전분석
2. 또는 contraction에 `balanceOf` 함수 직접 포함

---

## 남은 한계 (당장 블로커 아님)
- Assembly 함수 (AddressUpgradeable.functionCall 등): annotated 케이스에서 미사용
- Virtual/abstract 빈 함수: 정상 동작
- constant/immutable 변수 state_variable_node 미등록: not_detectable 케이스만 해당

---

# RQ2 실험 진행 현황 (2026-03-27)

## 이번 세션 엔진 수정 사항

### 1. Overload 함수 구분 (ContractAnalyzer, CFG)
- `find_function_context()`: 함수명 + param_types 튜플 반환 (multi-line 대응 포함)
- `analyze_context()`: `current_target_function_param_types` 저장
- `get_function_cfg()` 30곳: `param_types` 전달
- type alias resolve (UFixed18 → uint256 등)

### 2. Cross-library 호출 (Dependencies/main.py, Evaluation.py)
- `_global_library_cfgs` 누적 주입 + library 분석 순서 조정
- Qualified library call (`LibName.func()`) — identifier에서 library marker 반환 + member access에서 library CFG 조회
- `callerContext` 분기에서도 library 이름 인식

### 3. Struct 인자 전달 (Evaluation.py, Engine.py)
- 함수 호출 시 struct 인자: `.members` deepcopy
- identifier evaluate 시 StructVariable 객체 자체 반환
- `caller_env` 병합: 덮어쓰기 → `if k not in` 방식

### 4. Interface type cast (EnhancedSolidityVisitor.py, Evaluation.py, AddressSet.py)
- `visitFunctionCall`에서 interface/contract 이름이면 `TypeConversion` Expression 생성
- `evaluate_type_conversion_context`에서 interface cast → AddressSet + `_cast_interface` 태깅
- AddressSet `__slots__`에 `_cast_interface` 추가
- member access에서 cast된 interface 함수 호출 → `InterfaceFunctionCallContext`
- function call에서 interface function return type lookup (pkl에서) → top 반환

### 5. MetaType (EnhancedSolidityVisitor.py)
- `visitMetaType`에서 `expr.typeName` 설정 → `type(int256).max` 정상 동작

### 6. Mapping struct value 지원 (ContractAnalyzer.py)
- state/local MappingVariable 생성 시 `struct_defs`/`enum_defs` 주입
- file-level struct + parent 체인 포함

### 7. Modifier placeholder variables (DynamicCFGBuilder.py)
- `build_modifier_placeholder`에서 새 node에 `cur_block.variables` 복사

### 8. For문 grammar 수정 (EnhancedSolidityVisitor.py)
- `visitInteractiveForStatement`: condition은 `ctx.expressionStatement().expression()`, update는 `ctx.expression()`

### 9. Refine 미지원 operator 스킵 (Refine.py)
- 함수 호출 등 refine 불가능한 operator → `return` (sound overapproximation)

### 10. Logical operator BoolInterval 변환 (Evaluation.py)
- `&&`/`||` 연산에서 non-BoolInterval 피연산자 → `BoolInterval.top()` 변환

### 11. Narrowing unreachable node 스킵 (Engine.py)
- fixpoint narrowing에서 widening 미도달(out_vars=None) node 제외

### 12. Node variables 백업/복원 (Engine.py)
- `_interpret_function_cfg_impl`에서 시작 시 node variables 백업, 종료 시 복원
- debug annotation `flush()` 후 definition-time node 상태 보존

### 13. Snapshot restore 수정 (Snapshot.py)
- `restore_from_snap`에서 실제 객체 `__dict__` 복원 추가

### 14. Parent state variable 주입 (StaticCFGFactory.py)
- `make_function_cfg`, `make_modifier_cfg`에서 parent 체인 순회하여 state variable 주입

### 15. Dependencies pkl 로드 확장 (main.py)
- `load_dependencies()`에서 `ifc_*.pkl`, `lib_*.pkl`, `con_*.pkl` 전부 로드
- contract pkl: prefix 제거 후 `contract_cfgs`에 등록

### 16. SafeMathUpgradeable 순서 (Dependencies/contracts)
- 3-arg overload를 2-arg 위로 reorder (forward reference 해결)

### 17. Update.py struct 반환
- `update_left_var_of_member_access_context`에서 mapping entry가 StructVariable이면 바로 반환

---

## 케이스별 실행 결과 (19/21건, 42_H_01/78_H_02 미생성) — 2026-03-30 최신

| # | Case | Status | 비고 |
|---|------|--------|------|
| 1 | WANGMI | ✅ VIOLATED (V=1) | |
| 2 | Nokon | ⚠️ WARNING (W=1) | |
| 3 | SwordCrowdsale | ✅ VIOLATED (V=2) | |
| 4 | BoostToken_operator | ✅ VIOLATED (V=2) | |
| 5 | BoostToken_indivisible | ✅ VIOLATED (V=4) | |
| 6 | HIT | ❌ ERROR | ImplicationContext.commonClause (보류) |
| 7 | 5_H_07 | ✅ VIOLATED (V=1) | |
| 8 | 5_H_08 | ✅ VIOLATED (V=1) | |
| 9 | 5_H_12 | ✅ VIOLATED (V=1) | |
| 10 | 77_H_01 | ✅ VIOLATED (V=1) | |
| 11 | 101_H_01 | ⚠️ WARNING (W=1) | Issue F(ERC1155) 필요 |
| 12 | 45_H_01 | ✅ VIOLATED (V=2) | 세션3에서 해결 |
| 13 | 47_H_02 | ❌ ERROR | Type 'ERC20Upgradeable' — 미해결 |
| 14 | 51_H_02 | ❌ ERROR | Type 'LPToken' — 미해결 |
| 15 | 56_H_02 | ❌ ERROR | struct member 'getEarnedYield' — 미해결 |
| 16 | 58_H_02 | ⚠️ WARNING (W=1) | 세션3에서 해결 (violated 여부 annotation 검토 필요) |
| 17 | 60_H_01 | ❌ ERROR | KeyError: None — 미분석 |
| 18 | 62_H_08 | ❌ ERROR | Modifier 'governed' — 미해결 |
| 19 | 70_H_10 | ❌ ERROR | Type 'ExchangePair' — 미해결 |

**10 VIOLATED + 3 WARNING + 6 ERROR = 19건**

---

## 세션 3 완료 사항 (2026-03-30)

### 1. Mapping key 통일 — AddressSet 값 기반 (45_H_01 해결)
- **5곳 수정**: Evaluation.py, Update.py, DebugInitializer.py
- global var(msg.sender)가 mapping index일 때 리터럴 `"msg.sender"` 대신 `str(AddressSet({101}))` 사용
- annotation `accountBorrows[msg.sender]`, callee의 `accountBorrows[account]` 모두 동일 key 수렴
- JSON 재생성 (clean contraction .sol에서 soltotestjson.py)

### 2. `top_from_soltype` 범용 유틸리티 (Helper.py)
- SolType → top-valued domain object 생성 (struct, enum, array, mapping, interface, elementary 전부 지원)
- interface function return에서 모든 타입에 대해 proper domain value 반환

### 3. Interface struct return 지원
- InterfaceFunctionCallContext에서 `top_from_soltype`으로 StructVariable 반환
- parent interface chain 검색 (`_lookup_interface_return` — BFS)
- `resolve_library_struct`에서 interface/contract pkl도 struct/enum 조회

### 4. Interface 타입 전반 지원 강화
- `top_from_soltype`, `initialize_struct._make_var`, `MappingVariable._make_value`, `Engine._interpret_var_decl`: interface typeCategory → AddressSet.top() + `_cast_interface`
- `AddressSet.join/meet/narrow`: `_cast_interface` 보존 (`getattr` safe access)
- `_make_bottom`: AddressSet bottom 시 `_cast_interface` 타입 정보 보존
- `evaluate_identifier_context`: composite 타입(Struct/Array/Mapping) 객체 직접 반환

### 5. @IReturn Grammar 일반화
- `debugIReturn` rule: 기존 4개 → PatternA/B 2개 + `ireturnAccessChain` (member/index/chained call)
- Visitor: `_parse_ireturn_access_chain` — `("member", name)`, `("index", int)`, `("call", name)`
- Registry key: `(contract_var, func_name, access_chain_tuple)` 형식
- Evaluation: `_assemble_ireturn_value` — chain 따라 struct member 설정, `_collect_ireturn_entries`
- `_resolve_ireturn_pattern_a/b` 헬퍼

### 6. Library constant 조회 (Evaluation.py)
- `evaluate_member_access_context`에서 library의 `state_variable_node.variables`에서 상수 조회
- CommonLibrary.PRICE_DENOMINATOR, DENOMINATOR, YEAR 등 해결

### 7. Dependency 사전분석 확장 (Dependencies/main.py)
- `_load_parent_pkls`: 모든 모드(contract, interface, library)에서 호출
- regex: `contract|interface|library` 모두 매칭
- interface 결과 추출: `_pre_existing_all` 기반으로 새로 분석된 interface만 식별
- ILpIssuerGovernance.pkl 재생성 (IVaultGovernance parent 포함)

### 8. Refine non-l-value skip (Refine.py)
- `_has_non_lvalue_in_chain`: FunctionCall, BinaryExp, UnaryExp, Literal, Tuple, TypeConversion 등
- operator 기반 체크: `+`, `-`, `*`, `/` 등
- `_maybe_update`에서 non-l-value expression narrowing 방지

### 9. BoolInterval.widen 시그니처 통일 (Interval.py)
- `widen(self)` → `widen(self, current_interval=None)`

### 10. SolidityAnalyzer._insert_lines shift 수정
- `skip_shift_at_start=True`일 때 `actual_offset = offset - 1` (start 라인 재사용 시 1줄 적게 shift)
- baseTvls loop 이후 코드가 loop 내부로 잘못 포함되던 CFG 구축 문제 해결

### 11. ContractAnalyzer._find_interface_name_for_var 수정
- `func_cfg.variables` → `func_cfg.related_variables`

---

## 다음 작업 (TODO)

### 우선순위 1: 58_H_02 annotation 검토
- WARNING(W=1) → VIOLATED 되어야 하는지 annotation 값 재검토 필요
- `toMint = [689655172..., TOP]`, `baseSupply = [1e21]` → overlap 여부

### 우선순위 2: 타입 미인식 문제
- 47_H_02: `ERC20Upgradeable` parent contract 타입
- 51_H_02: `LPToken` contract 타입
- 70_H_10: `ExchangePair` struct
- contraction .sol에서 해당 타입 인식 필요

### 우선순위 3: 기타 에러
- 56_H_02: `'getEarnedYield' not in struct '_self'` — struct member가 interface function call 결과
- 60_H_01: `KeyError: None` — 원인 미분석
- 62_H_08: `Modifier 'governed' not defined` — modifier dependency

### 우선순위 4: 미생성 케이스
- 42_H_01: FloatStruct rename 완료, JSON 생성 필요
- 78_H_02: ERC20 상속 private state variable 문제

### 우선순위 5: Implication 구현
- HIT 케이스: `ImplicationContext.commonClause` 미구현

### 우선순위 6: Dependency 추가
- Issue F: ERC1155Upgradeable (101_H_01용)
