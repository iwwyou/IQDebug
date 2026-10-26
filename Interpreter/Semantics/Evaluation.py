from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:                                         # 타입 검사 전용
     from Analyzer.ContractAnalyzer import ContractAnalyzer

from Domain.FunctionRef import FunctionRef
from Domain.Variable import Variables, ArrayVariable, StructVariable, MappingVariable, EnumVariable, EnumDefinition, array_length
from Domain.Type import SolType
from Domain.Interval import Interval, IntegerInterval, BoolInterval, UnsignedIntegerInterval
from Domain.AddressSet import AddressSet
from Domain.BytesSet import BytesSet
from Domain.IR import Expression

from Utils.Helper import VariableEnv
from Interpreter.Semantics.Storage import _join_into, is_composite

from decimal import Decimal, InvalidOperation
import re
import copy

def _is_yul_ctx(expr) -> bool:
    """이 표현식이 Yul(inline assembly) 문맥에서 만들어졌는가.
    EnhancedYulVisitor 가 컨텍스트 이름에 'Yul' 접두를 단다."""
    c = getattr(expr, "context", None)
    return isinstance(c, str) and c.startswith("Yul")


# 값이 아닌 이름 — `X.f()` 의 X 로 오면 using-for 대상이 아니다 (Evaluation._is_non_typed_receiver)
_NON_VALUE_NAMES = frozenset({"super", "this", "msg", "block", "tx", "abi"})


def _reinterpret_as(iv, target_cls):
    """구간을 2의 보수 256-bit word 로 보고 target_cls 로 재해석.

    부호 비트가 구간 안에서 변하지 않으면(음수가 없거나, unsigned 가 signed 범위 안)
    **값 범위가 그대로**라 손실이 없다. 그렇지 않으면 비트 패턴이 갈라지므로
    target_cls 의 TOP(건전)을 준다.
    """
    if iv.is_bottom():
        return target_cls(None, None, iv.type_length)
    w = iv.type_length
    lo, hi = iv.min_value, iv.max_value
    if target_cls is UnsignedIntegerInterval:
        if lo >= 0:                      # 음수 없음 → 비트 패턴 동일
            return UnsignedIntegerInterval(lo, hi, w)
        return UnsignedIntegerInterval.top(w)
    if hi <= (1 << (w - 1)) - 1:         # signed 표현 범위 안 → 동일
        return IntegerInterval(lo, hi, w)
    return IntegerInterval.top(w)


def _is_numeric_literal(expr) -> bool:
    """이 표현식이 Solidity 숫자 **리터럴**인가 (식별자·연산 결과가 아니라)."""
    if getattr(expr, "context", None) != "LiteralExpContext":
        return False
    lit = getattr(expr, "literal", None)
    if not isinstance(lit, str):
        return False
    t = lit.replace("_", "")
    return t.isdigit() or (t[:2].lower() == "0x" and len(t) > 2)


def _literal_retype(sub_expr, iv, target_cls):
    """숫자 리터럴 구간을 상대 피연산자 타입(target_cls)으로 재해석.

    ★ Solidity 에서 숫자 리터럴은 **타입이 없는 유리수**다 — 타입은 문맥,
      즉 상대 피연산자가 정한다. `int256 x, y` 에 대한 `x & y & 1` 에서 `1` 은
      `int256(1)` 로 변환되는 것이 언어 규칙이다.

      엔진은 visitLiteralExp 에서 모든 숫자 리터럴을 uint256 으로 못박는데
      (음수 리터럴은 없고 단항 '-' 로 표현되므로 그 자체는 옳다), 그 결과
      signed 피연산자와 만나면 타입 검사에서 걸렸다
      (PRBMathSD59x18.sol:27 `result = (x >> 1) + (y >> 1) + (x & y & 1);`).

      리터럴은 단일 상수라 대상 타입 범위에 들어가기만 하면 **정확히 같은 값**
      이다 — 근사가 아니다. 범위를 벗어나면 실제 Solidity 도 컴파일 에러이므로
      None 을 돌려 호출부가 예외를 내게 한다(⊤ 로 덮지 않는다).

      Yul 경로의 _reinterpret_as 와는 별개다: 저쪽은 '타입이 없는 word 의 2의
      보수 재해석'(정보 손실 가능 → TOP), 이쪽은 '타입 없는 리터럴에 문맥
      타입 부여'(손실 없음).
    """
    if type(iv) is target_cls or not _is_numeric_literal(sub_expr):
        return None
    if iv.is_bottom():
        return target_cls(None, None, iv.type_length)
    w = iv.type_length
    lo, hi = iv.min_value, iv.max_value
    if target_cls is IntegerInterval:
        b_lo, b_hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
    elif target_cls is UnsignedIntegerInterval:
        b_lo, b_hi = 0, (1 << w) - 1
    else:
        return None
    if lo < b_lo or hi > b_hi:
        return None                      # 대상 타입에 안 들어감 → 호출부에서 예외
    return target_cls(lo, hi, w)


_ARITH_OPS = frozenset({'+', '-', '*', '/', '%', '&', '|', '^'})


def _is_constant_expr(expr) -> bool:
    """숫자 리터럴과 연산자로만 이루어진 식(Solidity 의 rational constant — `10**18`, `1 << 128`, `-1`).
    이런 식은 **타입이 없고** 상대 피연산자/대입 대상의 타입으로 변환된다."""
    if expr is None:
        return False
    if getattr(expr, "context", None) == "LiteralExpContext":
        return _is_numeric_literal(expr)
    if getattr(expr, "left", None) is not None and getattr(expr, "right", None) is not None:
        return _is_constant_expr(expr.left) and _is_constant_expr(expr.right)
    if getattr(expr, "operator", None) in ('-', '~') and getattr(expr, "expression", None) is not None \
            and getattr(expr, "left", None) is None:
        return _is_constant_expr(expr.expression)
    return False


def _int_bounds(cls, w: int) -> tuple[int, int]:
    if cls is IntegerInterval:
        return -(1 << (w - 1)), (1 << (w - 1)) - 1
    return 0, (1 << w) - 1


def coerce_to_declared(value, sol_type, what: str = "value"):
    """Solidity **암묵 변환**: 정수 값을 선언 타입(`sol_type`)의 폭·부호로 바꾼다. (Phase 4b, 2026-10-22 승인)

    값을 저장하는 모든 지점(선언 초기화·대입·튜플·인자 바인딩·반환값·멤버/원소 쓰기)에서 부른다.
    Solidity 는 손실 없는 변환(같은 부호에서 넓히기, 범위에 맞는 상수)만 암묵 허용하므로 **값 범위는 바뀌지 않고 타입만 바뀐다.**
    예전엔 변환이 없어 `(uint256 r0, …) = pair.getReserves()` 의 r0 가 uint112 폭을 그대로 들고 다녔고,
    산술이 그 폭에서 overflow 해 112비트 ⊤ 가 됐다(실제 값은 256비트 — **unsound**; web3bugs_70_H_04/10).
    값이 선언 타입 범위에 안 들어가면 컴파일러가 거부했을 코드이거나 엔진의 타입 오류이므로 **예외**(폴백 없음).
    정수가 아닌 값·정수가 아닌 선언 타입은 그대로 돌려준다.
    """
    if not isinstance(value, (IntegerInterval, UnsignedIntegerInterval)):
        return value
    et = getattr(sol_type, "elementaryTypeName", None) if sol_type is not None else None
    if getattr(sol_type, "typeCategory", "elementary") != "elementary" or not isinstance(et, str):
        return value
    if et.startswith("uint"):
        cls = UnsignedIntegerInterval
    elif et.startswith("int"):
        cls = IntegerInterval
    else:
        return value
    w = getattr(sol_type, "intTypeLength", None) or 256
    if type(value) is cls and value.type_length == w:
        return value
    if value.is_bottom():
        return cls(None, None, w)
    lo, hi = _int_bounds(cls, w)
    if value.min_value < lo or value.max_value > hi:
        raise ValueError(f"implicit conversion of {what} {value} to {et} does not fit "
                         f"(Solidity 는 손실 있는 암묵 변환을 허용하지 않음 — 타입 오류)")
    return cls(value.min_value, value.max_value, w)


def _unify_operand_types(expr, L, R):
    """이항 산술·비트 연산의 **공통 타입** (Phase 4b). 예전엔 왼쪽 피연산자의 폭을 그대로 썼다.
      · 한쪽이 상수식(리터럴)이면 상대 타입으로 변환(범위 밖이면 Solidity 도 컴파일 오류 → 예외)
      · 둘 다 타입이 있고 부호가 같으면 넓은 쪽 폭(좁은 쪽이 암묵적으로 넓어진다)
      · 둘 다 상수식이면 그대로(상수 산술)
    부호가 다른 비상수 피연산자 조합은 기존 동작 유지(별도 검토 대상)."""
    if not (isinstance(L, (IntegerInterval, UnsignedIntegerInterval))
            and isinstance(R, (IntegerInterval, UnsignedIntegerInterval))):
        return L, R
    lc, rc = _is_constant_expr(expr.left), _is_constant_expr(expr.right)
    if lc and rc:
        return L, R

    def _to(v, cls, w, side):
        if v.is_bottom():
            return cls(None, None, w)
        lo, hi = _int_bounds(cls, w)
        if v.min_value < lo or v.max_value > hi:
            raise ValueError(f"constant operand {v} of '{expr.operator}' does not fit "
                             f"{'int' if cls is IntegerInterval else 'uint'}{w} (compile error)")
        return cls(v.min_value, v.max_value, w)

    if rc:
        return L, _to(R, type(L), L.type_length, "right")
    if lc:
        return _to(L, type(R), R.type_length, "left"), R
    if type(L) is type(R) and L.type_length != R.type_length:
        w = max(L.type_length, R.type_length)
        return type(L)(L.min_value, L.max_value, w) if not L.is_bottom() else type(L)(None, None, w), \
            type(R)(R.min_value, R.max_value, w) if not R.is_bottom() else type(R)(None, None, w)
    return L, R


class Evaluation :

    def __init__(self, analyzer: "ContractAnalyzer"):
        # ContractAnalyzer 인스턴스만 보관해 두고,
        # 나머지 컴포넌트는 필요할 때 property 로 접근합니다.
        self.an = analyzer

    # ── lazy properties ──────────────────────────────────────────────
    @property
    def up(self):
        return self.an.updater          # Update 싱글톤

    @property
    def engine(self):
        return self.an.engine          # Engine 싱글톤

    # ──────────────────── Helper functions ───────────────────────────

    def _find_enum_in_chain(self, name: str):
        """현재 contract + parent chain에서 EnumDefinition 검색. 없으면 None."""
        cfg = self.an.contract_cfgs.get(self.an.current_target_contract)
        return self._search_enum_recursive(cfg, name) if cfg else None

    @staticmethod
    def _search_enum_recursive(cfg, name):
        defs = getattr(cfg, 'enumDefs', {})
        if name in defs:
            return defs[name]
        for pcfg in getattr(cfg, 'parent_cfgs', {}).values():
            result = Evaluation._search_enum_recursive(pcfg, name)
            if result:
                return result
        return None

    def _get_interface_name_of_var(self, var_name: str, variables: dict) -> str | None:
        """
        변수(function parameter 또는 state variable)의 typeInfo에서
        interface 이름을 반환. interface 타입이 아니면 None.
        """
        # 1) 현재 함수의 variables (parameter 포함)
        if var_name in variables:
            var = variables[var_name]
            if hasattr(var, 'typeInfo') and var.typeInfo and var.typeInfo.typeCategory == "interface":
                return var.typeInfo.interfaceName

        # 2) FunctionCFG.interface_var_types (지역변수 중 interface 타입)
        fcfg = self.an.current_target_function_cfg
        if fcfg and hasattr(fcfg, 'interface_var_types') and var_name in fcfg.interface_var_types:
            return fcfg.interface_var_types[var_name]

        # 3) state variable
        current_contract = self.an.current_target_contract
        if current_contract and current_contract in self.an.contract_cfgs:
            ccf = self.an.contract_cfgs[current_contract]
            if hasattr(ccf, 'state_variables') and var_name in ccf.state_variables:
                var = ccf.state_variables[var_name]
                if hasattr(var, 'typeInfo') and var.typeInfo and var.typeInfo.typeCategory == "interface":
                    return var.typeInfo.interfaceName

        return None

    def _collect_ireturn_entries(self, registry, match_prefix):
        """registry에서 match_prefix로 시작하는 (contract_var, func_name) entries 수집.
        Returns: dict { access_chain_tuple: value }
        """
        entries = {}
        for key, val in registry.items():
            if key[:len(match_prefix)] == match_prefix:
                chain = key[len(match_prefix)]  # access_chain tuple
                entries[chain] = val
        return entries

    def _assemble_ireturn_value(self, interface_name, func_name, entries, callerObject):
        """@IReturn entries로부터 반환값 조립.
        entries: { access_chain_tuple: value }
        - () → 단일 값
        - (("member", "x"),) → struct member
        - (("index", 0),) → tuple index
        """
        # 단일 반환 (access_chain이 빈 tuple)
        if () in entries and len(entries) == 1:
            return self._mapping_lookup_if_needed(entries[()], callerObject)

        # struct member 또는 index가 있는 경우 → return type 조회 후 조립
        # (Phase 4b) 예전엔 `Dependencies/objectfile/ifc_<T>.pkl` 을 하드코딩해 읽고, 없으면 조용히 None 을 돌렸다.
        #   로드된 의존성(`contract_cfgs`)만 본다 — `_lookup_interface_return` 주석 참고.
        ifc_cfg = (getattr(self.an, 'contract_cfgs', {}) or {}).get(interface_name)
        if ifc_cfg is None:
            raise ValueError(f"@IReturn: interface '{interface_name}' 의 선언을 찾지 못함 (의존성 누락)")
        ifc_fcfg = ifc_cfg.get_function_cfg(func_name)
        if not ifc_fcfg or not ifc_fcfg.return_types:
            return None

        # ★ file-level struct(예: `struct FloatStruct {...}`가 인터페이스/컨트랙트
        # 밖, 파일 최상위에 선언된 경우, `IMochiProfile.liquidationFactor()`처럼
        # 그 인터페이스의 함수가 반환 타입으로만 참조함) 병합 — `ifc_cfg.structDefs`는
        # 그 인터페이스 "안에" 선언된 struct만 담고 있어서 빠짐, 안 채우면
        # `top_from_soltype`이 멤버 없는 빈 StructVariable을 만듦
        # (web3bugs_42_H_01에서 발견, `'denominator' not in struct 'lf'`).
        struct_defs = dict(getattr(self.an.sa, 'file_level_structs', {}) or {})
        struct_defs.update(getattr(ifc_cfg, 'structDefs', {}) or {})
        enum_defs = getattr(ifc_cfg, 'enumDefs', {})

        # top 값 생성
        #   다중 반환이면 반환 타입별 ⊤ 의 리스트 (Phase 3b: 예전엔 첫 반환 타입만 만들었다)
        def _top(rt, ident):
            tv = VariableEnv.top_from_soltype(rt, struct_defs, enum_defs, identifier=ident)
            # Variables wrapper → value 추출 (elementary)
            if isinstance(tv, Variables) and \
               not isinstance(tv, (StructVariable, ArrayVariable, MappingVariable, EnumVariable)):
                tv = tv.value
            return tv
        if len(ifc_fcfg.return_types) > 1:
            top_val = [_top(rt, f"{interface_name}.{func_name}_ret{i}")
                       for i, rt in enumerate(ifc_fcfg.return_types)]
        else:
            top_val = _top(ifc_fcfg.return_types[0], f"{interface_name}.{func_name}_ret")

        # entries의 access chain을 따라 값 설정
        for chain, val in entries.items():
            if chain == ():
                top_val = val  # 전체 덮어쓰기
                continue
            target = top_val
            for i, step in enumerate(chain):
                is_last = (i == len(chain) - 1)
                if step[0] == "member":
                    if isinstance(target, StructVariable) and step[1] in target.members:
                        if is_last:
                            member_var = target.members[step[1]]
                            if hasattr(member_var, 'value'):
                                member_var.value = val
                        else:
                            target = target.members[step[1]]
                    else:
                        break  # member를 찾지 못함
                elif step[0] == "index" and isinstance(target, list):
                    # 다중 반환 값의 i 번째 (Phase 3b)
                    idx = step[1]
                    if not (0 <= idx < len(target)):
                        raise ValueError(f"@IReturn {interface_name}.{func_name}()[{idx}]: "
                                         f"function returns {len(target)} values")
                    if is_last:
                        target[idx] = val
                    else:
                        target = target[idx]
                elif step[0] == "index":
                    if isinstance(target, ArrayVariable):
                        idx = step[1]
                        if target.in_static_range(idx):
                            if is_last:
                                elem = target.get_or_create_element(idx)
                                if hasattr(elem, 'value'):
                                    elem.value = val
                            else:
                                target = target.get_or_create_element(idx)
                    else:
                        break
                elif step[0] == "call":
                    # chained interface call: target이 Variables이고
                    # value가 AddressSet + _cast_interface면 해당 interface 함수 반환값 설정
                    call_name = step[1]
                    addr_val = target.value if isinstance(target, Variables) else target
                    cast_ifc = getattr(addr_val, '_cast_interface', None)
                    if cast_ifc and is_last:
                        # 이 chained call의 반환값을 별도 registry에 저장하지 않고
                        # 직접 top_val 수준에서 처리 (evaluation 시 _lookup_interface_return으로 조회)
                        # → chained call은 registry에 full chain으로 저장되어 있으므로
                        #   evaluation 시점에서 처리됨
                        pass  # 값은 이미 registry에 chain 전체로 저장됨
                    elif cast_ifc and not is_last:
                        # 중간 chained call: 반환 타입으로 top 생성하여 다음 step 진행
                        ret_val = self._lookup_interface_return(cast_ifc, call_name)
                        if ret_val is None:
                            raise ValueError(f"@IReturn chain: {cast_ifc}.{call_name}() returns nothing "
                                             f"but the chain continues")
                        target = ret_val
                    else:
                        break

        return self._mapping_lookup_if_needed(top_val, callerObject)

    def _resolve_ireturn_pattern_a(self, fcfg, interface_name, contract_var, func_name, callerObject):
        """Pattern A: contractVar.funcName().<chain> 조회"""
        if not fcfg.ireturn_registry:
            return None
        entries = self._collect_ireturn_entries(
            fcfg.ireturn_registry, (contract_var, func_name))
        if not entries:
            return None
        return self._assemble_ireturn_value(interface_name, func_name, entries, callerObject)

    def _resolve_ireturn_pattern_b(self, fcfg, interface_name, addr_var, func_name, callerObject):
        """Pattern B: Interface(addr).funcName().<chain> 조회"""
        if not fcfg.ireturn_registry:
            return None
        entries = self._collect_ireturn_entries(
            fcfg.ireturn_registry, (interface_name, addr_var, func_name))
        if not entries:
            return None
        return self._assemble_ireturn_value(interface_name, func_name, entries, callerObject)

    def _top_of_type(self, sol_type, owner_cfg, ident):
        """선언 타입의 ⊤ 값 (값 타입이면 .value, 복합 타입이면 변수 객체)."""
        # ★ file-level struct 병합 — _assemble_ireturn_value와 동일 이유
        # (web3bugs_42_H_01, `'denominator' not in struct 'lf'`)
        struct_defs = dict(getattr(self.an.sa, 'file_level_structs', {}) or {})
        struct_defs.update(getattr(owner_cfg, 'structDefs', {}) or {})
        enum_defs = getattr(owner_cfg, 'enumDefs', {}) or {}
        top_val = VariableEnv.top_from_soltype(sol_type, struct_defs, enum_defs, identifier=ident)
        if isinstance(top_val, Variables) and \
           not isinstance(top_val, (StructVariable, ArrayVariable, MappingVariable, EnumVariable)):
            return top_val.value
        return top_val

    def _executing_contract_cfg(self):
        """지금 실행 중인 함수가 속한 컨트랙트/라이브러리 CFG — using-for 조회 범위(Solidity: using 은 선언된
        컨트랙트·라이브러리 안에서만 유효). 함수 밖(빌드 시점 상태변수 초기화식 평가 등)이면 지금 구성 중인 컨트랙트.
        실행 중인 함수의 소속을 못 찾으면 오류 — 엉뚱한 컨트랙트의 using 목록으로 대신하지 않는다."""
        fcfg = self.an.current_target_function_cfg
        if fcfg is None:
            return (self.an.contract_cfgs or {}).get(self.an.current_target_contract)
        cname, _ = self.engine._fcfg_owner(fcfg)
        if cname is None:
            raise ValueError(f"using-for lookup: owner of executing function "
                             f"'{getattr(fcfg, 'function_name', '?')}' not found")
        return (self.an.contract_cfgs or {}).get(cname) or (self.an.library_cfgs or {}).get(cname)

    def _using_for_on_cast(self, type_name, member, implicit_arg):
        """`T(x).f(...)` 에서 T 가 f 를 선언하지 않았으면 using-for 로 T 에 붙은 라이브러리 함수 호출 표현을 만든다
        (2026-10-22 — 예전엔 무조건 외부 호출로 보내 "반환 타입 미해결" 로 멈췄다). T 가 f 를 선언했거나 T 를 못 찾으면
        None(외부 호출 경로). 선언도 없고 using-for 라이브러리에도 없으면 오류 — 컴파일되는 코드면 둘 중 하나는 있다."""
        if self._type_declares_member(type_name, member) is not False:
            return None
        ccf = self._executing_contract_cfg()
        if ccf is None or not ccf.find_library_function(type_name, member):
            raise ValueError(f"member '{member}' is neither declared by '{type_name}' nor attached to it "
                             f"by a using-for library")
        result_expr = Expression(function=Expression(identifier=member), operator='library_call',
                                 context='LibraryFunctionCallContext')
        result_expr._library_base_type = type_name
        result_expr._library_contract_cfg = ccf
        result_expr._implicit_first_arg = implicit_arg
        return result_expr

    def _type_declares_member(self, type_name, member):
        """contract/interface 타입(부모 체인 포함)이 `member` 를 함수 또는 public getter 로 선언하는가.
        True/False, 타입 선언 자체를 못 찾으면 None. (`_lookup_interface_return` 과 같은 조회 범위)"""
        cfgs = getattr(self.an, 'contract_cfgs', {}) or {}
        if type_name not in cfgs:
            return None
        visited, queue = set(), [type_name]
        while queue:
            name = queue.pop(0)
            if name in visited or not name:
                continue
            visited.add(name)
            cfg = cfgs.get(name)
            if cfg is None:
                continue
            if cfg.get_function_cfg(member) is not None:
                return True
            svn = getattr(cfg, 'state_variable_node', None)
            if svn is not None and member in (getattr(svn, 'variables', {}) or {}):
                return True
            queue.extend(list(getattr(cfg, 'parent_contracts', []) or []) +
                         list((getattr(cfg, 'parent_cfgs', {}) or {}).keys()))
        return False

    def _lookup_interface_return(self, interface_name, func_name):
        """외부 호출 `T(addr).f()` / `t.f()` 의 반환값 = 선언 반환 타입의 ⊤ (2026-10-21 재작성, 사용자 승인).

        조회 순서 (각각 부모 체인 포함):
          1. 로드된 interface/contract/library CFG (`self.an.contract_cfgs`) — interface pkl 도 여기에 올라와 있다.
             T 가 interface 가 아니라 **contract** 인 경우도 같다 (예: `Ownable(address(factory)).owner()`, web3bugs_36_H_02)
          2. public 상태변수의 자동 getter (`factory.ownerSplit()` 처럼 함수가 아니라 상태변수인 경우)
        함수를 찾았는데 반환 타입이 없으면(void) **None** = 값 없음(정상).
        끝내 못 찾으면 **예외** — 컴파일되는 코드라면 선언이 반드시 있으므로 의존성 누락이다.
        예전엔 못 찾으면 호출부가 `UnsignedIntegerInterval.top()` 으로 대체했고, 그 숫자가 address 자리에 들어가
        `require(account != address(0))` 를 불가능으로 만들었다(36_H_02 Unreachable)."""
        # (Phase 4b) 예전엔 1. 단계에서 `Dependencies/objectfile/ifc_<T>.pkl` 경로를 **하드코딩**해 직접 읽었다.
        #   프로젝트 단위 의존성(IQ_DEPS_ROOT)으로 돌릴 때도 레거시 공용 트리의 같은 이름 interface 를 읽어
        #   다른 판의 타입(web3bugs_42_H_01: 공용 트리의 rename 된 `FloatStruct` ↔ 프로젝트의 `float`)이 섞였다.
        #   interface pkl 은 load_dependencies 가 두 모드 모두 `contract_cfgs` 에 올려 두므로 그것만 본다.
        visited = set()
        queue = [interface_name]
        while queue:
            name = queue.pop(0)
            if name in visited or not name:
                continue
            visited.add(name)
            cfg = (getattr(self.an, 'contract_cfgs', {}) or {}).get(name)
            if cfg is None:
                continue
            fcfg = cfg.get_function_cfg(func_name)
            if fcfg is not None:
                if not fcfg.return_types:
                    return None                                   # void 함수: 값 없음
                if len(fcfg.return_types) > 1:
                    # ★ (Phase 3b) 다중 반환 = 반환 타입별 ⊤ 의 리스트. 예전엔 첫 반환 타입 하나만 돌려줘
                    #   `(a, b, , , c) = feed.latestRoundData()` 의 튜플 대입이 (조용히) 무시됐다.
                    return [self._top_of_type(t, cfg, f"{name}.{func_name}_ret{i}")
                            for i, t in enumerate(fcfg.return_types)]
                return self._top_of_type(fcfg.return_types[0], cfg, f"{name}.{func_name}_ret")
            svn = getattr(cfg, 'state_variable_node', None)
            sv = (getattr(svn, 'variables', {}) or {}).get(func_name) if svn is not None else None
            if sv is not None and getattr(sv, 'typeInfo', None) is not None \
                    and not isinstance(sv, (MappingVariable, ArrayVariable)):
                return self._top_of_type(sv.typeInfo, cfg, f"{name}.{func_name}_getter")
            # parent chain 추가
            for pname in list(getattr(cfg, 'parent_contracts', []) or []) + \
                         list((getattr(cfg, 'parent_cfgs', {}) or {}).keys()):
                if pname not in visited:
                    queue.append(pname)
        raise ValueError(
            f"external call return type unresolved: {interface_name}.{func_name}() — "
            f"'{interface_name}' 의 선언(interface pkl / 로드된 contract / public getter)을 찾지 못함 (의존성 누락)")

    def _find_interface_struct_def(self, interface_name, struct_name):
        """interface pkl에서 struct 정의 조회 (parent chain 포함) — qualified
        네임스페이스 struct 생성자(예: `IPool.TokenAmount({...})`, struct가
        interface `IPool` 안에 정의된 경우)를 위한 헬퍼. `_lookup_interface_return`
        과 동일한 PKL 로딩 패턴 재사용.
        Returns: (struct_def, struct_defs, enum_defs) 또는 None"""
        visited = set()
        queue = [interface_name]
        while queue:
            ifc_name = queue.pop(0)
            if ifc_name in visited:
                continue
            visited.add(ifc_name)
            # (Phase 4b) 하드코딩 경로 대신 로드된 의존성을 본다 — `_lookup_interface_return` 주석 참고.
            ifc_cfg = (getattr(self.an, 'contract_cfgs', {}) or {}).get(ifc_name)
            if ifc_cfg is None:
                continue
            struct_defs = getattr(ifc_cfg, 'structDefs', {})
            if struct_name in struct_defs:
                enum_defs = getattr(ifc_cfg, 'enumDefs', {})
                return struct_defs[struct_name], struct_defs, enum_defs
            for pname in getattr(ifc_cfg, 'parent_contracts', []):
                if pname not in visited:
                    queue.append(pname)
        return None

    def _mapping_lookup_if_needed(self, result, callerObject):
        """
        callerObject가 MappingVariable이면 result를 key로 mapping lookup 수행.
        index access 내 함수 호출 등에서 사용.
        """
        if not isinstance(callerObject, MappingVariable) or result is None:
            return result

        mapping_var = callerObject
        if not mapping_var.struct_defs or not mapping_var.enum_defs:
            all_structs, all_enums = self.an.get_full_struct_enum_defs()
            mapping_var.struct_defs = {**all_structs, **mapping_var.struct_defs}
            mapping_var.enum_defs = {**all_enums, **mapping_var.enum_defs}

        # key 결정: 리턴값에서 추론
        if isinstance(result, AddressSet):
            key_val = str(result)
        elif hasattr(result, "min_value"):
            if result.min_value == result.max_value:
                key_val = str(result.min_value)
            else:
                key_val = f"func_result_{id(result)}"
        else:
            key_val = str(result)

        if key_val not in mapping_var.mapping:
            mapping_var.mapping[key_val] = mapping_var.get_or_create(key_val)
        mvar = mapping_var.mapping[key_val]
        if isinstance(mvar, (StructVariable, ArrayVariable, MappingVariable)):
            return mvar
        else:
            return mvar.value

    # =================================================================
    #  F37. 정적 타입 질의 + 오버로드 선택
    # =================================================================
    #  Solidity 는 정적 타입 언어이고 오버로드 해소는 **결정적**이다.
    #  그런데 이 엔진은 식(expression)의 정적 타입을 추적하지 않았다 —
    #  `Domain/IR.py:74` 에 `expr_type` 슬롯이 **설계돼 있는데** 비지터가
    #  리터럴과 디버그 어노테이션 합성 표현식에만 채운다(식별자·멤버·인덱스·
    #  호출·산술은 전부 `None`). 그 결손 때문에 F14(리터럴 문맥 타이핑)·
    #  F16(bytesN 상수)·F34(오버로드 arity)가 **각각 따로 우회로**를 깔았다.
    #
    #  아래 `_static_type_of` 는 그 결손을 메우는 **순수 질의**다:
    #    · 평가하지 않는다 → 매핑 엔트리 생성·중첩 호출 재해석 같은 부작용 0
    #    · IR 을 쓰지 않는다(`expr_type` 미변경) → 기존 리터럴 경로 동작 변화 0
    #  비지터가 `expr_type` 을 채우고 F14·F16 우회로를 걷어내는 것은 별건이다
    #  (`evaluate_literal_context` 의 `ety` 분기가 정확 타입 `uint256`·`bytes32` 도
    #   받게 먼저 고쳐야 한다 — F16 주석의 실측 기록 참조).
    # =================================================================

    @staticmethod
    def _type_name(t) -> str | None:
        """SolType → 선언 타입 이름 문자열 (`parameter_types` 와 비교 가능한 형태)."""
        if t is None:
            return None
        cat = getattr(t, "typeCategory", None)
        if cat == "elementary":
            return getattr(t, "elementaryTypeName", None)
        if cat == "struct":
            return getattr(t, "structTypeName", None)
        if cat == "enum":
            return getattr(t, "enumTypeName", None)
        if cat == "interface":
            return getattr(t, "interfaceName", None)
        if cat == "array":
            base = Evaluation._type_name(getattr(t, "arrayBaseType", None))
            return f"{base}[]" if base else None
        return None

    def _static_type_of(self, expr, variables):
        """식의 **정적 타입**(SolType). 평가하지 않고 선언 정보만 본다. 모르면 None.

        리터럴은 **의도적으로 None** 이다 — Solidity 에서 숫자 리터럴은 독립적인
        타입이 없고 문맥이 정한다(그래서 `f(5)` 가 `(int256)`/`(uint256)` 오버로드를
        가지면 solc 자신도 ambiguity 에러를 낸다).
        """
        if expr is None:
            return None
        ctx = getattr(expr, "context", None)

        # ── 함수 호출: 리턴 타입
        #   `visitFunctionCall` 이 만드는 모양은
        #     Expression(function=<IdentifierExp|MemberAccess>, arguments=[...],
        #                operator='()', context='FunctionCallContext')
        #   이라 함수 이름은 `expr.function.identifier` 에 있다.
        #   (`evaluate_function_call_context` 는 `expr.context == "IdentifierExpContext"`
        #    인 변형도 받으므로 그쪽도 함께 본다.)
        #   ★ 이 분기가 없으면 `add(self, fromU256(value))` 의 2번째 인자 타입이
        #     None 이 되어 오버로드를 못 고른다(실측: FixedPointMath L28/L38).
        if ctx == "FunctionCallContext" or (
                ctx == "IdentifierExpContext"
                and (getattr(expr, "arguments", None) is not None
                     or getattr(expr, "named_arguments", None))):
            if getattr(getattr(expr, "function", None), "context", None) == "MemberAccessContext":
                return self._return_type_of_member_call(expr, variables)
            return self._return_type_of_call(expr, variables)

        # ── 식별자: 선언 타입 그대로
        if ctx == "IdentifierExpContext":
            v = variables.get(getattr(expr, "identifier", None))
            return getattr(v, "typeInfo", None) if v is not None else None

        # ── 캐스트: `uint256(x)` / `address(x)` / `IERC20(x)` / `Fixed18(x)`
        #   visitTypeConversion 이 typeName 에 **문자열**을 담는다.
        if ctx == "TypeConversion":
            ty = getattr(expr, "typeName", None)
            if not isinstance(ty, str):
                return ty if hasattr(ty, "typeCategory") else None
            t = SolType()
            resolved = self.an.sa.resolve_type(ty)      # type alias → underlying
            if resolved is not None:
                t.typeCategory = "elementary"
                t.elementaryTypeName = resolved
                t.aliasName = ty
            elif ty in self.an.interface_names:
                t.typeCategory = "interface"
                t.interfaceName = ty
            else:
                t.typeCategory = "elementary"
                t.elementaryTypeName = ty
            if (t.elementaryTypeName or "").startswith(("int", "uint")):
                digits = "".join(c for c in t.elementaryTypeName if c.isdigit())
                t.intTypeLength = int(digits) if digits else 256
            return t

        # ── 멤버 접근: 전역 멤버 / struct 필드의 선언 타입 / 내장 멤버
        if ctx == "MemberAccessContext":
            member = getattr(expr, "member", None)
            base_e = getattr(expr, "base", None)
            gname = (getattr(base_e, "identifier", None)
                     if getattr(base_e, "context", None) == "IdentifierExpContext" else None)
            if gname in ("msg", "block", "tx") and gname not in variables:
                ety = self._GLOBAL_MEMBER_TYPES.get(f"{gname}.{member}")
                return self._elementary_type(ety) if ety else None
            base_t = self._static_type_of(base_e, variables)
            if base_t is None or member is None:
                return None
            cat = getattr(base_t, "typeCategory", None)
            ety = getattr(base_t, "elementaryTypeName", None)
            # (2026-10-23) 내장 멤버 — using-for 수신자 타입을 정적 타입으로 정하면서 필요해졌다
            if member == "length" and (cat == "array" or ety in ("bytes", "string")):
                return self._elementary_type("uint256")
            if member == "balance" and ety in ("address", "address payable"):
                return self._elementary_type("uint256")
            if cat == "struct":
                sdef = self.an.struct_def_of_type(base_t)
                members = getattr(sdef, "members", None) if sdef is not None else None
                if isinstance(members, dict):
                    return members.get(member)
            return None

        # ── 인덱스 접근: 배열 base / 매핑 value
        if ctx == "IndexAccessContext":
            base_t = self._static_type_of(getattr(expr, "base", None), variables)
            cat = getattr(base_t, "typeCategory", None)
            if cat == "array":
                return getattr(base_t, "arrayBaseType", None)
            if cat == "mapping":
                return getattr(base_t, "mappingValueType", None)
            return None

        # ── 산술/비트 연산: 피연산자에서 유도 (아는 쪽, 둘 다 알면 넓은 쪽)
        if getattr(expr, "left", None) is not None and getattr(expr, "right", None) is not None:
            lt = self._static_type_of(expr.left, variables)
            rt = self._static_type_of(expr.right, variables)
            if lt is None:
                return rt
            if rt is None:
                return lt
            ll = getattr(lt, "intTypeLength", None) or 0
            rl = getattr(rt, "intTypeLength", None) or 0
            return lt if ll >= rl else rt

        # ── 삼항: true/false 분기
        if ctx in ("ConditionalContext", "TernaryContext"):
            return (self._static_type_of(getattr(expr, "true_expr", None), variables)
                    or self._static_type_of(getattr(expr, "false_expr", None), variables))

        # ── 리터럴: Solidity 의미론상 독립 타입이 없다
        return None

    _GLOBAL_MEMBER_TYPES = {
        "msg.sender": "address", "msg.value": "uint256", "msg.data": "bytes", "msg.sig": "bytes4",
        "tx.origin": "address", "tx.gasprice": "uint256",
        "block.timestamp": "uint256", "block.number": "uint256", "block.difficulty": "uint256",
        "block.prevrandao": "uint256", "block.gaslimit": "uint256", "block.chainid": "uint256",
        "block.basefee": "uint256", "block.coinbase": "address",
    }

    @staticmethod
    def _elementary_type(name: str):
        t = SolType()
        t.typeCategory = "elementary"
        t.elementaryTypeName = name
        if name.startswith(("int", "uint")):
            digits = "".join(c for c in name if c.isdigit())
            t.intTypeLength = int(digits) if digits else 256
        return t

    @staticmethod
    def _using_type_key(t) -> str | None:
        """SolType → using-for 등록 키(`using L for <키>`). `_get_variable_type_string` 과 같은 표기
        (별칭이 있으면 별칭 이름 — `using UFixed18Lib for UFixed18`)."""
        if t is None:
            return None
        cat = getattr(t, "typeCategory", None)
        if cat == "elementary":
            return getattr(t, "aliasName", None) or getattr(t, "elementaryTypeName", None)
        if cat == "array":
            base = Evaluation._type_name(getattr(t, "arrayBaseType", None))
            return f"{base}[]" if base else None
        if cat == "mapping":
            return "mapping"
        if cat == "struct":
            return getattr(t, "structTypeName", None)
        if cat == "enum":
            return getattr(t, "enumTypeName", None)
        if cat == "interface":
            return getattr(t, "interfaceName", None)
        return None

    def _evaluate_yul(self, expr, variables):
        """Yul 문맥의 식/문장.
          · YulBuiltinContext   builtin 호출 (값 또는 None = 문장)
          · YulWordContext      `let x := e` 의 초기화식 — word 그대로(Yul 지역은 uint256)
          · YulAssignValue      `x := e` 의 우변 — 대상 Solidity 변수 타입으로 cleanup
          · YulUnsupportedStatement  아직 해석하지 않는 Yul 구문 — 실행되면 명시적으로 멈춘다(본문을 무조건 실행하지 않도록)"""
        from Interpreter.Semantics.YulEval import YulEvaluator
        yul = getattr(self, "_yul", None)
        if yul is None:
            yul = self._yul = YulEvaluator(self)
        ctx = expr.context
        if ctx == "YulBuiltinContext":
            return yul.builtin(expr, variables)
        if ctx == "YulWordContext":
            return yul.word_of(expr.expression, variables)
        if ctx == "YulAssignValue":
            target = variables.get(expr.identifier)
            if target is None:
                raise ValueError(f"Yul assignment to undeclared variable '{expr.identifier}'")
            return yul.cleanup(yul.word_of(expr.expression, variables), target)
        if ctx == "YulUnsupportedStatement":
            raise ValueError(f"inline assembly: Yul `{expr.identifier}` is not supported yet "
                             f"(Yul 제어 흐름·종료는 블록 단위 해석 단계에서 지원 — 지금 실행하면 결과가 sound 하지 않다)")
        raise ValueError(f"unknown Yul expression context '{ctx}'")

    def _is_non_typed_receiver(self, base_expr, base_val) -> bool:
        """`X.f(...)` 의 X 가 **값이 아닌 이름**(super/this/전역 네임스페이스/라이브러리·컨트랙트 이름/enum 정의/type(...))인가.
        이것들은 using-for 대상이 아니고 member access 의 각 전용 분기가 처리한다."""
        if isinstance(base_val, (dict, EnumDefinition)):
            return True
        if getattr(base_expr, "context", None) == "IdentifierExpContext":
            name = getattr(base_expr, "identifier", None)
            if name in _NON_VALUE_NAMES:
                return True
        return isinstance(base_val, str) and base_val in _NON_VALUE_NAMES

    @staticmethod
    def declared_return_types(fcfg) -> list:
        """선언 반환 타입 목록. 이름 없는 반환은 `return_types`, 이름 있는 반환은 `return_vars` 에 들어 있다
        (StaticCFGFactory.make_function_cfg). 둘이 섞인 선언은 순서를 복원할 수 없어 오류."""
        rts = list(getattr(fcfg, "return_types", None) or [])
        rvs = list(getattr(fcfg, "return_vars", None) or [])
        if rts and rvs:
            raise ValueError(f"'{fcfg.function_name}': mixed named/unnamed return parameters are not supported")
        return rts or [rv.typeInfo for rv in rvs]

    @staticmethod
    def _single_return_type(fcfg):
        rts = Evaluation.declared_return_types(fcfg)
        return rts[0] if len(rts) == 1 else None

    def _return_type_of_member_call(self, expr, variables):
        """(2026-10-23) `x.f(args)` 호출의 리턴 타입(SolType). 모르면 None.
        - `Lib.f(args)` (라이브러리 한정 호출)
        - x 의 정적 타입에 using-for 로 붙은 라이브러리 함수 (`a.mul(b).div(c)` 체인)
        - x 가 interface/contract 타입이면 그 선언 함수 (부모 체인 포함)"""
        fn = getattr(expr, "function", None)
        member = getattr(fn, "member", None)
        base_e = getattr(fn, "base", None)
        if member is None or base_e is None:
            return None
        n_explicit = len(getattr(expr, "arguments", None) or []) + len(getattr(expr, "named_arguments", None) or {})
        bname = (getattr(base_e, "identifier", None)
                 if getattr(base_e, "context", None) == "IdentifierExpContext" else None)
        if bname is not None and bname not in variables and bname in (self.an.library_cfgs or {}):
            subs = (self.an.library_cfgs[bname].functions or {}).get(member) or {}
            cands = [f for f in subs.values() if len(getattr(f, "parameters", None) or []) == n_explicit]
            if len(cands) == 1:
                return self._single_return_type(cands[0])
            if not cands:
                return None
            # (2026-10-23) 오버로드 여럿: 반환 타입이 모두 같으면 그 타입(고를 필요 없음 — `Fixed18Lib._from(UFixed18)`/
            #   `_from(int256)` 둘 다 Fixed18, 60_H_01), 다르면 인자 정적 타입으로 해소(`_select_overload`, 못 고르면 오류)
            rts = [self._single_return_type(f) for f in cands]
            names = {(getattr(t, "typeCategory", None), getattr(t, "aliasName", None) or self._type_name(t)) for t in rts}
            if None not in rts and len(names) == 1:
                return rts[0]
            chosen = self._select_overload({i: f for i, f in enumerate(cands)}, list(getattr(expr, "arguments", None) or []),
                                           dict(getattr(expr, "named_arguments", None) or {}), variables, member)
            return self._single_return_type(chosen) if chosen is not None else None
        base_t = self._static_type_of(base_e, variables)
        key = self._using_type_key(base_t)
        if key is not None:
            ccf = self._executing_contract_cfg()
            lib_f = ccf.find_library_function(key, member, n_args=1 + n_explicit) if ccf is not None else None
            if lib_f is not None:
                return self._single_return_type(lib_f)
        if getattr(base_t, "typeCategory", None) == "interface":
            visited, queue = set(), [getattr(base_t, "interfaceName", None)]
            while queue:
                name = queue.pop(0)
                if not name or name in visited:
                    continue
                visited.add(name)
                cfg = (self.an.contract_cfgs or {}).get(name)
                if cfg is None:
                    continue
                f = cfg.get_function_cfg(member)
                if f is not None:
                    return self._single_return_type(f)
                queue.extend(getattr(cfg, "parent_contracts", None) or [])
        return None

    def _return_type_of_call(self, expr, variables):
        """이름만으로 하는 함수 호출의 **리턴 타입**(SolType). 모르면 None.

        오버로드가 있어도 ① 모든 오버로드의 리턴 타입이 같으면 고르지 않고도 알 수 있고
        (실측: `FixedPointMath.add/sub` 가 둘 다 `FixedDecimal`),
        ② 인자 개수로 유일하면 그걸 쓴다.
        ★ `_select_overload` 을 부르지 않는다 — 상호 재귀를 막기 위해서다.
        """
        # 이름은 `expr.function.identifier`(FunctionCallContext) 또는
        # `expr.identifier`(IdentifierExpContext 변형) 에 있다.
        # 라이브러리 한정 호출(`UFixed18Lib._from(a)`)은 function 이 MemberAccess 라
        # 여기선 None 을 돌려준다 — 필요해지면 별도로 확장한다.
        fn_expr = getattr(expr, "function", None)
        name = getattr(fn_expr, "identifier", None) if fn_expr is not None else None
        if not name:
            name = getattr(expr, "identifier", None)
        if not name:
            return None
        # (2026-10-23) 실행 중인 함수가 속한 컨트랙트/라이브러리 기준 — 예전엔 타깃 컨트랙트라 library callee 안에서 틀렸다
        #   (2026-10-23) 후보 = 디스패치와 같은 범위(가장 파생된 컨트랙트의 선형화 전체, 라이브러리면 그 라이브러리)
        if self._executing_contract_cfg() is None:
            return None
        subs = {sig: fs[0] for sig, fs in
                self._functions_named(self.linearize(self._dispatch_root_cfg()), name).items()}
        if not subs:
            return None
        with_rt = [f for f in subs.values() if self.declared_return_types(f)]
        if not with_rt:
            return None
        # ① 리턴 타입이 하나로 수렴하면 오버로드 선택 불필요
        names = {self._type_name(self.declared_return_types(f)[0]) for f in with_rt}
        if len(names) == 1 and next(iter(names)) is not None:
            return self.declared_return_types(with_rt[0])[0]
        # ② 인자 개수로 유일하면 그것
        n_args = len(getattr(expr, "arguments", None) or [])
        cands = [f for f in with_rt if self._arity(f) == n_args]
        if len(cands) == 1:
            return self.declared_return_types(cands[0])[0]
        return None

    def find_function_overloads_in_hierarchy(self, contract_cfg, function_name: str):
        """함수 이름의 **overload 집합**(`{sig: FunctionCFG}`)을 **소유 CFG** 에서 찾는다.

        ★ F34. `find_function_in_hierarchy` 는 부모 체인까지 찾아 FunctionCFG 하나를
          돌려주는데(`param_types` 가 없으면 `next(iter(sub.values()))` = **dict 첫 항목**,
          즉 임의), 호출부는 오버로드 재검색용 집합을 `contract_cfg.functions.get(name)`
          으로 **현재 컨트랙트에서만** 가져왔다. 상속받은 오버로드 함수는 집합이 비어
          재검색이 아예 돌지 않았다(실측: `112/Controller` 의 `_prepare(key, amount)` 가
          `Preparable` 의 3-인자 오버로드로 해석돼 arity 오류).
          탐색 순서는 `find_function_in_hierarchy` 와 **동일**해야 한다.
        """
        sub = (getattr(contract_cfg, "functions", None) or {}).get(function_name)
        if sub:
            return sub
        parent_cfgs = getattr(contract_cfg, "parent_cfgs", {}) or {}
        for parent_name in (getattr(contract_cfg, "parent_contracts", None) or []):
            pcfg = parent_cfgs.get(parent_name)
            if pcfg is None:
                continue
            r = self.find_function_overloads_in_hierarchy(pcfg, function_name)
            if r:
                return r
        return None

    @staticmethod
    def _implicitly_convertible(arg_type_name: str | None, param_type_name: str | None) -> bool:
        """`arg_type_name` 값을 `param_type_name` 파라미터에 암시적으로 넘길 수 있나.

        Solidity 규칙 전체가 아니라 **오버로드를 가르는 데 필요한 만큼**만 본다:
        같은 부호 계열의 확장(`uint128` → `uint256`), `bytesN` 확장, 동일 이름.
        `uint` ↔ `int`, `uint` ↔ `address` 처럼 **계열이 다르면 False** — 이게
        `_prepare(bytes32,uint256)` vs `_prepare(bytes32,address)` 를 가른다.
        """
        if arg_type_name is None or param_type_name is None:
            return True                      # 모르면 배제하지 않는다
        if arg_type_name == param_type_name:
            return True

        def _split(n):
            head = n.rstrip("0123456789")
            digits = n[len(head):]
            return head, (int(digits) if digits else None)

        ah, an_ = _split(arg_type_name)
        ph, pn = _split(param_type_name)
        if ah != ph:
            return False                     # uint vs int, uint vs address … 계열 불일치
        if an_ is None or pn is None:
            return True
        return an_ <= pn                     # 확장만 허용

    def _select_overload(self, overloads, arguments, named_arguments,
                         variables, function_name):
        """오버로드 후보 중 하나를 **결정적으로** 고른다. 못 고르면 raise (F37).

        절차: ① 인자 개수로 후보 필터 → ② 선언 타입 **정확 일치** → ③ 암시적 변환.
        ③ 이후에도 후보가 여럿이면 (a) 우리 타입 바인딩이 결손이거나 (b) 소스가
        유효한 Solidity 가 아니다(solc 도 ambiguity 에러). 둘 다 **드러나야 하는**
        상황이므로 조용히 첫 것을 집지 않고 raise 한다.
        """
        total_args = len(arguments) + len(named_arguments)
        cands = [f for f in overloads.values() if self._arity(f) == total_args]
        if len(cands) == 1:
            return cands[0]
        if not cands:
            return None                      # 호출자가 기존 arity 오류로 처리

        # 위치 인자만 타입 비교 (named 인자는 이름으로 이미 매칭된다)
        arg_types = [self._static_type_of(a, variables) for a in arguments]
        known = [i for i, t in enumerate(arg_types) if t is not None]
        arg_names = [self._type_name(t) for t in arg_types]

        def _ptypes(f):
            return getattr(f, 'parameter_types', None) or []

        if known:
            exact = [f for f in cands
                     if all(i < len(_ptypes(f)) and _ptypes(f)[i] == arg_names[i]
                            for i in known)]
            if len(exact) == 1:
                return exact[0]
            pool = exact or cands
            conv = [f for f in pool
                    if all(i < len(_ptypes(f))
                           and self._implicitly_convertible(arg_names[i], _ptypes(f)[i])
                           for i in known)]
            if len(conv) == 1:
                return conv[0]

        raise ValueError(
            f"Cannot resolve overload for '{function_name}': "
            f"arg types={arg_names}, candidates={[_ptypes(f) for f in cands]}"
        )

    # ── (2026-10-23) 상속 선형화와 디스패치 ────────────────────────────────
    @staticmethod
    def _cfg_name(c):
        return getattr(c, "contract_name", None) or getattr(c, "library_name", None)

    @staticmethod
    def _arity(f) -> int:
        """함수의 파라미터 수 — 이름 없는 파라미터도 센다(`parameter_types`)."""
        return len(getattr(f, "parameter_types", None) or [])

    def linearize(self, ccf) -> list:
        """Solidity C3 선형화: `contract C is B1, …, Bn` → [C] + merge(L(Bn), …, L(B1), [Bn, …, B1]).
        (`is` 목록의 오른쪽이 더 파생된 쪽.) 컨트랙트는 **이름**으로 식별한다 — 의존성 pkl 이 컨트랙트마다 따로 저장돼
        같은 조상이 부모마다 다른 사본으로 들어오기 때문. 객체는 세션에 로드된 것(`contract_cfgs`)을 우선한다."""
        cfgs = self.an.contract_cfgs or {}
        by_name: dict = {}
        memo: dict = {}

        def lin(c, stack):
            name = self._cfg_name(c)
            by_name.setdefault(name, cfgs.get(name) or c)
            if name in memo:
                return memo[name]
            if name in stack:
                raise ValueError(f"inheritance cycle through '{name}'")
            parents = []
            for pname in getattr(c, "parent_contracts", None) or []:
                p = cfgs.get(pname) or (getattr(c, "parent_cfgs", None) or {}).get(pname)
                if p is None:
                    raise ValueError(f"inheritance: parent '{pname}' of '{name}' is not loaded")
                parents.append(p)
            rev = [self._cfg_name(p) for p in reversed(parents)]
            seqs = [list(lin(p, stack | {name})) for p in reversed(parents)] + [rev]
            out = [name]
            seqs = [s for s in seqs if s]
            while seqs:
                for s in seqs:
                    head = s[0]
                    if not any(head in t[1:] for t in seqs):
                        break
                else:
                    raise ValueError(f"linearization of '{name}' is impossible (Solidity 도 컴파일 오류)")
                out.append(head)
                seqs = [[x for x in t if x != head] for t in seqs]
                seqs = [s for s in seqs if s]
            memo[name] = out
            return out

        return [by_name[n] for n in lin(ccf, frozenset())]

    def _dispatch_root_cfg(self):
        """가상 디스패치의 기준 = 가장 파생된 컨트랙트(타깃). 실행 중인 함수가 타깃의 선형화에 없으면
        (라이브러리 등) 그 함수의 소유 컨트랙트."""
        cfgs = self.an.contract_cfgs or {}
        target = cfgs.get(self.an.current_target_contract)
        fcfg = self.an.current_target_function_cfg
        own = self.engine._fcfg_owner(fcfg)[0] if fcfg is not None else None
        if target is not None and (own is None or own in [self._cfg_name(c) for c in self.linearize(target)]):
            return target
        own_cfg = cfgs.get(own) or (self.an.library_cfgs or {}).get(own)
        if own_cfg is None:
            raise ValueError(f"dispatch root: contract '{own}' of executing function not loaded")
        return own_cfg

    @staticmethod
    def _functions_named(L, name) -> dict:
        """선형화 순서대로 이름이 같은 함수들 — {시그니처: [가장 파생된 것부터]}."""
        by_sig: dict = {}
        for c in L:
            for sig, f in ((getattr(c, "functions", None) or {}).get(name) or {}).items():
                by_sig.setdefault(sig, []).append(f)
        return by_sig

    def _dispatch(self, L, name, arguments, named_arguments, variables):
        """① 오버로드 해소: 선형화 전체의 시그니처(선언 포함) 중 인자에 맞는 것 하나.
        ② 디스패치: 그 시그니처의 **본문 있는** 첫 함수(가장 파생된 override). 없으면 추상 → 오류."""
        by_sig = self._functions_named(L, name)
        where = [self._cfg_name(c) for c in L]
        if not by_sig:
            raise ValueError(f"call to '{name}': no function of that name in {where}")
        reps = {sig: fs[0] for sig, fs in by_sig.items()}
        n_args = len(arguments) + len(named_arguments)
        if len(reps) == 1:
            chosen = next(iter(reps.values()))
            if self._arity(chosen) != n_args:
                raise ValueError(f"call to '{name}': no overload takes {n_args} arguments "
                                 f"(candidates {list(reps)} in {where})")
        else:
            chosen = self._select_overload(reps, arguments, named_arguments, variables, name)
            if chosen is None:
                raise ValueError(f"call to '{name}': no overload takes {n_args} arguments "
                                 f"(candidates {list(reps)} in {where})")
        sig = next(s for s, f in reps.items() if f is chosen)
        impl = next((f for f in by_sig[sig] if f.has_body), None)
        if impl is None:
            raise ValueError(f"call to '{name}{sig}': no implementation in {where} (선언만 있음 — 추상)")
        return impl

    def find_function_in_hierarchy(self, contract_cfg, function_name: str):
        """
        Args:
            contract_cfg: 현재 컨트랙트 CFG
            function_name: 찾을 함수 이름

        Returns:
            FunctionCFG 또는 None (못 찾은 경우)
        """
        # 1. 현재 컨트랙트에서 검색
        if function_name in contract_cfg.functions:
            return contract_cfg.get_function_cfg(function_name)

        # 2. 부모 컨트랙트 체인 검색 (MRO 순서)
        parent_contracts = getattr(contract_cfg, 'parent_contracts', [])
        parent_cfgs = getattr(contract_cfg, 'parent_cfgs', {})

        for parent_name in parent_contracts:
            parent_cfg = parent_cfgs.get(parent_name)
            if parent_cfg:
                # 재귀적으로 부모 체인 검색
                result = self.find_function_in_hierarchy(parent_cfg, function_name)
                if result:
                    return result

        # 3. 못 찾음
        return None

    def find_function_in_parent_only(self, contract_cfg, function_name: str):
        """
        super 키워드용: 현재 컨트랙트는 건너뛰고 부모 체인에서만 검색한다.

        Args:
            contract_cfg: 현재 컨트랙트 CFG
            function_name: 찾을 함수 이름

        Returns:
            FunctionCFG 또는 None (못 찾은 경우)
        """
        parent_contracts = getattr(contract_cfg, 'parent_contracts', [])
        parent_cfgs = getattr(contract_cfg, 'parent_cfgs', {})

        for parent_name in parent_contracts:
            parent_cfg = parent_cfgs.get(parent_name)
            if parent_cfg:
                # 부모에서는 전체 계층 검색
                result = self.find_function_in_hierarchy(parent_cfg, function_name)
                if result:
                    return result

        return None

    def _is_known_parent_contract(self, contract_cfg, name: str) -> bool:
        """name이 contract_cfg의 부모 체인(직계/조상 포함) 안에 있는 컨트랙트
        이름인지 확인 — 명시적 base-contract-qualified 호출(`ParentName.foo()`,
        `super.foo()`와 비슷하지만 조상 하나를 이름으로 못박는 문법)을 위한 체크."""
        parent_contracts = getattr(contract_cfg, 'parent_contracts', [])
        parent_cfgs = getattr(contract_cfg, 'parent_cfgs', {})
        for parent_name in parent_contracts:
            if parent_name == name:
                return True
            parent_cfg = parent_cfgs.get(parent_name)
            if parent_cfg and self._is_known_parent_contract(parent_cfg, name):
                return True
        return False

    def _join_struct_fields(self, struct_list):
        """
        구조체 리스트의 각 필드를 join하여 하나의 구조체 반환
        """
        if not struct_list:
            return None

        # 첫 구조체를 복사하여 결과 구조체 생성
        result = copy.deepcopy(struct_list[0])

        # 각 필드별로 모든 구조체의 값을 join
        for field_name in result.members:
            values = []
            for i, s in enumerate(struct_list):
                if field_name in s.members:
                    field_var = s.members[field_name]
                    if isinstance(field_var, (StructVariable, ArrayVariable, MappingVariable)):
                        # 복합 타입은 join 불가 - 첫 번째 것 사용
                        values.append(field_var)
                        break
                    else:
                        val = field_var.value
                        values.append(val)

            # join 수행
            if values:
                joined_val = values[0]
                for v in values[1:]:
                    if hasattr(joined_val, 'join') and hasattr(v, 'join'):
                        joined_val = joined_val.join(v)

                # 결과 저장
                if isinstance(result.members[field_name], Variables):
                    result.members[field_name].value = joined_val

        return result

    def _join_array_elements_virtually(self, array, index_range):
        """
        배열을 수정하지 않고 가상으로 요소 생성하여 join
        """
        l, r = index_range
        span = r - l

        # 샘플링할 인덱스 결정 (최대 20개)
        if span > 20:
            sample_indices = [l + i * span // 20 for i in range(21)]
        else:
            sample_indices = list(range(l, r + 1))

        joined = None
        for idx in sample_indices:
            # 기존 요소가 있으면 사용, 없으면 가상으로 생성
            elem = array.read_element(idx)          # (희소 배열) 구체 셀 또는 D

            # join 로직
            if isinstance(elem, StructVariable):
                # 구조체는 각 필드별로 join
                if joined is None:
                    joined = copy.deepcopy(elem)
                else:
                    # 구조체의 각 필드 join
                    for field in elem.members:
                        if field in joined.members:
                            elem_val = elem.members[field].value if hasattr(elem.members[field], 'value') else elem.members[field]
                            joined_val = joined.members[field].value if hasattr(joined.members[field], 'value') else joined.members[field]
                            if hasattr(elem_val, 'join') and hasattr(joined_val, 'join'):
                                joined.members[field].value = joined_val.join(elem_val)
            else:
                val = elem.value if hasattr(elem, 'value') else elem
                joined = val if joined is None else joined.join(val)

        return joined

    def _join_mapping_values_virtually(self, mapping, sample_keys):
        """
        매핑의 여러 키에 대해 가상으로 value 생성하여 join
        """
        joined = None
        for k in sample_keys:
            k_str = str(k)
            if k_str in mapping.mapping:
                val_obj = mapping.mapping[k_str]
            else:
                val_obj = mapping._create_value_virtual(k_str)

            # 구조체: 필드별 join
            if isinstance(val_obj, StructVariable):
                if joined is None:
                    joined = copy.deepcopy(val_obj)
                else:
                    for field in val_obj.members:
                        if field in joined.members:
                            val = val_obj.members[field].value if hasattr(val_obj.members[field], 'value') else val_obj.members[field]
                            joined_val = joined.members[field].value if hasattr(joined.members[field], 'value') else joined.members[field]
                            if hasattr(val, 'join') and hasattr(joined_val, 'join'):
                                joined.members[field].value = joined_val.join(val)
            # 기본 타입: value join
            elif isinstance(val_obj, (ArrayVariable, MappingVariable)):
                # 복합 타입은 첫 것만 사용
                if joined is None:
                    joined = val_obj
            else:
                val = val_obj.value if hasattr(val_obj, "value") else val_obj
                joined = val if joined is None else joined.join(val)

        return joined

    def evaluate_expression(self, expr: Expression, variables, callerObject=None, callerContext=None):
        # (2026-10-23, Yul P0-a) Yul 식은 256-bit word 의미로 따로 평가한다 (Interpreter/Semantics/YulEval.py)
        if str(expr.context).startswith("Yul"):
            return self._evaluate_yul(expr, variables)
        if expr.context == "LiteralExpContext":
            return self.evaluate_literal_context(expr, variables, callerObject, callerContext)
        elif expr.context == "NumLiteralContext":
            # Guardian DSL의 숫자 리터럴 (arithExpr에서 사용)
            return self.evaluate_literal_context(expr, variables, callerObject, callerContext)
        elif expr.context == "IdentifierExpContext":
            return self.evaluate_identifier_context(expr, variables, callerObject, callerContext)
        elif expr.context == "VarRefBase":
            # Guardian DSL의 단순 변수 참조 (IdentifierExpContext와 동일 구조)
            return self.evaluate_identifier_context(expr, variables, callerObject, callerContext)
        elif expr.context == 'MemberAccessContext':
            return self.evaluate_member_access_context(expr, variables, callerObject, callerContext)
        elif expr.context == "VarRefMemberAccess":
            # Guardian DSL의 멤버 접근 (MemberAccessContext와 동일 구조)
            return self.evaluate_member_access_context(expr, variables, callerObject, callerContext)
        elif expr.context == "IndexAccessContext":
            return self.evaluate_index_access_context(expr, variables, callerObject, callerContext)
        elif expr.context == "VarRefIndexAccess":
            # Guardian DSL의 인덱스 접근 (IndexAccessContext와 동일 구조)
            return self.evaluate_index_access_context(expr, variables, callerObject, callerContext)
        elif expr.context == "MetaTypeContext":
            # type(uint256), type(address) 등
            return {"isType": True, "typeName": expr.typeName}
        elif expr.context == "TypeConversion":
            return self.evaluate_type_conversion_context(expr, variables, callerObject, callerContext)
        elif expr.context == "ConditionalExpContext":
            return self.evaluate_conditional_expression_context(expr, variables, callerObject, callerContext)
        elif expr.context == "InlineArrayExpression":
            return self.evaluate_inline_array_expression_context(expr, variables, callerObject, callerContext)
        elif expr.context == "TypeWrapUnwrapContext":
            return self.evaluate_type_wrap_unwrap(expr, variables, callerObject, callerContext)
        elif expr.context == "FunctionCallContext":
            return self.evaluate_function_call_context(expr, variables, callerObject, callerContext)
        elif expr.context == "FunctionCallOptionContext":
            return self.evaluate_function_call_option_context(expr, variables, callerObject, callerContext)
        elif expr.context == "PayableFunctionCallContext":
            # payable(addr) - address를 payable address로 변환
            return self.evaluate_payable_function_call_context(expr, variables, callerObject, callerContext)

        elif expr.context == "TupleExpressionContext":
            return self.evaluate_tuple_expression_context(expr, variables,
                                                          callerObject, callerContext)
        elif expr.context == 'AssignmentOpContext':
            return self.evaluate_assignment_expression(expr, variables,
                                                       callerObject, callerContext)
        elif expr.context == "LiteralSubDenomination":
            return self.evaluate_literal_with_subdenomination_context(
                expr, variables, callerObject, callerContext)
        elif expr.context == "NewExpContext":
            return self.evaluate_new_expression_context(expr, variables,
                                                        callerObject, callerContext)

        # 단항 연산자
        if expr.operator in ['-', '!', '~'] and expr.expression:
            return self.evaluate_unary_operator(expr, variables, callerObject, callerContext)

        # 이항 연산자
        if expr.left is not None and expr.right is not None:
            return self.evaluate_binary_operator(expr, variables, callerObject, callerContext)

    def evaluate_new_expression_context(self, expr: Expression,
                                        variables, callerObject=None, callerContext=None):
        """
        ▸ expr.type_name  : visitNewExp() 에서 채워 둔 SolType 인스턴스
        ▸ 반환값          : 새로 만든 ArrayVariable / MappingVariable /
                           StructVariable / Variables (elementary) /
                           심볼릭 address 등
        """

        sol_t: SolType = expr.typeName  # 타입 정보
        fresh_id = f"new_{id(expr)}"  # 유니크한 식별자

        # ── (A) 배열 ───────────────────────────────────────────────
        if sol_t.typeCategory == "array":
            # 동적 배열 크기 평가: new uint256[](size) 형태
            MAX_CONCRETE_ARRAY_LEN = 100  # 구체적으로 초기화할 최대 배열 길이
            array_length = sol_t.arrayLength
            length_iv = None        # (2026-10-23) 추상 길이 — 위 array_length 는 구체화할 원소 개수일 뿐이다
            if expr.arguments and len(expr.arguments) > 0:
                # arguments[0]에 길이 표현식이 있으면 평가
                length_result = self.evaluate_expression(expr.arguments[0], variables, callerObject, callerContext)
                if isinstance(length_result, (IntegerInterval, UnsignedIntegerInterval)) and not length_result.is_bottom():
                    length_iv = UnsignedIntegerInterval(max(length_result.min_value, 0), length_result.max_value, 256)
                elif isinstance(length_result, int) and not isinstance(length_result, bool):
                    length_iv = UnsignedIntegerInterval(length_result, length_result, 256)
                else:
                    length_iv = UnsignedIntegerInterval.top(256)
                # 결과가 interval이면 상한값 사용 (max_value 속성 사용)
                if hasattr(length_result, 'max_value') and length_result.max_value is not None:
                    # 배열 길이가 너무 크면 동적 배열로 처리 (무한 루프 방지)
                    if length_result.max_value > MAX_CONCRETE_ARRAY_LEN:
                        array_length = None  # 동적 배열로 처리
                    else:
                        array_length = length_result.max_value
                elif isinstance(length_result, int):
                    if length_result > MAX_CONCRETE_ARRAY_LEN:
                        array_length = None
                    else:
                        array_length = length_result
                else:
                    # 심볼릭이거나 다른 타입이면 None으로 (동적)
                    array_length = None

            arr = ArrayVariable(
                fresh_id,
                base_type=sol_t.arrayBaseType,
                array_length=array_length,
                is_dynamic=sol_t.isDynamicArray,
                scope="memory"
            )
            if sol_t.isDynamicArray:
                arr.length = length_iv if length_iv is not None else UnsignedIntegerInterval.top(256)

            # ★ base type이 qualified 네임스페이스 struct(예: `IPool.TokenAmount`,
            # interface 안에 정의된 struct)면 현재 컨트랙트의 struct_defs엔
            # 당연히 없음 — interface pkl에서 struct_defs를 가져와 채워야
            # `_init_recursive`의 struct 분기가 실제로 초기화할 수 있음
            # (web3bugs_35_H_08/35_H_12에서 발견).
            base_t = sol_t.arrayBaseType
            if isinstance(base_t, SolType) and base_t.typeCategory == "struct" and \
                    isinstance(base_t.structTypeName, str) and "." in base_t.structTypeName:
                ifc_name, bare_name = base_t.structTypeName.split(".", 1)
                ifc_struct = self._find_interface_struct_def(ifc_name, bare_name)
                if ifc_struct is not None:
                    _, ifc_struct_defs, ifc_enum_defs = ifc_struct
                    arr.struct_defs = {**ifc_struct_defs, **arr.struct_defs}
                    arr.enum_defs = {**ifc_enum_defs, **arr.enum_defs}

            # ⬇ Solidity default: new array elements are zero-initialized
            #   (2026-10-23, 희소 배열) D = 0 셀 — 길이가 미지여도 모든 원소가 0 이다(원소를 만들지 않는다)
            if ArrayVariable._is_abstractable(sol_t.arrayBaseType):
                et = str(sol_t.arrayBaseType.elementaryTypeName)
                bits = sol_t.arrayBaseType.intTypeLength or 256
                if et.startswith("int"):
                    dummy = IntegerInterval(0, 0, bits)
                elif et == "bool":
                    dummy = BoolInterval(0, 0)
                else:
                    dummy = UnsignedIntegerInterval(0, 0, bits)
                arr.elements = {}
                arr.summary = arr._uniform_cell("*", sol_t.arrayBaseType, dummy)
            else:
                arr.initialize_not_abstracted_type()

            return arr

        # ── (B) 매핑 ───────────────────────────────────────────────
        if sol_t.typeCategory == "mapping":
            ccf = self.an.contract_cfgs[self.an.current_target_contract]

            return MappingVariable(fresh_id,
                                   key_type=sol_t.mappingKeyType,
                                   value_type=sol_t.mappingValueType,
                                   scope="memory",
                                   struct_defs=ccf.structDefs,  # ⭐️ 전달
                                   enum_defs=ccf.enumDefs)  # ⭐️ 전달)

        # ── (C) 구조체 ─────────────────────────────────────────────
        if sol_t.typeCategory == "struct":
            return StructVariable(fresh_id, sol_t.structTypeName, scope="memory")

        # ── (D) 컨트랙트 new Foo()  → 심볼릭 address ───────────────
        if sol_t.typeCategory == "userDefined" :
            # "fresh address"를 set domain TOP 으로
            return AddressSet.top()

        # ── (E) 기본형 new uint[](...) 처럼 size 없는 array 등
        #        또는 new bytes(...) – 메모리 상 동적 할당
        if sol_t.typeCategory == "elementary":
            return f"symbolic_{fresh_id}"

        raise ValueError(f"unsupported 'new' type: {sol_t!r}")

    # ───────────────────── evaluate_literal_context ──────────────
    def evaluate_literal_context(
            self,
            expr: Expression,
            variables: dict[str, Variables],
            callerObject: Variables | ArrayVariable | MappingVariable | None = None,
            callerContext: str | None = None):

        lit = expr.literal  # 예: "123", "0x1A", "true", ...
        ety = expr.expr_type  # 'uint'·'int'·'bool'·'string'·'address' 등
        _NUM_SCI = re.compile(r"^[+-]?\d+([eE][+-]?\d+)$")  # 1e8, 2E+18 …

        def _parse_maybe_int(txt: str):
            """10·16·8진수 또는 지수표기를 int 로 반환. 실패하면 None."""
            # ➊ 0x / 0o / decimal
            try:
                return int(txt, 0)
            except ValueError:
                pass

            # ➋ scientific notation
            if _NUM_SCI.match(txt):
                try:
                    return int(Decimal(txt))
                except (InvalidOperation, ValueError):
                    pass
            return None

        def _literal_is_address(txt: str) -> bool:
            """
            0x 로 시작하고 20 바이트(40 hex) 또는 0x0 처럼 짧아도 ‘주소 literal’ 로 간주
            실제 Solidity lexer 는 0x 포함 42자 고정이지만, 여기선 분석 편의상 느슨하게 허용
            """
            return txt.lower().startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in txt[2:])

        # ───────── 1. 상위 객체(Array / Mapping) 인덱싱 ──────────
        if callerObject is not None:
            # 1-A) 배열 인덱스
            if isinstance(callerObject, ArrayVariable):
                if not lit.lstrip("-").isdigit():
                    raise ValueError(f"Array index must be decimal literal, got '{lit}'")
                idx = int(lit)
                # ★ 동적 배열은 `get_or_create_element` 로 필요한 만큼 확장한다.
                #   여기만 `elements[idx]` 에 직접 접근해서, 길이를 모르는 파라미터
                #   (`uint256[][] memory w`) 의 `w[0]` 이 auto-push 없이
                #   IndexError 로 터졌다. `DebugInitializer` 는 이미 이 패턴을 쓴다.
                #   생성되는 원소는 `_create_default_value` 의 ⊤ 라 건전하다.
                #   정적 배열은 기존대로 범위 검사(실제 Solidity 도 컴파일 에러).
                if callerObject.typeInfo.isDynamicArray:
                    if idx < 0:
                        raise IndexError(f"Negative index {idx} for array '{callerObject.identifier}'")
                    return callerObject.get_or_create_element(idx)
                if idx < 0 or not callerObject.in_static_range(idx):
                    raise IndexError(f"Index {idx} out of range for array '{callerObject.identifier}'")
                return callerObject.get_or_create_element(idx)  # element (Variables | …)

            # 1-B) 매핑 키 – 문자열·hex·decimal 모두 허용
            if isinstance(callerObject, MappingVariable):
                if not callerObject.struct_defs or not callerObject.enum_defs:
                    all_structs, all_enums = self.an.get_full_struct_enum_defs()
                    callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                    callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

                return callerObject.get_or_create(lit)

        # ───────── 2. 상위 없음 & 인덱스/멤버 base 해결 ──────────
        if callerContext in ("IndexAccessContext", "MemberAccessContext"):
            return lit  # key 로 그대로 사용

        # ───────── 3. 실제 값으로 해석해 반환 ──────────
        # (예전엔 여기서 과학적 표기(`1e18` 등)를 처리 못 하는 `_to_scalar_int`라는
        # 별도 헬퍼를 썼음 — docstring은 "scientific notation 지원"이라 적혀있었지만
        # 실제로는 `_NUM_SCI`/`Decimal` 폴백이 없어서 `int("1e18", 0)`이 ValueError를
        # 던지고 조용히 None을 리턴했음. `int` 분기는 그 None을 그대로
        # `IntegerInterval(None, None, bits)`(= bottom)로 만들어 조용히 삼켰고,
        # `uint` 분기는 `if val < 0`에서 `None < 0`으로 크래시했음
        # (web3bugs_79_H_02, `@During tokenAllocated == wavaxReserve * 1e18 /
        # floorPrice`에서 발견 — `1e18`이 예전엔 무조건 'int' 타입이라 전자로 조용히
        # bottom이 됐다가, 이번 세션의 리터럴 부호-판정 수정으로 양수라 'uint'가
        # 되면서 후자의 크래시로 드러남). `_parse_maybe_int`가 바로 아래 이미
        # 과학적 표기를 제대로 처리하므로 그걸 그대로 재사용.
        if ety == "uint":
            val = _parse_maybe_int(lit)
            if val < 0:
                raise ValueError("uint literal cannot be negative")
            bits = expr.type_length or 256
            return UnsignedIntegerInterval(val, val, bits)

        if ety == "int":
            bits = expr.type_length or 256
            val = _parse_maybe_int(lit)
            return IntegerInterval(val, val, bits)

        if ety == "bool":
            if lit.lower() == "true":
                return BoolInterval(1, 1)
            if lit.lower() == "false":
                return BoolInterval(0, 0)
            raise ValueError(f"Invalid bool literal '{lit}'")

        # 새로 추가 ───────── address / bytes / string ─────────
        if ety == "address":
            if not _literal_is_address(lit):
                raise ValueError(f"Malformed address literal '{lit}'")
            val_int = int(lit, 16)
            # ★ Set domain 사용: 구체적 address ID로 singleton set 생성
            return AddressSet(ids={val_int})

        # bytes32, bytes16 등 고정 크기 바이트 배열
        if ety and ety.startswith("bytes") and len(ety) > 5:  # "bytes32", "bytes16" 등
            byte_size = int(ety[5:])  # "bytes32" -> 32
            maybe_int = _parse_maybe_int(lit)
            if maybe_int is not None:
                # 숫자로 파싱 가능하면 BytesSet으로 처리
                return BytesSet(values={maybe_int}, byte_size=byte_size)
            # 16진수 문자열 시도
            if lit.startswith("0x"):
                try:
                    val_int = int(lit, 16)
                    return BytesSet(values={val_int}, byte_size=byte_size)
                except ValueError:
                    pass
            # ★ 문자열 리터럴 → bytesN: Solidity 는 UTF-8 바이트를 **상위 바이트부터**
            #   배치하고 하위를 0 으로 채운다(left-aligned = 오른쪽 0 패딩).
            #   근거(실증): Dataset/Web3Bugs/S3_2/contest_145_H_02/registry_ReverseRegistrar.sol:12
            #       bytes32 constant lookup = 0x30313233...6600000000...;   // "0123456789abcdef"
            #       mstore8(i, byte(and(addr, 0xf), lookup))
            #   Yul `byte(n,x)` 는 MSB 부터 n 번째 바이트를 뽑으므로, 이 인덱싱(0→'0',15→'f')이
            #   성립하려면 ASCII 가 상위 바이트에 있어야 한다.
            #   상수의 구체 값 집합은 **싱글턴**이므로 값이 정확하면 이것이 가장 좁은
            #   건전한 추상화다. ⊤ 로 두면 BytesSet.equals 가 항상 Unknown 을 내
            #   (`hasRole(ROLE_ADMIN, ...)`, mapping key) verdict 가 전부 Warning 이 된다.
            if len(lit) >= 2 and lit[0] in ("'", '"') and lit[-1] == lit[0]:
                body = lit[1:-1]
                # 모델링하지 않은 형태는 **틀린 싱글턴**을 만든다 — ⊤ 로 덮지 말고 터뜨린다.
                #   (⊤ 는 "정밀도만 잃는" 게 아니라 잘못된 경로를 믿게 만든다.
                #    현재 이 케이스들은 이미 오류로 드러나 있으므로 raise 는 현상 유지다.)
                if "\\" in body:
                    raise ValueError(
                        f"bytes{byte_size} 문자열 리터럴에 이스케이프가 있어 바이트열을 "
                        f"확정할 수 없다: {lit!r}")
                raw = body.encode("utf-8")
                if len(raw) > byte_size:
                    raise ValueError(
                        f"문자열 리터럴 {lit!r} 이 bytes{byte_size} 범위를 넘는다 "
                        f"({len(raw)} 바이트)")
                return BytesSet(values={int.from_bytes((raw + bytes(byte_size - len(raw))), "big")},
                                byte_size=byte_size)
            raise ValueError(
                f"bytes{byte_size} 로 해석할 수 없는 리터럴: {lit!r} "
                f"(hex\"..\" / unicode\"..\" 형태는 아직 미구현)")

        if ety in ("string", "bytes"):
            maybe_int = _parse_maybe_int(lit)
            if maybe_int is not None:
                # ▶ 사실은 숫자!  → uint256 interval 로 취급
                return UnsignedIntegerInterval(maybe_int, maybe_int, 256)
            return lit  # 진짜 문자열이면 그대로 심볼릭

        # 기타 타입
        raise ValueError(f"Unsupported literal expr_type '{ety}'")

    def evaluate_identifier_context(self, expr: Expression, variables, callerObject=None, callerContext=None):
        ident_str = expr.identifier

        # callerObject가 있는 경우
        if callerObject is not None:
            if isinstance(callerObject, ArrayVariable):
                if ident_str not in variables:
                    raise ValueError(f"Index identifier '{ident_str}' not found.")

                idx_var_obj = variables[ident_str]
                iv = idx_var_obj.value  # Unsigned/IntegerInterval …

                # ── (A) 인덱스가 확정(singleton) ────────────────────────
                if VariableEnv.is_interval(iv) and not iv.is_bottom() and iv.min_value == iv.max_value:
                    idx = iv.min_value
                    if idx < 0:
                        raise IndexError(f"Negative index {idx} for array '{callerObject.identifier}'")

                    if not callerObject.in_static_range(idx):
                        # ❗ 정적 배열 범위 밖 → base-type 의 TOP 값 (경로 차단은 Phase 5)
                        base_t = callerObject.typeInfo.arrayBaseType
                        if base_t.elementaryTypeName and base_t.elementaryTypeName.startswith("uint"):
                            bits = base_t.intTypeLength or 256
                            return UnsignedIntegerInterval.top(bits)
                        elif base_t.elementaryTypeName and base_t.elementaryTypeName.startswith("int"):
                            bits = base_t.intTypeLength or 256
                            return IntegerInterval.top(bits)
                        elif base_t.elementaryTypeName and base_t.elementaryTypeName == "bool":
                            return BoolInterval.top()
                        elif base_t.elementaryTypeName and base_t.elementaryTypeName == "address":
                            return AddressSet.top()
                        elif isinstance(base_t, SolType) and base_t.typeCategory == "struct":
                            # 구조체 타입: 빈 구조체 생성 후 초기화
                            empty_struct = StructVariable(
                                f"{callerObject.identifier}[{idx}]",
                                base_t.structTypeName,
                                scope=callerObject.scope
                            )
                            ccf = self.an.contract_cfgs[self.an.current_target_contract]
                            if base_t.structTypeName in ccf.structDefs:
                                empty_struct.initialize_struct(ccf.structDefs[base_t.structTypeName],
                                                               struct_defs=ccf.structDefs, enum_defs=ccf.enumDefs)
                            return empty_struct
                        elif isinstance(base_t, SolType) and \
                                base_t.typeCategory in ("array", "mapping", "enum"):
                            # ★ 중첩 배열 / mapping / enum → 도메인 헬퍼에 위임.
                            #   이 분기가 없어서 `uint256[][] w` 의 `w[i]` 가 아래 else 로
                            #   떨어져 **문자열**을 돌려줬고, 거기에 `.length` 를 걸면
                            #   "member 'length' is not a recognised global-member" 로
                            #   터졌다(CommonLibrary.splitAmounts 14건의 시작점).
                            #   `_create_element_virtual` → `_create_new_array_element` 는
                            #   중첩 ArrayVariable / MappingVariable / EnumVariable 을
                            #   올바르게 만들고, 원소 값은 `_create_default_value` 의 ⊤ 다.
                            return callerObject._create_element_virtual(idx)
                        else:
                            # 기타 (bytes/string 등)는 symbol
                            return f"symbolic_{callerObject.identifier}[{idx}]"

                    elem = callerObject.read_element(idx)      # (희소 배열) 구체 셀 또는 D
                    # 구조체나 배열 등 복합 타입이면 객체 자체 반환, 기본 타입이면 .value 반환
                    if isinstance(elem, (StructVariable, ArrayVariable, MappingVariable)):
                        return elem
                    return elem.value if hasattr(elem, "value") else elem

                # ── (B) 불확정(bottom 또는 [l,r] 범위) ─────────────────
                #      ⇒  배열 모든 요소의 join 을 반환 (구조체 포함)
                # (희소 배열) 후보 = 범위 안 구체 셀 + (구체화 안 된 위치가 있으면) D
                _lo = iv.min_value if VariableEnv.is_interval(iv) and not iv.is_bottom() else None
                _hi = iv.max_value if _lo is not None else None
                _cands = [e for k, e in sorted(callerObject.elements.items())
                          if _lo is None or _lo <= k <= _hi]
                _n = callerObject.static_length()
                _span = None if _lo is None else (min(_hi, _n - 1) if _n is not None else _hi) - _lo + 1
                if _span is None or len(_cands) < _span:
                    _cands.append(callerObject.read_element("*"))
                if _cands:
                    first_elem = _cands[0]

                    # 구조체 배열: 각 필드를 join하여 필드마다 TOP으로 만든 구조체 반환
                    if isinstance(first_elem, StructVariable):
                        return self._join_struct_fields(_cands)

                    # 기본 타입: 모든 값 join
                    elif not isinstance(first_elem, (ArrayVariable, MappingVariable)):
                        # # DEBUG: Check array access with interval index
                        # print(f"[ARRAY DEBUG] Accessing {callerObject.identifier} with interval index {ident_str}={iv}, elements count={len(callerObject.elements)}")
                        joined = None
                        for i, elem in enumerate(_cands):
                            val = getattr(elem, "value", elem)
                            # print(f"[ARRAY DEBUG]   element[{i}] = {val}")
                            joined = val if joined is None else joined.join(val)
                        # print(f"[ARRAY DEBUG]   joined result = {joined}")
                        return joined

                    # 배열/매핑 중첩: 첫 요소 그대로 반환 (복잡도 제한)
                    else:
                        return first_elem

                # 배열이 비어 있으면 base-type 에 맞는 TOP 반환
                base_t = callerObject.typeInfo.arrayBaseType

                # 구조체: 빈 구조체 생성 후 초기화
                if isinstance(base_t, SolType) and base_t.typeCategory == "struct":
                    empty_struct = StructVariable(
                        f"{callerObject.identifier}[virtual]",
                        base_t.structTypeName,
                        scope=callerObject.scope
                    )
                    # struct_defs가 필요하면 ContractAnalyzer에서 가져오기
                    ccf = self.an.contract_cfgs[self.an.current_target_contract]
                    if base_t.structTypeName in ccf.structDefs:
                        empty_struct.initialize_struct(ccf.structDefs[base_t.structTypeName],
                                                       struct_defs=ccf.structDefs, enum_defs=ccf.enumDefs)
                    return empty_struct

                # 기본형: TOP interval
                if base_t.elementaryTypeName and base_t.elementaryTypeName.startswith("uint"):
                    bits = base_t.intTypeLength or 256
                    return UnsignedIntegerInterval.top(bits)
                if base_t.elementaryTypeName and base_t.elementaryTypeName.startswith("int"):
                    bits = base_t.intTypeLength or 256
                    return IntegerInterval.top(bits)
                if base_t.elementaryTypeName and base_t.elementaryTypeName == "bool":
                    return BoolInterval.top()
                if base_t.elementaryTypeName and base_t.elementaryTypeName == "address":
                    return AddressSet.top()

                # ★ 중첩 배열 / mapping / enum → 도메인 헬퍼에 위임 (위 (a) 와 동일 이유)
                if isinstance(base_t, SolType) and \
                        base_t.typeCategory in ("array", "mapping", "enum"):
                    return callerObject._create_element_virtual("*")

                # 기타
                return f"symbolic_{callerObject.identifier}[<unk>]"

            elif isinstance(callerObject, StructVariable):
                if ident_str not in callerObject.members:
                    raise ValueError(f"member identifier '{ident_str}' not found in struct variables.")

                var = callerObject.members[ident_str]

                if isinstance(var, Variables):  # int, uint, bool이면 interval address, string이면 symbol을 리턴
                    return var.value
                else:  # ArrayVariable, StructVariable
                    return var  # var 자체를 리턴 (배열, 다른 구조체일 수 있음)

            elif isinstance(callerObject, EnumDefinition):
                for enumMemberIndex in range(len(callerObject.members)):
                    if ident_str == callerObject.members[enumMemberIndex]:
                        return enumMemberIndex

            # ContractAnalyzer.evaluate_identifier_context 내부

            elif isinstance(callerObject, MappingVariable):
                if not callerObject.struct_defs or not callerObject.enum_defs:
                    all_structs, all_enums = self.an.get_full_struct_enum_defs()
                    callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                    callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

                # ── ① key 결정 ──────────────────────────────────
                if ident_str in variables:  # ident_str == 변수명
                    key_var = variables[ident_str]
                    val = getattr(key_var, "value", key_var)

                    # ★ AddressSet: singleton이면 str(val)을 key로 사용
                    if isinstance(val, AddressSet):
                        if val.is_singleton():
                            key_val = str(val)  # "AddressSet({1})"
                        else:
                            key_val = key_var.identifier  # TOP/multi → identifier
                    elif hasattr(val, "min_value"):
                        if val.min_value == val.max_value:  # 숫자·bool 싱글톤
                            key_val = str(val.min_value)
                        else:
                            # TOP 범위인 경우: 매핑에 이미 설정된 키 중 범위 내 키가 있으면 사용
                            # (디버그 annotation으로 설정된 concrete 키 우선)
                            found_key = None
                            for existing_key in callerObject.mapping.keys():
                                try:
                                    k_int = int(existing_key)
                                    if val.min_value <= k_int <= val.max_value:
                                        found_key = existing_key
                                        break
                                except (ValueError, TypeError):
                                    continue
                            key_val = found_key if found_key else key_var.identifier
                    else:
                        key_val = key_var.identifier  # string·bool 등
                else:
                    # ───── 리터럴 키 ────────────────────────────
                    try:
                        key_val = str(int(ident_str, 0))
                    except ValueError:
                        key_val = ident_str

                # ── ③ 매핑 엔트리 가져오거나 생성 ─────────────
                if key_val not in callerObject.mapping:
                    callerObject.mapping[key_val] = callerObject.get_or_create(key_val)
                mvar = callerObject.mapping[key_val]
                # ── ④ 반환 규칙 ────────────────────────────
                if isinstance(mvar, (StructVariable, ArrayVariable, MappingVariable)):
                    return mvar
                else:
                    return mvar.value
            else:
                raise ValueError(f"This '{ident_str}' may not be included in enum def '{callerObject.enum_name}'")

        # callerObject가 없고 callerContext는 있는 경우
        if callerContext is not None:
            if callerContext == "MemberAccessContext":  # base에 대한 접근
                if ident_str in variables:
                    var = variables[ident_str]
                    # interface 타입 변수: AddressSet with _cast_interface → value 반환
                    if isinstance(var, Variables) and \
                       not isinstance(var, (ArrayVariable, StructVariable, MappingVariable)) and \
                       isinstance(getattr(var, 'value', None), AddressSet) and \
                       getattr(var.value, '_cast_interface', None):
                        return var.value
                    return var  # MappingVariable, StructVariable 자체를 리턴
                elif ident_str == "this":
                    # this 키워드: 현재 컨트랙트 자체를 반환
                    return "this"
                elif ident_str == "super":
                    return "super"
                elif ident_str in ["block", "tx", "msg", "address", "code"]:
                    return ident_str  # block, tx, msg를 리턴
                elif self._find_enum_in_chain(ident_str):  # EnumDef 리턴 (parent chain 포함)
                    return self._find_enum_in_chain(ident_str)
                elif ident_str in getattr(self.an.sa, 'file_level_enums', {}):
                    # ★ interface/contract 밖, 파일 최상위에 선언된 enum(예: `Status`,
                    # web3bugs_42_H_01) — _find_enum_in_chain은 현재 컨트랙트+부모
                    # 체인의 enumDefs만 보므로 못 찾음. main.py의 load_dependencies()가
                    # regex로 미리 수집해둔 sa.file_level_enums에서 즉석으로
                    # EnumDefinition을 만들어 반환(기존 EnumDefinition 소비 경로 재사용).
                    enum_def = EnumDefinition(ident_str)
                    for m in self.an.sa.file_level_enums[ident_str]:
                        enum_def.add_member(m)
                    return enum_def
                elif ident_str in self.an.library_cfgs:
                    return {"isLibrary": True, "libraryName": ident_str}
                elif (self.an.current_target_contract and
                      self.an.current_target_contract in self.an.contract_cfgs and
                      self._is_known_parent_contract(
                          self.an.contract_cfgs[self.an.current_target_contract], ident_str)):
                    # 명시적 base-contract-qualified 호출: ParentName.foo()
                    # (super.foo()와 비슷하지만 조상 하나를 이름으로 못박는 문법)
                    return {"isBaseContract": True, "contractName": ident_str}
                else:
                    from Utils.Errors import UnresolvedName
                    raise UnresolvedName(ident_str, f"This '{ident_str}' is may be array or struct but may not be declared")
            elif callerContext == "IndexAccessContext":  # base에 대한 접근
                if ident_str in variables:
                    return variables[ident_str]  # ArrayVariable, MappingVariable 자체를 리턴

        # callerContext, callerObject 둘다 없는 경우
        if ident_str == "this":
            return "this"
        if ident_str in variables:  # variables에 있으면
            var_obj = variables[ident_str]
            # composite 타입(Struct/Array/Mapping)은 객체 자체를 반환
            if isinstance(var_obj, (StructVariable, ArrayVariable, MappingVariable)):
                return var_obj
            return var_obj.value  # 해당 value 리턴

        # library 이름이면 qualified call marker 반환 (UFixed18Lib._from(x) 등)
        if ident_str in self.an.library_cfgs:
            return {"isLibrary": True, "libraryName": ident_str}

        # (2026-10-23) 함수 이름을 값으로 쓴 것(`g = inc;`, `apply(inc, a)`) → 함수 포인터 ⊤ (대상은 추적하지 않는다)
        ccf = self._executing_contract_cfg()
        if ccf is not None and (self.find_function_overloads_in_hierarchy(ccf, ident_str)
                                or ident_str in (getattr(ccf, "functions", None) or {})):
            return FunctionRef.top()

        from Utils.Errors import UnresolvedName
        raise UnresolvedName(ident_str, f"This '{ident_str}' is may be elementary variable but may not be declared")

    def evaluate_member_access_context(
            self,
            expr: Expression,
            variables: dict[str, Variables],
            callerObject: Variables | None = None,
            callerContext: str | None = None):

        baseVal = self.evaluate_expression(expr.base, variables, None,
                                           "MemberAccessContext")
        member = expr.member

        # ──────────────────────────────────────────────────────────────
        # 0. Function call context 처리 (using directive 지원)
        # ──────────────────────────────────────────────────────────────
        # callerContext가 "functionCallContext"인 경우 - a.mul(b)에서 a.mul 부분이 호출될 때
        if callerContext == "functionCallContext":
            # baseVal이 Variables, mapping, Struct, 또는 interval인 경우 라이브러리 함수 호출 처리
            implicit_arg = baseVal

            # (2026-10-23) 수신자 타입 = 식의 **정적 타입**. Solidity 의 `using L for T` 는 수신자 식의 정적 타입으로 붙는다.
            #   예전엔 값으로 추측했다(변수 객체면 선언 타입, 구간이면 비트폭으로 uintN, 그 밖의 값이면 타입 없음) —
            #   `address(token).functionCall(...)` 의 값이 AddressSet 이라 타입을 못 정해 using-for 를 건너뛰었다(31_H_01).
            base_type = self._using_type_key(self._static_type_of(expr.base, variables))
            if base_type is None and not self._is_non_typed_receiver(expr.base, baseVal):
                raise ValueError(f"using-for lookup: static type of receiver of '.{member}(...)' is unknown")

            if base_type:
                # using directive 확인 — (2026-10-22) **실행 중인 함수가 속한** 컨트랙트/라이브러리의 using 목록.
                #   예전엔 타깃 컨트랙트 것을 봐서, 라이브러리 함수 안의 `using Address for address` 가 안 보였다(31_H_01).
                contract_cfg = self._executing_contract_cfg()
                if contract_cfg is not None:

                    # 라이브러리 함수 존재 여부만 확인 (overload 해소는 function call 시점에서)
                    library_function = contract_cfg.find_library_function(base_type, member)
                    if library_function:
                        result_expr = Expression(
                            function=Expression(identifier=member),
                            operator='library_call',
                            context='LibraryFunctionCallContext'
                        )
                        # overload 해소를 위한 정보 저장 (특정 FunctionCFG가 아닌 검색 키)
                        result_expr._library_base_type = base_type
                        result_expr._library_contract_cfg = contract_cfg
                        result_expr._implicit_first_arg = implicit_arg
                        return result_expr
        
        # ──────────────────────────────────────────────────────────────
        # 0-Q. Qualified library call (UFixed18Lib._from(x) 등)
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, dict) and baseVal.get("isLibrary"):
            lib_name = baseVal["libraryName"]
            lib_cfg = self.an.library_cfgs.get(lib_name)
            if lib_cfg and callerContext == "functionCallContext":
                result_expr = Expression(
                    function=Expression(identifier=member),
                    operator='library_call',
                    context='LibraryFunctionCallContext'
                )
                result_expr._library_base_type = None  # qualified call은 implicit first arg 없음
                result_expr._library_contract_cfg = lib_cfg
                result_expr._implicit_first_arg = None
                result_expr._qualified_library_call = True
                return result_expr
            # function call이 아닌 경우 (상수/enum 타입 등)
            # enum 타입 참조 (LibraryName.EnumName, e.g. Perpetuals.Side) - 이후 .Member 접근을
            # 위해 EnumDefinition 자체를 반환해야 함. 이걸 못 찾으면 아래 symbolic fallback으로
            # 떨어져서 문자열이 되어버리고, 그 다음 단계의 .Member 접근이 전부 깨짐.
            if lib_cfg and member in getattr(lib_cfg, 'enumDefs', {}):
                return lib_cfg.enumDefs[member]
            if lib_cfg and member in getattr(lib_cfg, 'globals', {}):
                return lib_cfg.globals[member].value
            # state_variable_node에서 상수 조회
            sv_node = getattr(lib_cfg, 'state_variable_node', None)
            if sv_node and member in getattr(sv_node, 'variables', {}):
                sv = sv_node.variables[member]
                return sv.value if hasattr(sv, 'value') else sv
            # ★ (2026-10-21) 예전엔 `"symbolic(Lib.member)"` 문자열을 돌려줘 이후 산술에서 엉뚱하게 터지거나 조용히 틀렸다.
            raise ValueError(f"unresolved library member '{lib_name}.{member}' "
                             f"(함수·enum·상수 어디에도 없음 — 라이브러리 정의 누락)")

        # ──────────────────────────────────────────────────────────────
        # 0-1. super 키워드 처리 (super.foo() → 부모 컨트랙트 함수 호출)
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, str) and baseVal == "super":
            if callerContext == "functionCallContext":
                # (2026-10-23) super.f() = 가장 파생된 컨트랙트의 C3 선형화에서 **f 를 정의한 컨트랙트 다음**부터 디스패치.
                #   예전엔 타깃 컨트랙트의 부모를 깊이 우선으로 찾아, 부모 함수 안의 super 가 엉뚱한 곳으로 갔다.
                L = self.linearize(self._dispatch_root_cfg())
                own = self.engine._fcfg_owner(self.an.current_target_function_cfg)[0]
                names = [self._cfg_name(c) for c in L]
                if own not in names:
                    raise ValueError(f"super.{member}(): executing contract '{own}' is not in the linearization {names}")
                result_expr = Expression(function=Expression(identifier=member), operator='super_call',
                                         context='SuperFunctionCallContext')
                result_expr._dispatch_L = L[names.index(own) + 1:]
                return result_expr
            raise ValueError(f"'super.{member}' used outside a call")

        # ──────────────────────────────────────────────────────────────
        # 0-1b. 명시적 base-contract-qualified 호출 (ParentName.foo())
        # super.foo()와 비슷하지만 조상 하나를 이름으로 못박는 문법 — e.g.
        # `abstract contract CrossMarginAccounts is RoleAware, PriceAware { ...
        # PriceAware.getCurrentPriceInPeg(...) ... }`. super와 동일하게
        # SuperFunctionCallContext로 태그해서 기존 소비 경로(evaluate_super_
        # function_call_context)를 그대로 재사용한다.
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, dict) and baseVal.get("isBaseContract"):
            contract_name = baseVal["contractName"]
            if callerContext == "functionCallContext":
                # (2026-10-23) `Base.f()` = Base 에서 시작하는 정적(비가상) 호출 — Base 의 선형화로 디스패치
                base_cfg = self.an.contract_cfgs.get(contract_name)
                if base_cfg is None:
                    raise ValueError(f"'{contract_name}.{member}()': contract '{contract_name}' not loaded")
                result_expr = Expression(function=Expression(identifier=member), operator='super_call',
                                         context='SuperFunctionCallContext')
                result_expr._dispatch_L = self.linearize(base_cfg)
                return result_expr

            raise ValueError(f"'{contract_name}.{member}' used outside a call (base-contract member)")

        # ──────────────────────────────────────────────────────────────
        # 0-2. this 키워드 처리 (this.foo(), this.balance, address(this).balance 등)
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, str) and baseVal == "this":
            if callerContext == "functionCallContext":
                # (2026-10-23) this.f() = 자기 자신에 대한 외부 호출 — 가장 파생된 컨트랙트 기준 가상 디스패치.
                #   예전엔 못 찾으면 uint ⊤ 를 돌려줬다(반환 타입 무시).
                result_expr = Expression(function=Expression(identifier=member), operator='this_call',
                                         context='ThisFunctionCallContext')
                result_expr._dispatch_L = self.linearize(self._dispatch_root_cfg())
                return result_expr

            # this.balance → GlobalVar에 값이 있으면 사용, 없으면 Top
            if member == "balance":
                gv_val = self._get_address_this_balance(variables)
                return gv_val if gv_val is not None else UnsignedIntegerInterval.top()

            # 기타 this 멤버 접근은 심볼릭으로 처리
            return f"this.{member}"

        # ──────────────────────────────────────────────────────────────
        # 0-2b. 동적 bytes / string 의 `.length` (2026-10-23)
        #   동적 bytes·string 의 내용·길이는 모델링하지 않는다(값은 심볼) → 길이는 uint256 ⊤.
        #   판정은 값이 아니라 수신자 식의 정적 타입으로 한다.
        # ──────────────────────────────────────────────────────────────
        if member == "length" and callerContext != "functionCallContext":
            st = self._static_type_of(expr.base, variables)
            if getattr(st, "typeCategory", None) == "elementary" \
                    and getattr(st, "elementaryTypeName", None) in ("bytes", "string"):
                return UnsignedIntegerInterval.top(256)

        # ──────────────────────────────────────────────────────────────
        # 0-3. address 변수 (Phase 4b)
        #   MemberAccessContext 의 식별자 평가는 using-for 조회(0.)를 위해 **변수 객체**를 돌려준다.
        #   using-for 로 해소되지 않은 address 변수의 멤버(.transfer/.send/.balance …)는 값(AddressSet)
        #   기준으로 처리해야 하는데, 예전엔 풀어주지 않아 6.(해석 불가 멤버)로 떨어졌다.
        # ──────────────────────────────────────────────────────────────
        if (isinstance(baseVal, Variables)
                and not isinstance(baseVal, (ArrayVariable, StructVariable, MappingVariable, EnumVariable))
                and isinstance(baseVal.value, AddressSet)):
            baseVal = baseVal.value

        # ──────────────────────────────────────────────────────────────
        # 1. Global-var (block / msg / tx)
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, str):
            if baseVal in {"block", "msg", "tx"}:
                # 0) 함수-env 에 이미 변수로 들어와 있나?
                full_name = f"{baseVal}.{member}"
                if isinstance(callerObject, MappingVariable):
                    if not callerObject.struct_defs or not callerObject.enum_defs:
                        all_structs, all_enums = self.an.get_full_struct_enum_defs()
                        callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                        callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}
                    # global var의 실제 값으로 key 결정 (identifier 경로와 동일)
                    key_val = full_name  # fallback
                    if full_name in variables:
                        gv_val = variables[full_name].value
                        if isinstance(gv_val, AddressSet):
                            if gv_val.is_singleton():
                                key_val = str(gv_val)  # "AddressSet({101})"
                            # else: TOP → full_name 유지
                        elif hasattr(gv_val, "min_value"):
                            if gv_val.min_value == gv_val.max_value:
                                key_val = str(gv_val.min_value)
                    if key_val not in callerObject.mapping:
                        callerObject.mapping[key_val] = callerObject.get_or_create(key_val)
                    entry = callerObject.mapping[key_val]
                    if isinstance(entry, (StructVariable, ArrayVariable, MappingVariable)):
                        return entry
                    return entry.value if hasattr(entry, "value") else entry

                else:
                    if full_name in variables:  # ← added
                        return variables[full_name].value  # (Variables → 값))
                    else:
                        raise ValueError(f"There is no global variable in function")

            if baseVal.startswith("type(") and member == "max":
                inner = baseVal[5:-1].strip()  # "uint256", "int224", "address", "MyERC20", ...
                m = member  # 읽기 편하게 별도 변수로

                if inner.startswith("uint") or inner.startswith("int"):
                    signed = inner.startswith("int")
                    bits_txt = inner.lstrip("uintint")  # '' 면 기본 256
                    bits = int(bits_txt) if bits_txt else 256
                    if signed:
                        i_min = -2 ** (bits - 1)
                        i_max = 2 ** (bits - 1) - 1
                        if m == "max":
                            return IntegerInterval(i_max, i_max, bits)
                        elif m == "min":
                            return IntegerInterval(i_min, i_min, bits)
                    else:
                        u_min = 0
                        u_max = 2 ** bits - 1
                        if m == "max":
                            return UnsignedIntegerInterval(u_max, u_max, bits)
                        elif m == "min":
                            return UnsignedIntegerInterval(u_min, u_min, bits)

                    # ---- address ------------------------------------------------------
                if inner == "address":
                    if m == "max":
                        # ★ address의 max는 모든 주소 가능 → TOP
                        return AddressSet.top()
                    if m == "min":
                        # ★ address의 min은 0 주소
                        return AddressSet(ids={0})

                    # ---- bytes<M>  (고정 길이) ----------------------------------------
                if inner.startswith("bytes") and inner != "bytes":
                    # 컴파일 타임 바이트 시퀀스 최대/최소 → 심볼릭 문자열이면 충분
                    return f"{inner}.{m}"  # 예: "bytes32.max"

                    # ---- 컨트랙트 타입  (MyERC20) --------------------------------------
                    # creationCode / runtimeCode / interfaceId
                if m in {"creationCode", "runtimeCode", "interfaceId"}:
                    return f"symbolic_{inner}_{m}"  # 심볼릭 스트링

                    # ---- type(SomeType).name ------------------------------------------
                if m == "name":
                    return inner  # 그냥 타입 이름 문자열

                    # ---- 기타 미지원 멤버 ---------------------------------------------
                return f"symbolicMeta({inner}.{m})"

            # address.code / address.code.length
            if baseVal == "code":
                if member == "length":
                    # 코드 사이즈 – 예시로 고정 상수
                    return UnsignedIntegerInterval(0, 24_000, 256)
                return member  # address.code → 다음 단계에서 .length 접근

            if member == "code":  # <addr>.code
                return "code"  # 상위 계층에서 재귀적으로 처리

            # interface cast된 address에서 interface 함수 호출 (IERC20(x).balanceOf())
            cast_ifc = getattr(baseVal, '_cast_interface', None)
            if cast_ifc and callerContext == "functionCallContext":
                lib_call = self._using_for_on_cast(cast_ifc, member, baseVal)
                if lib_call is not None:
                    return lib_call
                # interface function lookup → return type 기반 top 반환
                result_expr = Expression(
                    function=Expression(identifier=member),
                    operator='interface_call',
                    context='InterfaceFunctionCallContext'
                )
                result_expr._cast_interface = cast_ifc
                result_expr._cast_address = baseVal
                return result_expr

            raise ValueError(f"member '{member}' is not a recognised global-member.")

        # ──────────────────────────────────────────────────────────────
        # 2. ArrayVariable  ( .myArray.length  /  .push() / .pop() )
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, ArrayVariable):
            if member == "length":
                # (2026-10-23) 추상 길이 — 구체화한 원소 수(len(elements))가 아니다. `array_length` 참고.
                return array_length(baseVal)

            # .push() / .pop()  – 동적배열만 허용
            if callerContext == "functionCallContext":
                if not baseVal.typeInfo.isDynamicArray:
                    raise ValueError("push / pop available only on dynamic arrays")
                elemType = baseVal.typeInfo.arrayBaseType

                # (2026-10-23) push/pop 은 추상 길이(baseVal.length)를 갱신한다. 새 원소의 위치 = 현재 길이 —
                #   길이가 정해져 있으면 그 원소에 strong update, 아니면(미지 길이·widening) 그 위치가 미확정이므로
                #   겹칠 수 있는 원소와 summary 에 weak update. 예전엔 elements 에 append 해 "원소 수 = 길이" 로 취급했다.
                L = array_length(baseVal)
                engine = getattr(self.an, 'engine', None)
                in_widening = bool(engine and getattr(engine, '_in_widening_mode', False))
                if expr.member == "push":
                    val = self.evaluate_expression(expr.arguments[0], variables) if expr.arguments else None
                    if L.min_value == L.max_value and not in_widening:
                        n = L.min_value
                        elem = baseVal.get_or_create_element(n)
                        if val is not None:
                            elem.value = val
                        baseVal.length = UnsignedIntegerInterval(n + 1, n + 1, 256)
                    else:
                        new = baseVal._create_new_array_element("*")
                        if val is not None:
                            new.value = val
                        hi = L.max_value
                        for i, e in baseVal.elements.items():
                            if L.min_value <= i <= hi:
                                _join_into(e, new)
                        if baseVal.summary is None:
                            baseVal.summary = baseVal.fresh_default()
                        _join_into(baseVal.summary, new)
                        mx = 2 ** 256 - 1
                        baseVal.length = (UnsignedIntegerInterval.top(256) if in_widening else
                                          UnsignedIntegerInterval(min(L.min_value + 1, mx), min(hi + 1, mx), 256))
                    return None  # Solidity push 는 값 반환 X

                if expr.member == "pop":
                    if L.max_value == 0:  # 빈 배열 pop → revert (경로 차단은 Phase 5)
                        return None
                    if L.min_value == L.max_value:
                        n = L.min_value
                        baseVal.elements = {k: v for k, v in baseVal.elements.items() if k < n - 1}
                    baseVal.length = UnsignedIntegerInterval(max(L.min_value, 1) - 1, L.max_value - 1, 256)
                    return None

        # ──────────────────────────────────────────────────────────────
        # 3. StructVariable  ( struct.field )
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, StructVariable):
            if member not in baseVal.members:
                raise ValueError(f"'{member}' not in struct '{baseVal.identifier}'")

            nested = baseVal.members[member]

            # ★ index-access 의 **키**로 쓰인 멤버 접근이면 매핑 조회까지 해야 한다.
            #   `evaluate_index_access_context` 는 인덱스 식 평가에 `callerObject=매핑` 을
            #   넘겨 **인덱스 식 핸들러가 매핑 조회를 마치도록** 설계돼 있다.
            #   리터럴 키(`evaluate_literal_context`)와 식별자 키
            #   (`evaluate_identifier_context`)는 그 처리를 하는데
            #   **멤버 접근 키만 빠져 있었다** — 매핑을 그냥 버리고 멤버 값만 돌려줬다.
            #   그 결과 `T.Tick storage previous = ticks[current.previousTick];` 가
            #   `current.previousTick` 의 **int24 값**으로 평가되고,
            #   `process_variable_declaration` 의 `isinstance(resolved, StructVariable)`
            #   분기를 못 타 빈 struct 가 그대로 남아
            #   `Member 'nextTick' not in struct 'previous'` 로 터졌다
            #   (Ticks.sol 4건 + AddressProviderMeta 2건).
            #   `_mapping_lookup_if_needed` 는 callerObject 가 MappingVariable 이 아니면
            #   **그대로 통과**하므로 기존 경로는 영향이 없다. 매핑일 때는
            #   `get_or_create` → `_make_value` 로 **정의가 붙은 struct** 를 만든다.

            # ① enum (저장형 uint) -----------------------------------------
            if isinstance(nested, EnumVariable):
                return self._mapping_lookup_if_needed(nested.value, callerObject)

            # ② leaf-variable ---------------------------------------------
            if (isinstance(nested, Variables) and
                    not isinstance(nested, (ArrayVariable,
                                            StructVariable,
                                            MappingVariable))):
                return self._mapping_lookup_if_needed(nested.value, callerObject)

            # ③ 배열·구조체·매핑 ------------------------------------------
            return self._mapping_lookup_if_needed(nested, callerObject)

        # ──────────────────────────────────────────────────────────────
        # 3-A. AddressSet  (address.balance, address.transfer(), address.send())
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, AddressSet):
            # address.balance → GlobalVar에 address(this).balance가 있으면 사용
            if member == "balance":
                gv_val = self._get_address_this_balance(variables)
                return gv_val if gv_val is not None else UnsignedIntegerInterval.top()

            # address.code → bytes Top
            if member == "code":
                return "code"  # 상위 계층에서 .length 접근 처리

            # address.codehash → bytes32 Top
            if member == "codehash":
                return BytesSet.top(32)

            # 함수 호출 컨텍스트에서의 처리
            if callerContext == "functionCallContext":
                # address.transfer(amount) → void (reverts on failure)
                if member == "transfer":
                    # transfer는 실패 시 revert하므로 리턴값 없음
                    # 단순히 None 반환 (상태 변경 없음으로 가정)
                    return None

                # address.send(amount) → bool (true=success, false=failure)
                if member == "send":
                    # send는 성공/실패를 bool로 반환
                    return BoolInterval.top()  # [0,1] 둘 다 가능

                # address.call/delegatecall/staticcall(data) → (bool success, bytes memory data)
                #   (Phase 4b) 예전엔 bool ⊤ 하나만 돌려 `(bool ok, bytes memory d) = a.call(...)` 의 튜플 대입이
                #   터졌다. 다중 반환은 리스트로 표현한다(슬롯 위치 보존). bytes 는 다른 동적 bytes 와 같이 심볼.
                if member in ("call", "delegatecall", "staticcall"):
                    return [BoolInterval.top(), f"symbol_{member}_returndata"]

            # interface cast된 address의 함수 호출 (IERC20(x).balanceOf())
            cast_ifc = getattr(baseVal, '_cast_interface', None)
            if cast_ifc and callerContext == "functionCallContext":
                lib_call = self._using_for_on_cast(cast_ifc, member, baseVal)
                if lib_call is not None:
                    return lib_call
                result_expr = Expression(
                    function=Expression(identifier=member),
                    operator='interface_call',
                    context='InterfaceFunctionCallContext'
                )
                result_expr._cast_interface = cast_ifc
                result_expr._cast_address = baseVal
                return result_expr

            # (2026-10-23) 예전엔 `"address.<member>"` 문자열을 값으로 돌려줬다 — `address(token).functionCall(...)` 의
            #   결과가 그 문자열이 되어 다음 줄 `returndata.length` 에서 엉뚱하게 터졌다(31_H_01).
            raise ValueError(f"unresolved address member '.{member}' "
                             f"(내장 멤버도, using-for 라이브러리 함수도 아님)")

        # ──────────────────────────────────────────────────────────────
        # 4. EnumDefinition  (EnumType.RED)
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, EnumDefinition):
            try:
                idx = baseVal.members.index(member)
                return UnsignedIntegerInterval(idx, idx, 256)
            except ValueError:
                raise ValueError(f"'{member}' not a member of enum '{baseVal.enum_name}'")

        # ──────────────────────────────────────────────────────────────
        # 5. Solidity type(uint).max / min  (baseVal == dict with "isType")
        # ──────────────────────────────────────────────────────────────
        if isinstance(baseVal, dict) and baseVal.get("isType"):
            T = baseVal["typeName"]
            if member not in {"max", "min"}:
                raise ValueError(f"Unsupported type property '{member}' for {T}")

            if T.startswith("uint"):
                bits = int(T[4:]) if len(T) > 4 else 256
                if member == "max":
                    mx = 2 ** bits - 1
                    return UnsignedIntegerInterval(mx, mx, bits)
                return UnsignedIntegerInterval(0, 0, bits)  # min

            if T.startswith("int"):
                bits = int(T[3:]) if len(T) > 3 else 256
                if member == "max":
                    mx = 2 ** (bits - 1) - 1
                    return IntegerInterval(mx, mx, bits)
                mn = -2 ** (bits - 1)
                return IntegerInterval(mn, mn, bits)

            raise ValueError(f"type() with unsupported base '{T}'")

        # ──────────────────────────────────────────────────────────────
        # 6. 해석 불가 멤버 → 예외 (2026-10-21)
        #   예전엔 `"symbolic(<값>.<멤버>)"` 문자열을 돌려줬다. 그 문자열이 `uint256(...)` 을 거쳐 산술에 들어가
        #   `'str' object has no attribute 'multiply'` 로 엉뚱한 곳에서 터졌다(web3bugs_52_H_23: `using UQ112x112 for
        #   uint224` 누락으로 `.uqdiv` 미해결). 근본 원인(using-for·정의 누락)이 드러나도록 여기서 멈춘다.
        # ──────────────────────────────────────────────────────────────
        raise ValueError(f"unresolved member '.{member}' on {type(baseVal).__name__} value {str(baseVal)[:60]} "
                         f"(using-for 지시자 또는 정의 누락)")

    def _get_variable_type_string(self, var: Variables) -> str:
        """Variables 객체에서 타입 문자열 추출"""
        if hasattr(var, 'typeInfo') and var.typeInfo:
            type_info = var.typeInfo
            if type_info.typeCategory == "elementary":
                # type alias가 있으면 원래 이름 반환 (library function lookup용)
                if getattr(type_info, 'aliasName', None):
                    return type_info.aliasName
                return type_info.elementaryTypeName
            elif type_info.typeCategory == "array":
                return f"{self._get_type_string_from_soltype(type_info.arrayBaseType)}[]"
            elif type_info.typeCategory == "mapping":
                return "mapping"
            elif type_info.typeCategory == "struct":
                return type_info.structTypeName
            elif type_info.typeCategory == "enum":
                return type_info.enumTypeName
        
        # 기본값으로 "unknown" 반환
        return "unknown"
    
    def _get_type_string_from_soltype(self, sol_type) -> str:
        """SolType 객체에서 타입 문자열 추출"""
        if sol_type.typeCategory == "elementary":
            return sol_type.elementaryTypeName
        elif sol_type.typeCategory == "array":
            return f"{self._get_type_string_from_soltype(sol_type.arrayBaseType)}[]"
        elif sol_type.typeCategory == "struct":
            return sol_type.structTypeName
        elif sol_type.typeCategory == "enum":
            return sol_type.enumTypeName
        else:
            return "unknown"

    def evaluate_index_access_context(self, expr, variables, callerObject=None, callerContext=None):
        """
        해석 로직:
          1) base_val = evaluate_expression(expr.base, variables, ..., callerContext="IndexAccessContext")
          2) index_val = evaluate_expression(expr.index, variables, callerObject=base_val, callerContext="IndexAccessContext")
          3) base_val이 ArrayVariable이면 -> arrayVar.elements[index]
             base_val이 MappingVariable이면 -> mappingVar.mapping[indexKey]
             그 외 -> symbolic/error
        """

        # 1) base 해석
        base_val = self.evaluate_expression(expr.base, variables, None, "IndexAccessContext")

        # (2026-10-23) mapping / array 는 키 해석·선택을 Storage 한 곳에서 — 식별자·리터럴·멤버·이항식 인덱스마다
        #   따로 있던 키 규칙(첫 구체 키 선택, 표본 join, 이름 키 등)이 서로 달라 unsound 했다. Storage.py 참고.
        if isinstance(base_val, (MappingVariable, ArrayVariable)) and expr.index is not None:
            if not base_val.struct_defs or not base_val.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                base_val.struct_defs = {**all_structs, **base_val.struct_defs}
                base_val.enum_defs = {**all_enums, **base_val.enum_defs}
            cell = self.an.storage.read(base_val, expr.index, variables)
            return cell if is_composite(cell) else cell.value

        if expr.index is not None:
            return self.evaluate_expression(expr.index, variables, base_val, "IndexAccessContext")
        else:
            raise ValueError(f"There is no index expression")

    def evaluate_literal_with_subdenomination_context(
            self, expr: Expression, variables,
            callerObject=None, callerContext=None):
        """
        · expr.literal 은 이제 604800 처럼 *이미 환산된* 10진수 문자열이다.
        · 모든 sub-denom 값은 양수이므로 uint256 TOP 안에 들어간다.
        """

        lit_txt = expr.literal  # e.g. '604800'
        try:
            abs_val = int(lit_txt, 10)
        except ValueError:
            raise ValueError(f"Invalid pre-evaluated literal '{lit_txt}'")

        # uint256 상수 Interval 로 반환
        return UnsignedIntegerInterval(abs_val, abs_val, 256)

    def evaluate_type_conversion_context(self, expr, variables, callerObject=None, callerContext=None):
        """
        expr: Expression(operator='type_conversion', type_name=..., expression=subExpr, context='TypeConversionContext')
        예:  'uint256(x)', 'int8(y)', 'bool(z)', 'address w' 등

        1) sub_val = evaluate_expression(expr.expression, variables, None, "TypeConversion")
        2) if type_name.startswith('uint'):  -> UnsignedIntegerInterval로 클램핑
           if type_name.startswith('int'):   -> IntegerInterval로 클램핑
           if type_name == 'bool':           -> 0이면 False, 나머지면 True (또는 Interval [0,1])
           if type_name == 'address':        -> int/Interval -> symbolic address, string '0x...' 등등
        3) 반환
        """

        type_name = expr.typeName  # 예: "uint256", "int8", "bool", "address"
        sub_val = self.evaluate_expression(expr.expression, variables, None, "TypeConversion")

        # 1) 우선 sub_val이 Interval(혹은 BoolInterval), str, etc. 중 어느 것인가 확인
        #    편의상, 아래에서 Interval이면 클램핑, BoolInterval이면 bool 변환 등 처리

        # a. bool, int, uint, address 등으로 나누어 처리
        if type_name.startswith("uint"):
            # 예: "uint256", "uint8" 등
            # 1) bits 추출
            bits_str = "".join(ch for ch in type_name[4:] if ch.isdigit())  # "256" or "8" 등
            bits = int(bits_str) if bits_str else 256

            # 2) sub_val이 IntegerInterval/UnsignedIntegerInterval 이라면:
            #    - 음수 부분은 0으로 clamp
            #    - 상한은 2^bits - 1로 clamp
            #    - 만약 sub_val이 BoolInterval, string, etc. => 대략 변환 로직 / symbolic
            result = self.convert_to_uint(sub_val, bits)
            # ★ callerObject가 Array/MappingVariable이면 이 cast는 인덱스/키로
            # 쓰인 것(예: `arr[uint256(EnumType.Member)]`) — evaluate_binary_operator가
            # 이미 하는 것과 동일하게 실제 element/entry lookup으로 위임
            # (web3bugs_70_H_04, `totalLiquidityWeight[uint256(Paths.VADER)]`에서 발견:
            # 이 분기가 없어서 인덱스 값 자체(0)를 배열 원소인 것처럼 리턴하고 있었음).
            if isinstance(callerObject, (ArrayVariable, MappingVariable)):
                return self.evaluate_binary_operator_of_index(result, callerObject)
            return result

        elif type_name.startswith("int"):
            # 예: "int8", "int256"
            bits_str = "".join(ch for ch in type_name[3:] if ch.isdigit())
            bits = int(bits_str) if bits_str else 256
            result = self.convert_to_int(sub_val, bits)
            if isinstance(callerObject, (ArrayVariable, MappingVariable)):
                return self.evaluate_binary_operator_of_index(result, callerObject)
            return result

        elif type_name == "bool":
            # sub_val이 Interval이면:
            #   == 0 => bool false
            #   != 0 => bool true
            # 범위 넓으면 [0,1]
            return self.convert_to_bool(sub_val)

        elif type_name == "address":
            # ★ address 타입 변환
            if isinstance(sub_val, AddressSet):
                # (2026-10-22) 결과의 정적 타입은 address — 인터페이스 태그를 떼어 낸 새 값. 예전엔 `address(token)` 이
                #   IERC20 태그를 그대로 가져가 `address(token).functionCall(...)`(using Address for address) 을
                #   인터페이스 호출로 오인했다(31_H_01).
                addr_result = sub_val.retagged(None)
            elif isinstance(sub_val, (UnsignedIntegerInterval, IntegerInterval)):
                # uint → address: singleton이면 구체적 ID, 아니면 TOP
                if sub_val.is_bottom():
                    addr_result = AddressSet.bot()
                elif sub_val.min_value == sub_val.max_value:
                    addr_result = AddressSet(ids={sub_val.min_value})
                else:
                    addr_result = AddressSet.top()
            elif isinstance(sub_val, str) and sub_val.startswith("0x"):
                addr_int = int(sub_val, 16)
                addr_result = AddressSet(ids={addr_int})
            elif isinstance(sub_val, str) and sub_val == "this":
                # ★ address(this): 현재 컨트랙트 주소 (symbolic ID 1)
                addr_result = self.an.addr_mgr.make_symbolic_address(1, "this")
            else:
                addr_result = AddressSet.top()

            # ★ callerObject가 MappingVariable이면 mapping lookup 수행
            if isinstance(callerObject, MappingVariable):
                if not callerObject.struct_defs or not callerObject.enum_defs:
                    all_structs, all_enums = self.an.get_full_struct_enum_defs()
                    callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                    callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}
                key_val = str(addr_result)
                if key_val not in callerObject.mapping:
                    callerObject.mapping[key_val] = callerObject.get_or_create(key_val)
                mvar = callerObject.mapping[key_val]
                if isinstance(mvar, (StructVariable, ArrayVariable, MappingVariable)):
                    return mvar
                return mvar.value

            return addr_result

        # ★ payable(addr) 타입 변환 - address와 동일하게 처리
        elif type_name == "payable" or type_name == "address payable":
            # payable은 Ether를 받을 수 있는 address - 추상 해석에서는 address와 동일
            if isinstance(sub_val, AddressSet):
                return sub_val.retagged(None)  # 정적 타입 = address (인터페이스 태그 제거, 새 값)
            if isinstance(sub_val, (UnsignedIntegerInterval, IntegerInterval)):
                if sub_val.is_bottom():
                    return AddressSet.bot()
                if sub_val.min_value == sub_val.max_value:
                    return AddressSet(ids={sub_val.min_value})
                return AddressSet.top()
            if isinstance(sub_val, str) and sub_val.startswith("0x"):
                addr_int = int(sub_val, 16)
                return AddressSet(ids={addr_int})
            if isinstance(sub_val, str) and sub_val == "this":
                return self.an.addr_mgr.make_symbolic_address(1, "this")
            return AddressSet.top()

        # bytes32, bytes16 등 고정 크기 바이트 배열 타입 변환
        elif type_name.startswith("bytes") and len(type_name) > 5:
            byte_size = int(type_name[5:])  # "bytes32" -> 32
            # 이미 BytesSet이면 그대로
            if isinstance(sub_val, BytesSet):
                return sub_val
            # uint/int → bytes: singleton이면 구체적 값, 아니면 TOP
            if isinstance(sub_val, (UnsignedIntegerInterval, IntegerInterval)):
                if sub_val.is_bottom():
                    return BytesSet.bot(byte_size)
                if sub_val.min_value == sub_val.max_value:
                    return BytesSet(values={sub_val.min_value}, byte_size=byte_size)
                return BytesSet.top(byte_size)
            # 16진수 문자열 → bytes
            if isinstance(sub_val, str) and sub_val.startswith("0x"):
                try:
                    val_int = int(sub_val, 16)
                    return BytesSet(values={val_int}, byte_size=byte_size)
                except ValueError:
                    pass
            return BytesSet.top(byte_size)  # 기타 → symbolic TOP

        # interface/contract type cast: IERC20(_token), IVault(addr) 등
        elif type_name in self.an.interface_names or type_name in self.an.contract_cfgs:
            # (2026-10-22) 새 값에 태그 — 예전엔 sub_val 객체에 직접 태그를 써서 캐스트한 원래 변수의 값까지 바뀌었다
            base = sub_val if isinstance(sub_val, AddressSet) else AddressSet.top()
            return base.retagged(type_name)

        else:
            # 그 외( string, etc. ) => 필요 시 구현
            return f"symbolicTypeConversion({type_name}, {sub_val})"

    def evaluate_conditional_expression_context(self, expr, variables, callerObject=None, callerContext=None):
        """
        삼항 연산자 (condition ? true_expr : false_expr)
        expr: Expression(
          condition=...,  # condition expression
          true_expr=...,  # true-branch expression
          false_expr=..., # false-branch expression
          operator='?:',
          context='ConditionalExpContext'
        )
        """

        # 1) 조건식 해석
        cond_val = self.evaluate_expression(expr.condition, variables, None, "ConditionalCondition")
        # cond_val이 BoolInterval일 가능성이 높음
        # 다른 경우(Interval 등) => symbolic or 0≠0 ?

        if isinstance(cond_val, BoolInterval):
            # (a) cond_val이 [1,1] => 항상 true
            if cond_val.min_value == 1 and cond_val.max_value == 1:
                return self.evaluate_expression(expr.true_expr, variables, callerObject, "ConditionalExp")

            # (b) cond_val이 [0,0] => 항상 false
            if cond_val.min_value == 0 and cond_val.max_value == 0:
                return self.evaluate_expression(expr.false_expr, variables, callerObject, "ConditionalExp")

            # (c) cond_val이 [0,1] => 부분적 => 두 branch 모두 해석 후 join
            true_val = self.evaluate_expression(expr.true_expr, variables, callerObject, "ConditionalExp")
            false_val = self.evaluate_expression(expr.false_expr, variables, callerObject, "ConditionalExp")

            return self._join_branch_values(true_val, false_val)

        # ★ (Phase 3b, D4) 조건이 bool 구간이 아닌 경우를 심볼 문자열로 넘기지 않는다.
        raise ValueError(f"conditional expression: condition is not a bool value ({type(cond_val).__name__})")

    def _join_branch_values(self, a, b):
        """삼항식 두 분기 값의 join. 튜플이면 원소별 (Phase 3b).
        ★ 예전엔 join 할 수 없으면 `"symbolicConditional(...)"` 문자열을 반환했다(튜플 분기 포함) — 그 문자열이
          튜플 대입에서 조용히 무시돼 변수들이 초기값으로 남았다."""
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
            return [self._join_branch_values(x, y) for x, y in zip(a, b)]
        if hasattr(a, 'join') and hasattr(b, 'join') and type(a) == type(b):
            return a.join(b)
        if isinstance(a, Variables) and isinstance(b, Variables):
            return VariableEnv._merge_values(a, b, "join")
        raise ValueError(f"conditional expression: cannot join branch values "
                         f"{type(a).__name__} / {type(b).__name__}")

    def evaluate_inline_array_expression_context(self, expr, variables, callerObject=None, callerContext=None):
        """
        expr: Expression(
           elements = [ expr1, expr2, ... ],
           expr_type = 'array',
           context   = 'InlineArrayExpressionContext'
        )

        이 배열 표현식은 예: [1,2,3], [0x123, 0x456], [true, false], ...
        각 요소를 재귀적으로 evaluate_expression으로 해석하고, 그 결과들을 리스트로 만든다.
        """

        results = []
        for elem_expr in expr.elements:
            # 각 요소를 재귀 해석
            # callerObject, callerContext는 "inline array element"로 명시
            val = self.evaluate_expression(elem_expr, variables, None, "InlineArrayElement")
            results.append(val)

        # -- 2) 여기서 optional로, 모든 요소가 Interval인지, BoolInterval인지, etc.를 확인해
        #       "동일한 타입"인지 검사하거나, 적절히 symbolic 처리할 수도 있음.
        # 여기서는 단순히 그대로 반환

        return results

    def evaluate_assignment_expression(self, expr, variables,
                                       callerObject=None, callerContext=None):
        """
        대입식이 ‘값을 돌려주는 표현식’ 으로 사용될 때 처리.
          예)  (z = x + y)
        ①  RHS 값을 계산
        ②  LHS 변수에 반영(update_left_var)
        ③  RHS 값을 그대로 반환
        """
        r_val = self.evaluate_expression(expr.right, variables, None, None)
        # LHS 쪽 환경 업데이트
        self.up.update_left_var(expr.left, r_val, '=', variables,
                             callerObject, callerContext, None, None, False)
        return r_val  # ← ‘값을 돌려주기’ 핵심!

    def evaluate_tuple_expression_context(self, expr, variables,
                                          callerObject=None, callerContext=None):
        # 각 요소 평가
        if any(e is None for e in expr.elements):
            # 빈 슬롯은 대입 좌변(`(, , x) = …`)에서만 의미가 있다 — 값으로 평가할 수 없다
            raise ValueError("tuple expression with empty slots cannot be evaluated as a value")
        elems = [self.evaluate_expression(e, variables, None, "TupleElem")
                 for e in expr.elements]

        # (a) 요소가 1개뿐 ⇒ 괄호식이거나 return (X) 같은 형태
        if len(elems) == 1:
            return elems[0]  # <- Interval · 값 그대로 반환

        # (b) 진짜 튜플 (a,b,...) ⇒ 리스트 유지
        return elems  # [v1, v2, ...]

    def evaluate_unary_operator(self, expr, variables,
                                callerObject=None, callerContext=None):

        operand_val = self.evaluate_expression(expr.expression, variables, None, "Unary")

        if operand_val is None:
            raise ValueError(f"Unable to evaluate operand in unary expression: {expr}")

        op = expr.operator
        if op == '-' and isinstance(operand_val, UnsignedIntegerInterval) and _is_constant_expr(expr.expression)                 and not operand_val.is_bottom():
            # (Phase 4b) `-1` 등 상수식의 부호 반전 = **타입 없는 유리수** 음수(문맥 타입은 대입·상대 피연산자가 정함).
            #   예전엔 uint256 리터럴에 wrap 을 적용해 `return -1;`(int256) 이 2^256-1 이 됐다.
            return IntegerInterval(-operand_val.max_value, -operand_val.min_value, 256)
        if op == '-':
            result = operand_val.negate()
        elif op == '!':
            result = operand_val.logical_not()
        elif op == '~':
            result = operand_val.bitwise_not()
        elif op == 'delete':
            # 분석 단계에서는 “완전 미정” 값으로 — 스칼라는 0-singleton,
            # Interval 이면 같은 bit-width bottom 으로.
            if hasattr(operand_val, "bottom"):
                result = operand_val.bottom(getattr(operand_val, "type_length", 256))
            else:
                result = 0
        else:
            return

        # ★ callerObject가 Array/MappingVariable이면 이 단항식이 인덱스/키로
        # 쓰인 것(예: `arr[~x]`) — evaluate_type_conversion_context와 동일한
        # 패턴으로 evaluate_binary_operator_of_index에 위임.
        if isinstance(callerObject, (ArrayVariable, MappingVariable)):
            return self.evaluate_binary_operator_of_index(result, callerObject)
        return result

    def evaluate_binary_operator(self, expr, variables, callerObject=None, callerContext=None):
        leftInterval = self.evaluate_expression(expr.left, variables, None, "Binary")
        rightInterval = self.evaluate_expression(expr.right, variables, None, "Binary")
        operator = expr.operator

        result = None

        def _bottom(interval) -> "Interval":
            """
            interval 과 동일한 클래스·bit-width로 ⊥(bottom) 을 만들어 준다.
            (IntegerInterval.bottom(bits) 같은 헬퍼 통일)
            """
            if isinstance(interval, IntegerInterval):
                return IntegerInterval.bottom(interval.type_length)
            if isinstance(interval, UnsignedIntegerInterval):
                return UnsignedIntegerInterval.bottom(interval.type_length)
            if isinstance(interval, BoolInterval):
                return BoolInterval.bottom()
            return Interval(None, None)  # fallback – 거의 안 옴

        left_bottom = isinstance(leftInterval, Interval) and leftInterval.is_bottom()
        right_bottom = isinstance(rightInterval, Interval) and rightInterval.is_bottom()
        if left_bottom or right_bottom:
            # || : short-circuit — 한쪽이 true 가능하면 결과도 true 가능
            if operator == '||':
                if not left_bottom and isinstance(leftInterval, BoolInterval) and leftInterval.max_value == 1:
                    return BoolInterval(0, 1)
                if not right_bottom and isinstance(rightInterval, BoolInterval) and rightInterval.max_value == 1:
                    return BoolInterval(0, 1)
                return BoolInterval.bottom()
            # && : 한쪽이 BOTTOM이면 BOTTOM
            if operator in ['==', '!=', '<', '>', '<=', '>=', '&&']:
                return BoolInterval.bottom()
            return _bottom(leftInterval if not left_bottom else rightInterval)

        # (Phase 4b) 산술·비트 연산은 공통 타입에서 계산 — 왼쪽 폭이 아니라(Solidity 의미)
        if operator in _ARITH_OPS:
            leftInterval, rightInterval = _unify_operand_types(expr, leftInterval, rightInterval)

        if operator == '+':
            result = leftInterval.add(rightInterval)
        elif operator == '-':
            if isinstance(leftInterval, UnsignedIntegerInterval):
                # (Phase 2) unchecked 블록에서는 wrap — Engine 이 문장 해석 중 노드 플래그로 켠다
                result = leftInterval.subtract(rightInterval, wrapping=self.an.engine._unchecked)
            else:
                result = leftInterval.subtract(rightInterval)
        elif operator == '*':
            result = leftInterval.multiply(rightInterval)
        elif operator == '/':
            result = leftInterval.divide(rightInterval)
        elif operator == '%':
            result = leftInterval.modulo(rightInterval)
        elif operator == '**':
            result = leftInterval.exponentiate(rightInterval)
        # 시프트 연산자 처리
        # ★ Solidity 에서 시프트는 산술이 아니다 — 좌변=피시프트값 / 우변=시프트양 으로
        #   역할이 다르고, 시프트 양은 **부호 없는 타입**이며 좌변은 signed 여도 된다.
        #   `int256 log_2 = (int256(msb) - 128) << 64;` 는 유효한 코드다(TickMath L131).
        #   예전엔 산술처럼 "양쪽 타입 일치"를 요구해서 이게 거부됐고, 그 한 줄이 실패하면
        #   `log_2` 선언이 안 돼 뒤따르는 assembly 18줄이 "'log_2' may not be declared" 로
        #   연쇄 실패했다. 결과 타입은 **좌변을 따른다**.
        #   도메인의 shift() 는 shift_iv 에서 min/max 만 읽으므로 좌변 타입으로 맞춰 넘기면 된다.
        elif operator in ('<<', '>>', '>>>'):
            #   Interval.shift 는 shift_iv 에서 min/max 만 읽고 bottom·음수도 자체 처리하므로
            #   타입 변환 없이 그대로 넘긴다(결과 타입은 좌변을 따른다).
            _IV = (IntegerInterval, UnsignedIntegerInterval)
            if isinstance(leftInterval, _IV) and isinstance(rightInterval, _IV):
                result = leftInterval.shift(rightInterval, operator)
            else:
                raise ValueError(
                    f"Shift operands must be int/uint intervals, got "
                    f"{type(leftInterval).__name__} and {type(rightInterval).__name__}"
                )
        # 비트 연산자 처리
        # ★ 도메인(Domain/Interval.py)엔 bitwise()/bitwise_not() 이 양쪽 클래스에 이미
        #   구현돼 있었는데(Unsigned: '&'→[0,min(hi,hi)], '|','^'→[0,max(hi,hi)];
        #   Signed: 보수적 top), **여기 디스패치만 빠져 있었다.** 그래서 Solidity 식의
        #   `absTick & 0x2` 같은 것이 전부 "Unsupported operator" 로 떨어졌고
        #   (TickMath 등 약 100건), 대입문이 통째로 실패해 LHS 가 이전 값을 유지했다.
        #   Yul 도 _BINARY_OPS 가 and/or/xor 를 '&','|','^' 로 매핑하므로 같이 죽었다.
        #   단항 '~' 는 evaluate_unary_operator(:1827) 에 이미 배선돼 있다.
        #   타입 불일치는 시프트와 동일하게 예외 — ⊤ 로 덮지 않는다.
        elif operator in ('&', '|', '^'):
            _L, _R = leftInterval, rightInterval
            if type(_L) is not type(_R):
                # ★ 숫자 리터럴은 타입이 없다 — 상대 피연산자가 타입을 정한다.
                #   `int256 x, y` 의 `x & y & 1` (PRBMathSD59x18.sol:27) 처럼
                #   uint 로 못박힌 리터럴이 signed 와 만나는 경우를 여기서 해소한다.
                _r = _literal_retype(expr.right, _R, type(_L))
                if _r is not None:
                    _R = _r
                else:
                    _l = _literal_retype(expr.left, _L, type(_R))
                    if _l is not None:
                        _L = _l
            if type(_L) is not type(_R) and _is_yul_ctx(expr):
                # ★ Yul 에는 타입이 없다 — 모든 값이 raw 256-bit word 다.
                #   Solidity 선언(`int256 log_2`)이 Yul 블록으로 새어 들어온 것뿐이라
                #   (`log_2 := or(log_2, shl(63, f))` — TickMath 14건 + PRBMathSD59x18 1건)
                #   여기서는 **2의 보수 word 로 재해석**해 좌변 타입에 맞춘다.
                #   부호 비트가 구간 안에서 변하면 재해석이 정확하지 않으므로 TOP(건전).
                #   ※ Solidity 소스 경로(표식 없음)는 아래 타입 일치 검사를 그대로 탄다.
                _R = _reinterpret_as(_R, type(_L))
            if (isinstance(_L, IntegerInterval) and isinstance(_R, IntegerInterval)) or                (isinstance(_L, UnsignedIntegerInterval) and isinstance(_R, UnsignedIntegerInterval)):
                result = _L.bitwise(operator, _R)
            else:
                raise ValueError(
                    f"Bitwise operands must both be int/uint intervals, got "
                    f"{type(leftInterval).__name__} and {type(rightInterval).__name__}"
                )
        # 비교 연산자 처리
        elif operator in ['==', '!=', '<', '>', '<=', '>=']:
            # ★ AddressSet 비교
            if isinstance(leftInterval, AddressSet) and isinstance(rightInterval, AddressSet):
                if operator == '==':
                    result = leftInterval.equals(rightInterval)
                elif operator == '!=':
                    result = leftInterval.not_equals(rightInterval)
                else:
                    # <, >, <=, >= 는 address에 대해 정의되지 않음
                    result = BoolInterval.top()
            # ★ BytesSet 비교
            elif isinstance(leftInterval, BytesSet) and isinstance(rightInterval, BytesSet):
                if operator == '==':
                    result = leftInterval.equals(rightInterval)
                elif operator == '!=':
                    result = leftInterval.not_equals(rightInterval)
                else:
                    # <, >, <=, >= 는 bytes에 대해 정의되지 않음 (Solidity에서)
                    result = BoolInterval.top()
            # 두 피연산자가 모두 Interval 계열인지 검사
            elif not (isinstance(leftInterval, (IntegerInterval,
                                              UnsignedIntegerInterval,
                                              BoolInterval))
                    and isinstance(rightInterval, (IntegerInterval,
                                                   UnsignedIntegerInterval,
                                                   BoolInterval))):
                # Interval 아니면 "결과 불확정" 으로 취급
                result = BoolInterval.top()  # [0,1]
            else:
                result = Evaluation.compare_intervals(
                    leftInterval, rightInterval, operator)
        # 논리 연산자 처리
        elif operator in ['&&', '||']:
            # 피연산자가 BoolInterval이 아닌 경우 변환
            if not isinstance(leftInterval, BoolInterval):
                leftInterval = BoolInterval.top()
            if not isinstance(rightInterval, BoolInterval):
                rightInterval = BoolInterval.top()
            result = leftInterval.logical_op(rightInterval, operator)
        else:
            raise ValueError(f"Unsupported operator '{operator}' in expression: {expr}")

        if isinstance(callerObject, ArrayVariable) or isinstance(callerObject, MappingVariable):
            return self.evaluate_binary_operator_of_index(result, callerObject)
        else:
            return result

    def _elementary_type_top(self, type_name: str):
        """
        elementary 타입 이름 문자열(예: "uint256", "address", "bytes32") → 그 타입의
        top 값. `abi.decode`의 타입-리스트 원소처럼 SolType 객체 없이 타입 이름만
        있는 상황 전용 — 실제 SolType이 있는 경우는 Utils.Helper.top_from_soltype을 쓸 것.
        """
        t = (type_name or "").strip()
        if t in ("address", "addresspayable", "address payable"):
            return AddressSet.top()
        if t == "bool":
            return BoolInterval.top()
        if t.startswith("uint"):
            bits = int(t[4:]) if t[4:].isdigit() else 256
            return UnsignedIntegerInterval.top(bits)
        if t.startswith("int"):
            bits = int(t[3:]) if t[3:].isdigit() else 256
            return IntegerInterval.top(bits)
        if t.startswith("bytes") and t[5:].isdigit():
            return BytesSet.top(int(t[5:]))
        if t in ("bytes", "string"):
            return BytesSet.top()
        raise ValueError(f"abi.decode: unsupported elementary type name '{type_name}'")

    def _evaluate_abi_call(self, expr, member_name: str):
        """
        abi.decode / abi.encode / abi.encodePacked / abi.encodeWithSelector /
        abi.encodeWithSignature / abi.encodeCall — 실제 ABI 인코딩·디코딩을
        추적하지 않고 opaque TOP을 반환한다. `.call()`/`.staticcall()`이 이미
        자신의 인자를 평가하지 않고 바로 top을 반환하는 것과 동일한 패턴 — 크래시
        대신 이 계열 전체를 미지원 외부 인터랙션으로 취급한다.
        """
        if member_name == "decode":
            args = expr.arguments or []
            if len(args) != 2:
                raise ValueError(f"abi.decode expects 2 arguments, got {len(args)}")
            type_list_expr = args[1]
            type_exprs = (type_list_expr.elements
                          if getattr(type_list_expr, 'elements', None) is not None
                          else [type_list_expr])
            if not type_exprs:
                raise ValueError("abi.decode: empty type list")

            results = []
            for t in type_exprs:
                type_name = getattr(t, 'identifier', None)
                if type_name is None:
                    raise ValueError(f"abi.decode: unrecognised type-list element {t!r}")
                results.append(self._elementary_type_top(type_name))

            return results[0] if len(results) == 1 else results

        if member_name in ("encode", "encodePacked", "encodeWithSelector",
                            "encodeWithSignature", "encodeCall"):
            return BytesSet.top()

        raise ValueError(f"abi.{member_name} is not a recognised abi function")

    def evaluate_function_call_context(self, expr, variables, callerObject=None, callerContext=None):
        # [TRACE]
        _fn = getattr(expr, 'function', None)
        _fn_ctx = getattr(_fn, 'context', None) if _fn else None
        _fn_mem = getattr(_fn, 'member', None) if _fn else None
        if expr.context == "IdentifierExpContext":
            function_name = expr.identifier
        elif _fn and _fn_ctx == "MemberAccessContext":  # dynamic array에 대한 push, pop
            # ★ @IReturn: interface call이면 registry에서 값 조회 (evaluate 전에 short-circuit)
            base_expr = expr.function.base
            member_name = expr.function.member

            # abi.decode / abi.encode* — 실제 ABI 인코딩을 추적하지 않고 opaque TOP
            # 반환 (.call()/.staticcall()이 이미 인자를 보지 않고 top을 반환하는 것과 동일한 패턴).
            if getattr(base_expr, 'identifier', None) == 'abi':
                return self._evaluate_abi_call(expr, member_name)

            if hasattr(base_expr, 'identifier'):
                contract_var = base_expr.identifier

                # ★ qualified 네임스페이스 struct 생성자: IPool.TokenAmount({...})
                # — IPool이 변수가 아니라 interface *이름 그 자체*이고, TokenAmount가
                # 그 interface 안에 정의된 struct일 때. 아래 Pattern A는 contract_var가
                # interface 타입 변수일 때만 해당되므로(진짜 함수 호출), 이 체크가
                # 먼저 와야 함 — 안 그러면 "IPool"을 변수로 찾으려다가 실패해서
                # 크래시함(web3bugs_35_H_08/35_H_12에서 발견).
                if contract_var in self.an.interface_names:
                    ifc_struct = self._find_interface_struct_def(contract_var, member_name)
                    if ifc_struct is not None:
                        struct_def, struct_defs, enum_defs = ifc_struct
                        new_struct = StructVariable(
                            identifier=f"temp_{member_name}_{id(expr)}",
                            struct_type=f"{contract_var}.{member_name}",
                            scope="memory"
                        )
                        new_struct.initialize_struct(struct_def, struct_defs=struct_defs, enum_defs=enum_defs)

                        named_args = expr.named_arguments if expr.named_arguments else {}
                        if named_args:
                            for field_name, field_expr in named_args.items():
                                if field_name in new_struct.members:
                                    field_value = self.evaluate_expression(field_expr, variables, None, None)
                                    field_var = new_struct.members[field_name]
                                    if isinstance(field_var, Variables):
                                        field_var.value = field_value
                        elif expr.arguments:
                            member_names = list(new_struct.members.keys())
                            for i, arg_expr in enumerate(expr.arguments):
                                if i < len(member_names):
                                    field_value = self.evaluate_expression(arg_expr, variables, None, None)
                                    field_var = new_struct.members[member_names[i]]
                                    if isinstance(field_var, Variables):
                                        field_var.value = field_value

                        return self._mapping_lookup_if_needed(new_struct, callerObject)

                fcfg = self.an.current_target_function_cfg
                interface_name = self._get_interface_name_of_var(contract_var, variables)
                # ★ (2026-10-22) 외부 호출은 **그 타입이 멤버를 선언했을 때만**. 선언하지 않았으면 Solidity 의 멤버 조회는
                #   using-for 로 붙은 라이브러리 함수다(`using SafeERC20 for IERC20; token.safeTransfer(...)`).
                #   예전엔 인터페이스 타입이면 무조건 외부 호출로 보내 "반환 타입 미해결" 로 멈췄다(31_H_01, 52_H_23).
                #   타입 선언 자체를 못 찾으면(None) 기존대로 외부 호출 경로 — 의존성 누락 오류를 그대로 낸다.
                if fcfg and interface_name and self._type_declares_member(interface_name, member_name) is False:
                    interface_name = None
                if fcfg and interface_name:
                    ret = self._resolve_ireturn_pattern_a(
                        fcfg, interface_name, contract_var, member_name, callerObject)
                    if ret is not None:
                        return ret
                    # ★ @IReturn/@Debugging 블록이 없거나 이 호출을 못 잡은 경우 —
                    # Pattern B(explicit cast)가 이미 하는 것과 동일한 TOP-degradation
                    # 폴백을 여기도 적용. 이게 없으면 일반 member-access 평가 경로로
                    # 흘러가서 호출 결과가 아니라 호출 대상 자체(함수 참조)가 반환돼
                    # raw 문자열로 바텀아웃함(web3bugs_113_H_05에서 발견).
                    # 반환 타입 ⊤ (못 찾으면 _lookup_interface_return 이 예외, void 면 None)
                    result = self._lookup_interface_return(interface_name, member_name)
                    return self._mapping_lookup_if_needed(result, callerObject) if result is not None else None

            # ★ @IReturn Pattern B: explicit cast — IERC20(want).balanceOf() 등
            #   (2026-10-25) visitor 는 인터페이스·컨트랙트 캐스트를 `TypeConversion(typeName, expression)` 으로 만든다 — 예전 조건
            #   (`FunctionCallContext`)은 한 번도 참이 아니어서 이 조회가 죽어 있었고, 아래의 "주소를 보지 않는" 대체 조회가 모든
            #   캐스트 호출을 처리했다(`IF(a).f()` 에 준 값이 `IF(b).f()` 에도 들어감). 키 = (인터페이스, 주소 이름, 함수) — 주소가
            #   식별자가 아닌 식이면 입력 키로 쓸 수 없으므로(문법 확장 = future work) 조회하지 않는다(반환 ⊤).
            if (getattr(base_expr, 'context', None) == 'TypeConversion'
                    and getattr(base_expr, 'typeName', None) in self.an.interface_names):
                cast_interface = base_expr.typeName
                fcfg = self.an.current_target_function_cfg
                addr_var = getattr(base_expr.expression, 'identifier', None) \
                    if getattr(base_expr.expression, 'context', None) == 'IdentifierExpContext' else None
                if fcfg and addr_var:
                    ret = self._resolve_ireturn_pattern_b(
                        fcfg, cast_interface, addr_var, member_name, callerObject)
                    if ret is not None:
                        return ret

            # member access를 평가하여 라이브러리 함수인지 확인
            function_result = self.evaluate_expression(expr.function, variables, None, "functionCallContext")
            
            # 라이브러리 함수 호출인 경우
            if (isinstance(function_result, Expression) and
                hasattr(function_result, 'context') and
                function_result.context == 'LibraryFunctionCallContext'):

                # function call 시점에서 인자 개수로 overload resolution
                implicit_arg = function_result._implicit_first_arg
                is_qualified = getattr(function_result, '_qualified_library_call', False)
                n_explicit = len(expr.arguments) if expr.arguments else 0
                n_args = (n_explicit if is_qualified else 1 + n_explicit)  # qualified: implicit arg 없음
                func_name = function_result.function.identifier
                ccfg = function_result._library_contract_cfg
                base_type = function_result._library_base_type

                # qualified call: library CFG에서 직접 함수 검색
                if is_qualified:
                    lib_fcfg = ccfg.get_function_cfg(func_name)
                    # (Phase 4) `Lib.S(args)` = 라이브러리에 정의된 구조체의 생성자 (함수 아님)
                    lib_structs = getattr(ccfg, "structDefs", {}) or {}
                    if lib_fcfg is None and func_name in lib_structs:
                        qname = f"{getattr(ccfg, 'library_name', None) or ''}.{func_name}".lstrip(".")
                        return self._mapping_lookup_if_needed(
                            self._construct_struct(lib_structs[func_name], qname, expr, variables,
                                                   lib_structs, getattr(ccfg, "enumDefs", {}) or {}),
                            callerObject)
                    # overload: 인자 개수로 매칭
                    if lib_fcfg and len(lib_fcfg.parameters) != n_args:
                        if func_name in ccfg.functions:
                            for sig, fc in ccfg.functions[func_name].items():
                                if len(fc.parameters) == n_args:
                                    lib_fcfg = fc
                                    break
                else:
                    # n_args를 전달하여 처음부터 올바른 overload 선택
                    lib_fcfg = ccfg.find_library_function(base_type, func_name, n_args=n_args)

                return self._mapping_lookup_if_needed(
                    self.evaluate_library_function_call_context(
                        expr, variables, implicit_arg, lib_fcfg),
                    callerObject)

            # interface cast 함수 호출인 경우 (IERC20(x).balanceOf())
            if (isinstance(function_result, Expression) and
                getattr(function_result, 'context', '') == 'InterfaceFunctionCallContext'):
                cast_ifc = function_result._cast_interface
                func_name = function_result.function.identifier
                # (2026-10-25) IReturn 값은 위 Pattern B(주소 이름까지 비교)에서만 — 인터페이스·함수 이름만 맞추던 대체 조회 삭제.
                # 반환 타입 ⊤ (못 찾으면 _lookup_interface_return 이 예외, void 면 None)
                result = self._lookup_interface_return(cast_ifc, func_name)
                return self._mapping_lookup_if_needed(result, callerObject) if result is not None else None

            # super 함수 호출인 경우 (super.foo())
            if (isinstance(function_result, Expression) and
                hasattr(function_result, 'context') and
                function_result.context == 'SuperFunctionCallContext'):

                # super 함수 호출로 처리
                return self._mapping_lookup_if_needed(
                    self.evaluate_super_function_call_context(
                        expr, variables, function_result._dispatch_L),
                    callerObject)

            # this 함수 호출인 경우 (this.foo())
            if (isinstance(function_result, Expression) and
                hasattr(function_result, 'context') and
                function_result.context == 'ThisFunctionCallContext'):

                # this 함수 호출로 처리 (일반 함수 호출과 동일)
                return self._mapping_lookup_if_needed(
                    self.evaluate_this_function_call_context(
                        expr, variables, function_result._dispatch_L),
                    callerObject)

            # 일반적인 member access 결과 반환 (dynamic array push/pop 등)
            return self._mapping_lookup_if_needed(function_result, callerObject)
            
        elif expr.function.context == "NewExpContext":
            # new Type[](size) → FunctionCall(NewExp, [size]) 로 파싱된 경우
            new_expr = expr.function
            if not new_expr.arguments:
                new_expr.arguments = expr.arguments
            return self.evaluate_new_expression_context(new_expr, variables, callerObject, callerContext)

        elif expr.function.context == "FunctionCallOptionContext":
            # expr{value: v, gas: g}(args) — (Phase 4b) 옵션은 반환값에 영향이 없으므로 옵션을 벗긴 호출로
            #   평가한다. 예전엔 무엇을 부르든 uint ⊤ 를 돌려 반환 타입이 틀렸다
            #   (`(bool ok, ) = to.call{value: v}("")` 가 튜플 대입에서 터지고, interface 호출의 반환 타입도 무시됨).
            plain = Expression(function=expr.function.function, arguments=expr.arguments,
                               named_arguments=expr.named_arguments, operator=expr.operator,
                               context=expr.context)
            return self.evaluate_function_call_context(plain, variables, callerObject, callerContext)

        elif expr.function.context == "IdentifierExpContext":
            function_name = expr.function.identifier
        else:
            raise ValueError(f"There is no function name in function call context")

        # 1-B) Solidity built-in 함수 처리
        builtin_result = self._evaluate_builtin_function(function_name, expr, variables, callerObject)
        if builtin_result is not None:
            return builtin_result

        # 2) 현재 컨트랙트 CFG 가져오기
        contract_cfg = self.an.contract_cfgs.get(self.an.current_target_contract)
        if not contract_cfg:
            raise ValueError(f"Unable to find contract CFG for {self.an.current_target_contract}")

        # 2-A) 구조체 생성자인지 확인 (현재 contract + parent chain + using libraries)
        found = self._find_struct_def(contract_cfg, function_name)
        if found is not None:
            struct_def, qualified_name = found
            return self._construct_struct(struct_def, qualified_name, expr, variables,
                                          contract_cfg.structDefs, contract_cfg.enumDefs)

        return self._resolve_and_call_internal(expr, variables, callerObject, callerContext,
                                               function_name, contract_cfg)

    def _construct_struct(self, struct_def, qualified_name, expr, variables, struct_defs, enum_defs):
        """구조체 생성자 `S(a, b)` / `S({x: a})` / `Lib.S(a)` → memory StructVariable."""
        new_struct = StructVariable(
            identifier=f"temp_{qualified_name}_{id(expr)}",
            struct_type=qualified_name,
            scope="memory"
        )
        new_struct.initialize_struct(struct_def, struct_defs=struct_defs, enum_defs=enum_defs)

        # named_arguments로 필드 초기화
        named_args = expr.named_arguments if expr.named_arguments else {}
        if named_args:
            for field_name, field_expr in named_args.items():
                if field_name in new_struct.members:
                    field_value = self.evaluate_expression(field_expr, variables, None, None)
                    field_var = new_struct.members[field_name]
                    if isinstance(field_var, Variables):
                        # (Phase 4b) 필드 선언 타입으로 암묵 변환
                        field_var.value = coerce_to_declared(field_value, getattr(field_var, "typeInfo", None),
                                                             f"struct field '{field_var.identifier}'")
        elif expr.arguments:
            # positional arguments: StructName(val1, val2, ...)
            member_names = list(new_struct.members.keys())
            for i, arg_expr in enumerate(expr.arguments):
                if i < len(member_names):
                    field_value = self.evaluate_expression(arg_expr, variables, None, None)
                    field_var = new_struct.members[member_names[i]]
                    if isinstance(field_var, Variables):
                        # (Phase 4b) 필드 선언 타입으로 암묵 변환
                        field_var.value = coerce_to_declared(field_value, getattr(field_var, "typeInfo", None),
                                                             f"struct field '{field_var.identifier}'")

        return new_struct

    def _call_function_pointer(self, fp, expr, variables, callerObject):
        """(2026-10-23) 함수 포인터 호출 — 대상을 추적하지 않으므로(값 = FunctionRef ⊤) **대상을 모르는 호출**로 요약한다.
          · internal 함수 타입: 같은 컨트랙트의 어떤 함수든 될 수 있다 → 상태변수 전체 ⊤, 복합 타입 인자(참조 의미) ⊤
          · external 함수 타입: 외부 호출 — 이 엔진의 외부 호출 모델(반환값만 주고받음)과 같게 상태는 유지
          · 반환값 = 함수 타입에 선언된 반환 타입의 ⊤"""
        ft = fp.typeInfo
        args = [self.evaluate_expression(a, variables, None, None) for a in (expr.arguments or [])]
        args += [self.evaluate_expression(a, variables, None, None) for a in (expr.named_arguments or {}).values()]
        owner_cfg = self._executing_contract_cfg()
        if getattr(ft, "functionVisibility", "internal") == "internal":
            for name, v in list(variables.items()):
                if "." not in name and getattr(v, "scope", None) == "state" and not getattr(v, "isConstant", False):
                    self.engine._havoc(v, owner_cfg)
            for a in args:
                if isinstance(a, (StructVariable, ArrayVariable, MappingVariable)):
                    self.engine._havoc(a, owner_cfg)
        rts = list(getattr(ft, "functionReturnTypes", None) or [])
        tops = [self._top_of_type(t, owner_cfg, f"{fp.identifier}_ret{i}") for i, t in enumerate(rts)]
        if not tops:
            return None
        return self._mapping_lookup_if_needed(tops[0], callerObject) if len(tops) == 1 else tops

    def _resolve_and_call_internal(self, expr, variables, callerObject, callerContext, function_name, contract_cfg):
        """이름 호출 `f(args)` — 정적 스코프로 callee 를 찾아 공통 진입점으로 해석 (Phase 4)."""
        # (2026-10-23) 함수 타입 변수를 통한 호출 — 지역 이름이 함수 이름을 가린다(Solidity 스코프)
        fp = variables.get(function_name)
        if fp is not None and getattr(getattr(fp, "typeInfo", None), "typeCategory", None) == "function":
            return self._call_function_pointer(fp, expr, variables, callerObject)
        # 3) 함수 CFG 가져오기
        # ★ 지금 라이브러리 함수 몸체 안에서 이름만으로 호출하는 중이라면(예:
        # SafeMathUpgradeable의 `sub(a,b) { return sub(a,b,errorMsg); }`처럼
        # 2-인자 오버로드가 같은 라이브러리의 3-인자 오버로드를 이름으로 위임
        # 호출), Solidity 스코프 규칙상 이 이름은 그 라이브러리 자신의
        # 함수(오버로드 포함)만 가리킬 수 있음 — 호출자 컨트랙트의 함수 목록을
        # 찾으면 안 됨. 예전엔 항상 contract_cfg만 검색해서 이런 라이브러리
        # 내부 위임 호출이 "함수를 못 찾음" → TOP으로 조용히 무너졌음
        # (web3bugs_47_H_02, `SafeMathUpgradeable.div(a,b)`가 내부에서
        # `div(a,b,errorMessage)`를 부르는 지점에서 발견 — mul()은 이 위임
        # 패턴이 없어서 우연히 안 걸렸었음).
        owning_lib = None
        cur_fcfg = self.an.current_target_function_cfg
        if cur_fcfg is not None:
            # ★ self.an.library_cfgs는 Dependencies pkl에서 로드된 "참고용"
            # 사본이라, 실제로 `using X for Y`가 붙이는(=`find_library_function`이
            # 반환하는) 라이브러리 함수 객체와 동일 인스턴스가 아님(별개
            # 재빌드본) — 그래서 이 dict로 identity 비교하면 항상 실패함.
            # 실제로 호출되는 라이브러리 함수는 각 컨트랙트의
            # `contract_cfg.using_libraries[target_type] -> [LibraryCFG, ...]`
            # 에 붙어있는 것이므로, 거기서 찾아야 함.
            _cur = getattr(cur_fcfg, "_live_origin", cur_fcfg)
            for _lib_list in getattr(contract_cfg, 'using_libraries', {}).values():
                for _lib_cfg in _lib_list:
                    if _cur in [f for _, f in _lib_cfg.iter_all_functions()]:
                        owning_lib = _lib_cfg
                        break
                if owning_lib is not None:
                    break
            # ★ (Phase 4, D3) `Lib.f(...)` 처럼 이름으로 직접 부른 라이브러리 본문 — using-for 목록에 없으므로
            #   로드된 라이브러리 전체에서 소속을 찾는다(예전엔 못 찾아 형제 함수 호출이 uint ⊤ 로 무너졌다).
            if owning_lib is None:
                for _lib_cfg in (self.an.library_cfgs or {}).values():
                    if _cur in [f for _, f in _lib_cfg.iter_all_functions()]:
                        owning_lib = _lib_cfg
                        break

        if owning_lib is not None:
            functions_source = owning_lib.functions.get(function_name)
            function_cfg = next(iter(functions_source.values())) if functions_source else None
        else:
            # (2026-10-23) 이름 호출 = 가장 파생된 컨트랙트의 C3 선형화 전체에서 오버로드 해소 → 본문 있는 첫 구현으로
            #   디스패치(`_dispatch`). 예전엔 소스 순서 깊이 우선으로 **이름이 처음 나온 곳**을 골라, 본문 없는 interface
            #   선언(파라미터 미기록 → "인자 0개")을 호출 대상으로 잡았다(52_H_23 `getReserves(foreignAsset)`).
            L = self.linearize(self._dispatch_root_cfg())
            function_cfg = (self._dispatch(L, function_name, expr.arguments or [], expr.named_arguments or {},
                                           variables)
                            if self._functions_named(L, function_name) else None)
            functions_source = None

        if not function_cfg:
            # ★ (Phase 4, D3/D4) 예전엔 여기서 `pending_callee_name` 을 남기고 uint256 ⊤ 를 반환했다(반환 타입 무시).
            #   디버깅 해석 시점에는 모든 선언이 들어와 있으므로, 이름이 가리킬 수 있는 것은 셋뿐이다.
            # (1) interface/contract 타입 이름 → 형변환 `X(a)` (파싱 시점에 X 가 아직 선언 전이었던 경우)
            if function_name in (getattr(self.an, "interface_names", set()) or set()) \
                    or function_name in (self.an.contract_cfgs or {}):
                conv = Expression(typeName=function_name,
                                  expression=(expr.arguments[0] if expr.arguments else None),
                                  operator='typecast', context='TypeConversion')
                return self.evaluate_expression(conv, variables, callerObject, callerContext)
            # (2) 그 외 = 존재하지 않는 함수 (컴파일 오류급)
            raise ValueError(f"call to undefined function '{function_name}'")

        # 4) 함수 파라미터와 인자 매핑
        #    expr.arguments -> 위치 기반 인자
        #    expr.named_arguments -> 키워드 인자
        arguments = expr.arguments if expr.arguments else []
        named_arguments = expr.named_arguments if expr.named_arguments else {}

        # 파라미터 목록
        param_names = getattr(function_cfg, 'parameters', [])
        total_params = len(param_names)
        total_args = len(arguments) + len(named_arguments)

        # overload 선택: 인자 개수 + **선언 타입** 으로 결정 (F37)
        # (owning_lib가 있으면 라이브러리 쪽, 없으면 소유 CFG 쪽 — 둘 다
        # functions_source 하나로 통일했으므로 분기 불필요)
        #
        # ★ 예전엔 **인자 개수만** 보고 첫 후보를 집었다. 그래서 개수가 같은
        #   오버로드(`_prepare(bytes32,uint256)` vs `_prepare(bytes32,address)`)는
        #   dict 순서로 결정돼 **오류 없이 엉뚱한 쪽**으로 갔다 — 실측:
        #     `Preparable:47  _prepare(key, value, _MIN_DELAY)`  value: address
        #     `Preparable:83  _setConfig(key, newValue)`          newValue: address
        #     `Fixed18:114    UFixed18Lib._from(a)`               a: Fixed18(=int256)
        #   이 세 곳이 각각 uint256/uint256/uint256 버전으로 해석돼
        #   `pendingAddresses` 대신 `pendingUInts256` 를 건드렸다(조용한 오분석).
        #   전수 조사: pkl 953개 함수 이름 중 오버로드 18개, 그중 **인자 개수로
        #   구별 불가 6개** — 전부 선언 타입으로 유일하게 갈린다.
        if functions_source and len(functions_source) > 1:
            _selected = self._select_overload(
                functions_source, arguments, named_arguments, variables, function_name)
            if _selected is not None:
                function_cfg = _selected
                param_names = getattr(function_cfg, 'parameters', None) or []
                total_params = len(param_names)

        if total_params != total_args:
            raise ValueError(f"Argument count mismatch in function call to '{function_name}': "
                             f"expected {total_params}, got {total_args}.")

        # 5) 인자 평가(caller env) → 6) 공통 진입점으로 해석 (Phase 4: 바인딩·참조 의미·컨텍스트는 invoke_callee)
        arg_values = self._ordered_call_args(arguments, named_arguments, param_names, variables, function_name)
        return_value = self.engine.invoke_callee(function_cfg, arg_values, variables, kind="internal",
                                                 call_expr=expr)
        return self._mapping_lookup_if_needed(return_value, callerObject)

    def _ordered_call_args(self, arguments, named_arguments, param_names, variables, function_name):
        """위치 인자 + 이름 인자 → 파라미터 순서의 값 리스트 (caller env 에서 평가)."""
        values = [self.evaluate_expression(a, variables, None, None) for a in (arguments or [])]
        if named_arguments:
            by_name = {}
            for key, ex in named_arguments.items():
                if key not in param_names:
                    raise ValueError(f"Unknown named parameter '{key}' in function '{function_name}'.")
                by_name[key] = self.evaluate_expression(ex, variables, None, f"CallNamedArg({function_name})")
            for pname in param_names[len(values):]:
                if pname not in by_name:
                    raise ValueError(f"call to '{function_name}': missing argument '{pname}'")
                values.append(by_name[pname])
        return values

    def evaluate_library_function_call_context(self, expr, variables, implicit_first_arg, library_function_cfg):
        """
        라이브러리 함수 호출 (`using L for T` 의 `x.f(...)` 또는 `L.f(...)`).
        implicit_first_arg: using-for 의 수신자(첫 번째 인자) 값.
        ★ (Phase 4) 공통 진입점(`Engine.invoke_callee`)으로 바인딩·해석. 예전엔 callee 파라미터 객체를 제자리에서
          덮어쓰거나(복합 타입은 caller 객체로 교체) 인자 수가 안 맞으면 심볼 문자열을 반환했다.
        """
        if not library_function_cfg:
            raise ValueError(f"library call '{getattr(expr.function, 'identifier', expr)}': function not found")
        param_names = list(getattr(library_function_cfg, 'parameters', []) or [])
        values = [] if implicit_first_arg is None else [implicit_first_arg]
        values += self._ordered_call_args(expr.arguments or [], getattr(expr, "named_arguments", None) or {},
                                          param_names[len(values):], variables,
                                          library_function_cfg.function_name)

        # 라이브러리의 constant 변수들을 callee 공유 env 에 추가 (호출자 env 는 건드리지 않는다)
        caller_env = dict(variables)
        for lib_cfg in self.an.library_cfgs.values():
            if library_function_cfg in [f for _, f in lib_cfg.iter_all_functions()]:
                svn = getattr(lib_cfg, "state_variable_node", None)
                for var_name, var_obj in ((svn.variables if svn else {}) or {}).items():
                    caller_env.setdefault(var_name, var_obj)
                break
        return self.engine.invoke_callee(library_function_cfg, values, caller_env, kind="library", call_expr=expr)

    def evaluate_super_function_call_context(self, expr, variables, dispatch_L):
        """super.f(...) / Base.f(...) — (Phase 4) 공통 진입점으로. 못 찾으면 예외(예전엔 ⊤).
        (2026-10-23) 대상은 호출 시점에 인자로 오버로드를 해소하고 선형화 구간에서 디스패치한다."""
        super_function_cfg = self._dispatch(dispatch_L, expr.function.member if expr.function.context ==
                                            "MemberAccessContext" else expr.function.identifier,
                                            expr.arguments or [], expr.named_arguments or {}, variables)
        param_names = list(getattr(super_function_cfg, 'parameters', []) or [])
        values = self._ordered_call_args(expr.arguments or [], expr.named_arguments or {}, param_names,
                                         variables, super_function_cfg.function_name)
        return self.engine.invoke_callee(super_function_cfg, values, variables, kind="super", call_expr=expr)

    def evaluate_this_function_call_context(self, expr, variables, dispatch_L):
        """this.f(...) — 외부 호출이므로 인자는 복사(참조 의미 없음). (Phase 4) 못 찾으면 예외(예전엔 ⊤/문자열)."""
        this_function_cfg = self._dispatch(dispatch_L, expr.function.member, expr.arguments or [],
                                           expr.named_arguments or {}, variables)
        param_names = list(getattr(this_function_cfg, 'parameters', []) or [])
        values = self._ordered_call_args(expr.arguments or [], expr.named_arguments or {}, param_names,
                                         variables, this_function_cfg.function_name)
        return self.engine.invoke_callee(this_function_cfg, values, variables, kind="this", call_expr=expr)

    def convert_to_uint(self, sub_val, bits):
        """
        sub_val을 uintN 범위 [0 .. 2^bits−1] 로 변환/클램프
        """
        type_max = (1 << bits) - 1  # 2**bits - 1 과 동일

        # ────────────────────────────────────────────────────────
        # 1) Interval 계열 (Unsigned / Integer)
        # ────────────────────────────────────────────────────────
        if isinstance(sub_val, (UnsignedIntegerInterval, IntegerInterval)):
            if sub_val.is_bottom():  # ★ bottom 우선 검사 (도달 불가는 그대로 전파)
                return UnsignedIntegerInterval(None, None, bits)
            lo, hi = self._wrap_integer_range(sub_val.min_value, sub_val.max_value, bits, signed=False)
            return UnsignedIntegerInterval(lo, hi, bits)

        # ────────────────────────────────────────────────────────
        # 2) BoolInterval  (0 또는 1)
        # ────────────────────────────────────────────────────────
        if isinstance(sub_val, BoolInterval):
            return UnsignedIntegerInterval(
                sub_val.min_value, sub_val.max_value, bits
            )  # 이미 0‥1 범위

        # ────────────────────────────────────────────────────────
        # 3) 문자열(리터럴·symbolic) → symbolic 래퍼
        # ────────────────────────────────────────────────────────
        if isinstance(sub_val, str):
            return f"symbolicUint{bits}({sub_val})"

        # ────────────────────────────────────────────────────────
        # 4) 기타(정수 등) → 그대로 Interval 로 래핑
        # ────────────────────────────────────────────────────────
        try:
            v = int(sub_val)
            lo, hi = self._wrap_integer_range(v, v, bits, signed=False)
            return UnsignedIntegerInterval(lo, hi, bits)
        except (ValueError, TypeError):
            return f"symbolicUint{bits}({sub_val})"

    @staticmethod
    def _wrap_integer_range(lo: int, hi: int, bits: int, *, signed: bool) -> tuple[int, int]:
        """명시적 정수 변환(`uint8(x)`, `uint256(int x)`, `int128(y)` …)의 구간 의미. (Phase 2)

        Solidity 의 정수 간 명시적 변환은 **revert 하지 않고** 2의 보수로 잘라/재해석한다
        (v mod 2^bits, signed 면 상위 절반을 음수로). 예전 구현은 범위 밖을 경계로 **clamp** 하고
        (불건전: uint8(300) = 44 를 255 로), 범위와 겹치지 않으면 **⊥**(도달 불가로 오판)를 냈다.
        결과가 연속 구간으로 표현되면 그 구간, 감긴 결과가 두 조각이면 타입 전체(⊤).
        """
        mod = 1 << bits
        off = (1 << (bits - 1)) if signed else 0           # signed: [-2^(b-1), 2^(b-1)) 로 이동
        t_lo, t_hi = -off, mod - 1 - off
        if hi - lo + 1 >= mod:
            return t_lo, t_hi
        w_lo = (lo + off) % mod - off
        w_hi = (hi + off) % mod - off
        if w_lo <= w_hi:
            return w_lo, w_hi
        return t_lo, t_hi

    def convert_to_int(self, sub_val, bits):
        """
        주어진 sub_val(Interval·리터럴·symbolic)을
        signed int<bits> 범위 [-2^(bits-1) .. 2^(bits-1)-1] 로 변환/클램프한다.
        """
        type_min = -(1 << (bits - 1))
        type_max = (1 << (bits - 1)) - 1

        # ────────────────────────────────────────────────────────
        # 1) Interval → Interval
        #    ⊥(bottom) 은 그대로 bottom 반환
        # ────────────────────────────────────────────────────────
        if isinstance(sub_val, (IntegerInterval, UnsignedIntegerInterval)):
            if sub_val.is_bottom():  # ★ bottom 체크 (도달 불가는 그대로 전파)
                return IntegerInterval(None, None, bits)
            lo, hi = self._wrap_integer_range(sub_val.min_value, sub_val.max_value, bits, signed=True)
            return IntegerInterval(lo, hi, bits)

        # ────────────────────────────────────────────────────────
        # 2) BoolInterval → 0/1 로 압축 후 위와 동일
        # ────────────────────────────────────────────────────────
        if isinstance(sub_val, BoolInterval):
            # 0‥1 과 int<bits> 의 교집합은 그대로 0‥1
            return IntegerInterval(
                max(type_min, sub_val.min_value),
                min(type_max, sub_val.max_value),
                bits
            )

        # ────────────────────────────────────────────────────────
        # 3) 문자열(리터럴·심볼릭)  → 그대로 symbolic 래퍼
        # ────────────────────────────────────────────────────────
        if isinstance(sub_val, str):
            return f"symbolicInt{bits}({sub_val})"

        # ────────────────────────────────────────────────────────
        # 4) 기타(정수 등) → 그대로 Interval 로 래핑
        # ────────────────────────────────────────────────────────
        try:
            v = int(sub_val)
            lo, hi = self._wrap_integer_range(v, v, bits, signed=True)
            return IntegerInterval(lo, hi, bits)
        except (ValueError, TypeError):
            return f"symbolicInt{bits}({sub_val})"

    def evaluate_type_wrap_unwrap(self, expr, variables, callerObject=None, callerContext=None):
        """
        user-defined value type의 wrap/unwrap 처리.
        Fixed18.wrap(100) → int256 interval로 변환
        Fixed18.unwrap(a) → underlying type interval로 변환
        """
        type_name = expr.identifier       # "Fixed18", "UFixed18"
        method = expr.member               # "wrap" or "unwrap"
        args = expr.arguments or []

        if not args:
            return UnsignedIntegerInterval.top()

        # 인자 evaluate
        arg_val = self.evaluate_expression(args[0], variables, None, None)

        # underlying type 조회
        underlying = self.an.sa.resolve_type(type_name)
        if underlying is None:
            return arg_val  # fallback

        # underlying type에 맞는 interval로 변환
        if underlying.startswith("int"):
            bits = int(underlying[3:]) if len(underlying) > 3 else 256
            if VariableEnv.is_interval(arg_val):
                return IntegerInterval(arg_val.min_value, arg_val.max_value, bits)
            elif isinstance(arg_val, (int, float)):
                return IntegerInterval(int(arg_val), int(arg_val), bits)
            return IntegerInterval.top(bits)
        elif underlying.startswith("uint"):
            bits = int(underlying[4:]) if len(underlying) > 4 else 256
            if VariableEnv.is_interval(arg_val):
                return UnsignedIntegerInterval(arg_val.min_value, arg_val.max_value, bits)
            elif isinstance(arg_val, (int, float)):
                return UnsignedIntegerInterval(int(arg_val), int(arg_val), bits)
            return UnsignedIntegerInterval.top(bits)
        elif underlying == "bool":
            if isinstance(arg_val, BoolInterval):
                return arg_val
            return BoolInterval.top()

        return arg_val  # 기타 타입은 그대로

    def _resolve_alias_from_expr(self, base_expr, variables) -> str | None:
        """expr.base에서 원래 타입의 aliasName을 복원.
        1) Identifier → variables에서 조회
        2) MemberAccess on struct → struct 멤버의 typeInfo 조회
        3) 그 외 → None + error 출력"""
        if base_expr is None:
            return None

        # 1) Identifier: someVar.add(...)
        if getattr(base_expr, 'context', None) == 'IdentifierExpContext':
            ident = base_expr.identifier
            var = variables.get(ident)
            if var and hasattr(var, 'typeInfo') and var.typeInfo:
                return getattr(var.typeInfo, 'aliasName', None)
            return None

        # 2) MemberAccess: self.shortfall.add(...)
        if getattr(base_expr, 'context', None) == 'MemberAccessContext':
            member_name = base_expr.member
            # global keyword (block, msg, tx) base는 alias 없음
            base_ident = getattr(base_expr.base, 'identifier', None)
            if base_ident in ('block', 'msg', 'tx'):
                return None
            try:
                base_val = self.evaluate_expression(base_expr.base, variables, None, None)
            except (ValueError, KeyError):
                return None
            if isinstance(base_val, StructVariable) and member_name in base_val.members:
                member_var = base_val.members[member_name]
                if hasattr(member_var, 'typeInfo') and member_var.typeInfo:
                    return getattr(member_var.typeInfo, 'aliasName', None)
            return None

        # 3) TupleExpression: (expr).method() → 괄호 벗기고 재귀
        if getattr(base_expr, 'context', None) == 'TupleExpressionContext':
            inner = getattr(base_expr, 'elements', None)
            if inner and len(inner) == 1:
                return self._resolve_alias_from_expr(inner[0], variables)

        # 4) IndexAccess: balances[_to].add(...) → 평가해서 Variables typeInfo 확인
        if getattr(base_expr, 'context', None) == 'IndexAccessContext':
            try:
                val = self.evaluate_expression(base_expr, variables, None, None)
                if isinstance(val, Variables) and hasattr(val, 'typeInfo') and val.typeInfo:
                    return getattr(val.typeInfo, 'aliasName', None)
            except (ValueError, KeyError, AttributeError):
                pass
            return None

        # 5) FunctionCall: value.div(100000).mul(...) → 체이닝, base object 재귀 추적
        if getattr(base_expr, 'context', None) == 'FunctionCallContext':
            func_expr = getattr(base_expr, 'function', None) or getattr(base_expr, 'base', None)
            if func_expr and getattr(func_expr, 'context', None) == 'MemberAccessContext':
                result = self._resolve_alias_from_expr(func_expr.base, variables)
                if result:
                    return result
                # base가 library 이름인 경우: using directive에서 역추적
                lib_ident = getattr(func_expr.base, 'identifier', None)
                if lib_ident and lib_ident in self.an.library_cfgs:
                    contract_name = self.an.current_target_contract
                    ccfg = self.an.contract_cfgs.get(contract_name)
                    if ccfg:
                        for type_name, libs in ccfg.using_libraries.items():
                            for lib in libs:
                                lib_name = getattr(lib, 'library_name', None) or getattr(lib, 'contract_name', None)
                                if lib_name == lib_ident:
                                    return type_name
            return None

        # 6) 그 외: alias 없음
        return None

    def _find_struct_def(self, contract_cfg, struct_name):
        """struct 정의를 현재 contract, parent chain, using libraries에서 검색.
        Returns: (struct_def, qualified_name) 또는 None"""
        # 1) 현재 contract
        if struct_name in contract_cfg.structDefs:
            return contract_cfg.structDefs[struct_name], struct_name
        # 2) parent chain
        for parent_cfg in getattr(contract_cfg, 'parent_cfgs', {}).values():
            if struct_name in parent_cfg.structDefs:
                return parent_cfg.structDefs[struct_name], struct_name
        # 3) using libraries
        all_libs = []
        for libs in getattr(contract_cfg, 'using_libraries', {}).values():
            all_libs.extend(libs if isinstance(libs, list) else [libs])
        all_libs.extend(getattr(contract_cfg, 'using_all_libraries', []))
        for lib in all_libs:
            if struct_name in lib.structDefs:
                qualified = f"{lib.library_name}.{struct_name}"
                return lib.structDefs[struct_name], qualified
        # 4) library_cfgs 직접 검색
        for lib_name, lib_cfg in self.an.library_cfgs.items():
            if struct_name in lib_cfg.structDefs:
                qualified = f"{lib_name}.{struct_name}"
                return lib_cfg.structDefs[struct_name], qualified
        return None

    def _evaluate_builtin_function(self, function_name, expr, variables, callerObject):
        """Solidity built-in 함수 처리. 해당하면 결과 반환, 아니면 None."""
        args = expr.arguments or []

        # addmod(a, b, n) → (a + b) % n
        if function_name == "addmod" and len(args) == 3:
            a = self.evaluate_expression(args[0], variables, None, None)
            b = self.evaluate_expression(args[1], variables, None, None)
            n = self.evaluate_expression(args[2], variables, None, None)
            if VariableEnv.is_interval(a) and VariableEnv.is_interval(b) and VariableEnv.is_interval(n):
                return self._mapping_lookup_if_needed(a.add(b).modulo(n), callerObject)
            return self._mapping_lookup_if_needed(UnsignedIntegerInterval.top(), callerObject)

        # mulmod(a, b, n) → (a * b) % n
        if function_name == "mulmod" and len(args) == 3:
            a = self.evaluate_expression(args[0], variables, None, None)
            b = self.evaluate_expression(args[1], variables, None, None)
            n = self.evaluate_expression(args[2], variables, None, None)
            if VariableEnv.is_interval(a) and VariableEnv.is_interval(b) and VariableEnv.is_interval(n):
                return self._mapping_lookup_if_needed(a.multiply(b).modulo(n), callerObject)
            return self._mapping_lookup_if_needed(UnsignedIntegerInterval.top(), callerObject)

        # keccak256, sha256, ripemd160, blockhash → TOP (bytes32)
        if function_name in ("keccak256", "sha256", "ripemd160", "blockhash"):
            return self._mapping_lookup_if_needed(UnsignedIntegerInterval.top(), callerObject)

        # ecrecover → TOP (address)
        if function_name == "ecrecover":
            return self._mapping_lookup_if_needed(AddressSet.top(), callerObject)

        # gasleft → TOP (uint256)
        if function_name == "gasleft":
            return self._mapping_lookup_if_needed(UnsignedIntegerInterval.top(), callerObject)

        return None  # built-in이 아님

    def _get_address_this_balance(self, variables):
        """GlobalVar에서 address(this).balance 값을 조회. 없으면 None."""
        gv_name = "address(this).balance"
        # 1) variables(함수 env)에 있는지
        if gv_name in variables:
            v = variables[gv_name]
            return v.value if hasattr(v, "value") else v
        # 2) contract globals에 있는지
        contract_name = self.an.current_target_contract
        if contract_name and contract_name in self.an.contract_cfgs:
            cfg = self.an.contract_cfgs[contract_name]
            if gv_name in cfg.globals:
                g = cfg.globals[gv_name]
                return g.value
        return None

    def convert_to_bool(self, sub_val):
        """
        int/uint interval -> 0 => false, !=0 => true => [0,1] 형태
        """
        if isinstance(sub_val, IntegerInterval) or isinstance(sub_val, UnsignedIntegerInterval):
            if sub_val.is_bottom():
                return BoolInterval(None, None)
            # if entire range is strictly 0..0 => false
            if sub_val.min_value == 0 and sub_val.max_value == 0:
                return BoolInterval(0, 0)
            # if entire range is non-zero => true => [1,1]
            if sub_val.min_value > 0:
                return BoolInterval(1, 1)
            # if partial includes 0 and nonzero => [0,1]
            return BoolInterval(0, 1)

        elif isinstance(sub_val, BoolInterval):
            # 이미 bool => 그대로 반환 가능
            return sub_val

        elif isinstance(sub_val, str):
            # string => symbolic bool
            return BoolInterval(0, 1)

        # fallback
        return BoolInterval(0, 1)

    def evaluate_binary_operator_of_index(self, result, callerObject):
        def array_base_is_address(arr: ArrayVariable) -> bool:
            et = arr.typeInfo.arrayBaseType
            if isinstance(et, SolType):
                return et.elementaryTypeName == "address"
            return et == "address"

        if isinstance(callerObject, ArrayVariable):
            # 숫자/인터벌이 아니면 그대로 symbolic (fallback)
            if not hasattr(result, "min_value"):
                # 가상 생성 시도
                return self._join_array_elements_virtually(callerObject, (0, 0))

            # bottom → 빈 구조체 또는 TOP interval
            if result.is_bottom():
                base_t = callerObject.typeInfo.arrayBaseType
                if isinstance(base_t, SolType) and base_t.typeCategory == "struct":
                    empty_struct = StructVariable(
                        f"{callerObject.identifier}[bottom]",
                        base_t.structTypeName,
                        scope=callerObject.scope
                    )
                    ccf = self.an.contract_cfgs[self.an.current_target_contract]
                    if base_t.structTypeName in ccf.structDefs:
                        empty_struct.initialize_struct(ccf.structDefs[base_t.structTypeName],
                                                       struct_defs=ccf.structDefs, enum_defs=ccf.enumDefs)
                    return empty_struct
                elif array_base_is_address(callerObject):
                    return AddressSet.top()
                else:
                    return f"symbolicIndex({callerObject.identifier}[BOTTOM])"

            l, r = result.min_value, result.max_value

            # ─── (A) 단일 인덱스 ───────────────────────────────
            if l == r:
                try:
                    elem = callerObject.get_or_create_element(l)
                except IndexError:
                    # 범위 밖: 가상 생성
                    elem = callerObject._create_element_virtual(l)
                if isinstance(elem, (StructVariable, ArrayVariable, MappingVariable)):
                    return elem
                return elem.value if hasattr(elem, "value") else elem

            # ─── (B) 범위 [l..r]  → 가상 생성 + join  ─────────────────────
            return self._join_array_elements_virtually(callerObject, (l, r))

        # 3) callerObject가 MappingVariable인 경우
        if isinstance(callerObject, MappingVariable):
            if not callerObject.struct_defs or not callerObject.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

            # result => 단일 키 or 범위 => map lookup
            if not hasattr(result, 'min_value') or not hasattr(result, 'max_value'):
                # 가상 키로 value 생성
                sample_keys = [0, 1, 2, 3, 4]
                return self._join_mapping_values_virtually(callerObject, sample_keys)

            if result.is_bottom():
                # bottom: 빈 value 생성
                return callerObject._create_value_virtual("bottom")

            min_idx = result.min_value
            max_idx = result.max_value

            # 단일 키
            if min_idx == max_idx:
                key_str = str(min_idx)
                if key_str in callerObject.mapping:
                    val_obj = callerObject.mapping[key_str]
                else:
                    val_obj = callerObject.get_or_create(key_str)

                # 복합 타입이면 객체 반환, 기본 타입이면 value 반환
                if isinstance(val_obj, (StructVariable, ArrayVariable, MappingVariable)):
                    return val_obj
                return val_obj.value if hasattr(val_obj, "value") else val_obj

            # 범위 키: 가상 생성 + join
            else:
                span = max_idx - min_idx
                if span > 20:
                    # 샘플링
                    sample_keys = [min_idx + i * span // 20 for i in range(21)]
                else:
                    sample_keys = list(range(min_idx, max_idx + 1))

                return self._join_mapping_values_virtually(callerObject, sample_keys)
        return

    def evaluate_payable_function_call_context(self, expr, variables, callerObject=None, callerContext=None):
        """
        payable(addr) 함수 호출 처리
        - address를 payable address로 변환
        - 추상 해석에서는 address와 동일하게 AddressSet 반환
        """
        # 인자가 있으면 평가
        if expr.arguments:
            sub_val = self.evaluate_expression(expr.arguments[0], variables, None, None)

            # 이미 AddressSet이면 그대로 반환
            if isinstance(sub_val, AddressSet):
                return sub_val

            # uint/int → address 변환
            if isinstance(sub_val, (UnsignedIntegerInterval, IntegerInterval)):
                if sub_val.is_bottom():
                    return AddressSet.bot()
                if sub_val.min_value == sub_val.max_value:
                    return AddressSet(ids={sub_val.min_value})
                return AddressSet.top()

            # 0x로 시작하는 문자열 → address
            if isinstance(sub_val, str) and sub_val.startswith("0x"):
                addr_int = int(sub_val, 16)
                return AddressSet(ids={addr_int})

            # "this" → 현재 컨트랙트 주소
            if isinstance(sub_val, str) and sub_val == "this":
                return self.an.addr_mgr.make_symbolic_address(1, "this")

        # 기타 경우 → Top
        return AddressSet.top()

    def evaluate_function_call_option_context(self, expr, variables, callerObject=None, callerContext=None):
        """
        FunctionCallOptions: expr.function { option1: val1, option2: val2, ... }

        두 가지 경우:
        1) 구조체 생성자: StructName({ field1: val1, field2: val2 })
        2) 함수 호출 옵션: contract.func{value: 1 ether, gas: 5000}(args)
        """
        # Check if it's a MemberAccess with call/delegatecall/staticcall
        if expr.function and hasattr(expr.function, 'context') and expr.function.context == "MemberAccessContext":
            member = getattr(expr.function, 'member', None)
            if member in ['call', 'delegatecall', 'staticcall']:
                # This is the case: address.call{value: ...}
                # 외부 호출은 결과를 알 수 없으므로 Top 반환
                return UnsignedIntegerInterval.top()

        # expr.function이 구조체 타입 이름인지 확인
        if expr.function and expr.function.context == "IdentifierExpContext":
            struct_name = expr.function.identifier

            # 현재 컨트랙트 CFG에서 구조체 정의 가져오기
            ccf = self.an.contract_cfgs[self.an.current_target_contract]

            # 구조체인지 확인
            if struct_name in ccf.structDefs:
                # 구조체 생성자: 새 StructVariable 생성
                struct_def = ccf.structDefs[struct_name]

                new_struct = StructVariable(
                    identifier=f"temp_{struct_name}_{id(expr)}",
                    struct_type=struct_name,
                    scope="memory"
                )
                new_struct.typeInfo = SolType(typeCategory="struct", structTypeName=struct_name)

                # 구조체 초기화
                new_struct.initialize_struct(struct_def, struct_defs=ccf.structDefs, enum_defs=ccf.enumDefs)

                # options에서 필드 값 설정
                if expr.options:
                    for field_name, field_expr in expr.options.items():
                        if field_name in new_struct.members:
                            field_value = self.evaluate_expression(field_expr, variables, None, None)
                            field_var = new_struct.members[field_name]
                            if isinstance(field_var, Variables):
                                field_var.value = field_value
                            # 중첩 구조체/배열의 경우 추가 처리 필요할 수 있음

                return new_struct

        # 함수 호출 옵션 (예: {value: 1 ether, gas: 5000})
        # 결과를 알 수 없으므로 Top 반환
        return UnsignedIntegerInterval.top()

    @staticmethod
    def calculate_default_interval(var_type):
        # 1. int 타입 처리 - 상태변수 기본값은 0
        if var_type.startswith("int"):
            length = int(var_type[3:]) if var_type != "int" else 256  # int 타입의 길이 (기본값은 256)
            return IntegerInterval(0, 0, length)  # int의 기본값 0

        # 2. uint 타입 처리 - 상태변수 기본값은 0
        elif var_type.startswith("uint"):
            length = int(var_type[4:]) if var_type != "uint" else 256  # uint 타입의 길이 (기본값은 256)
            return UnsignedIntegerInterval(0, 0, length)  # uint의 기본값 0

        # 3. bool 타입 처리 - 상태변수 기본값은 false (0)
        elif var_type == "bool":
            return BoolInterval(0, 0)  # bool의 기본값 false (0)

        # 4. address 타입 처리 - 기본값은 address(0)
        elif var_type == "address":
            return AddressSet(ids={0})  # address의 기본값 0 (singleton set)

        # 5. bytes32, bytes16 등 고정 크기 바이트 배열 - 기본값은 bytes32(0)
        elif var_type.startswith("bytes") and len(var_type) > 5:
            byte_size = int(var_type[5:])  # "bytes32" -> 32
            return BytesSet(values={0}, byte_size=byte_size)  # bytes32의 기본값 0

        # 6. 기타 처리 (필요시 확장 가능)
        else:
            raise ValueError(f"Unsupported type for default interval: {var_type}")

    @staticmethod
    def compare_intervals(left_interval, right_interval, operator):

        # 값이 하나라도 BOTTOM이면 비교 결과도 BOTTOM (unreachable)
        if (left_interval.min_value is None or left_interval.max_value is None or
                right_interval.min_value is None or right_interval.max_value is None):
            return BoolInterval.bottom()  # unreachable

        definitely_true = False
        definitely_false = False

        # ───────── 비교 연산별 판정 ────────────────────────────────
        if operator == '==':
            if left_interval.max_value < right_interval.min_value or \
                    left_interval.min_value > right_interval.max_value:
                definitely_false = True
            elif (left_interval.min_value == left_interval.max_value ==
                  right_interval.min_value == right_interval.max_value):
                definitely_true = True

        elif operator == '!=':
            if left_interval.max_value < right_interval.min_value or \
                    left_interval.min_value > right_interval.max_value:
                definitely_true = True
            elif (left_interval.min_value == left_interval.max_value ==
                  right_interval.min_value == right_interval.max_value):
                definitely_false = True

        elif operator == '<':
            if left_interval.max_value < right_interval.min_value:
                definitely_true = True
            elif left_interval.min_value >= right_interval.max_value:
                definitely_false = True

        elif operator == '>':
            if left_interval.min_value > right_interval.max_value:
                definitely_true = True
            elif left_interval.max_value <= right_interval.min_value:
                definitely_false = True

        elif operator == '<=':
            if left_interval.max_value <= right_interval.min_value:
                definitely_true = True
            elif left_interval.min_value > right_interval.max_value:
                definitely_false = True

        elif operator == '>=':
            if left_interval.min_value >= right_interval.max_value:
                definitely_true = True
            elif left_interval.max_value < right_interval.min_value:
                definitely_false = True

        else:
            raise ValueError(f"Unsupported comparison operator: {operator}")

        # ───────── BoolInterval 생성 ─────────────────────────────
        if definitely_true and not definitely_false:
            return BoolInterval(1, 1)  # [1,1]  확실히 true
        if definitely_false and not definitely_true:
            return BoolInterval(0, 0)  # [0,0]  확실히 false
        return BoolInterval(0, 1)  # [0,1]  불확정(top)