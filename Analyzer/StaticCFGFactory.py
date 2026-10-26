from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:                                         # 타입 검사 전용
     from Analyzer.ContractAnalyzer import ContractAnalyzer

from Utils.CFG import ContractCFG, FunctionCFG
from Utils.Helper import VariableEnv
from Utils.Errors import UnsupportedShadowing
from Domain.Interval import UnsignedIntegerInterval, IntegerInterval, BoolInterval
from Domain.Variable import GlobalVariable, Variables, ArrayVariable, StructVariable, EnumVariable, MappingVariable
from Domain.AddressSet import AddressSet
from Domain.FunctionRef import FunctionRef
from Domain.Type import SolType

class StaticCFGFactory:

    @staticmethod
    def _create_global_variables(an: ContractAnalyzer) -> dict[str, GlobalVariable]:
        """
        글로벌 변수 테이블 생성 (block, msg, tx 등)
        ContractCFG와 AbstractContractCFG 모두에서 사용
        """
        def _u256() -> UnsignedIntegerInterval:
            """미지정 환경값 = uint256 전체 범위(⊤).
            ★ (2026-10-20, 사용자 승인) 예전엔 [0,0] 이었다. block.timestamp 등은 Solidity 의 "기본값" 이 아니라
              호출 시점의 환경이므로, @GlobalVar 로 지정하지 않으면 **알 수 없는 값**이다 — IntentChecker 원고
              (debug 어노테이션은 "otherwise unknown" 값을 대체한다, 제약이 없으면 ⊤ 로 남는다)와 일치시킨다.
              msg.value 는 non-payable 함수에서 EVM 이 0 을 강제하므로 ContractAnalyzer 가 함수별로 [0,0] 으로 좁힌다."""
            return UnsignedIntegerInterval.top(256)

        def _addr_fixed(nid: int) -> AddressSet:
            """symbolicAddress nid → AddressSet({nid})"""
            return an.addr_mgr.make_symbolic_address(nid)

        def _sol_elem(name: str, bits: int | None = None) -> SolType:
            T = SolType()
            T.typeCategory = "elementary"
            T.elementaryTypeName = name
            if bits is not None:
                T.intTypeLength = bits
            return T

        return {
            # --- block ---
            "block.basefee": GlobalVariable(
                identifier="block.basefee",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "block.blobbasefee": GlobalVariable(
                identifier="block.blobbasefee",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "block.chainid": GlobalVariable(
                identifier="block.chainid",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "block.coinbase": GlobalVariable(
                identifier="block.coinbase",
                value=_addr_fixed(0),
                typeInfo=_sol_elem("address")),
            "block.difficulty": GlobalVariable(
                identifier="block.difficulty",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "block.gaslimit": GlobalVariable(
                identifier="block.gaslimit",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "block.number": GlobalVariable(
                identifier="block.number",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "block.prevrandao": GlobalVariable(
                identifier="block.prevrandao",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "block.timestamp": GlobalVariable(
                identifier="block.timestamp",
                value=_u256(),
                typeInfo=_sol_elem("uint")),

            # --- msg ---
            "msg.sender": GlobalVariable(
                identifier="msg.sender",
                value=_addr_fixed(101),
                typeInfo=_sol_elem("address")),
            "msg.value": GlobalVariable(
                identifier="msg.value",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "msg.data": GlobalVariable(
                identifier="msg.data",
                value="symbol_msg.data",
                typeInfo=_sol_elem("bytes")),
            "msg.sig": GlobalVariable(
                identifier="msg.sig",
                value="symbol_msg.sig",
                typeInfo=_sol_elem("bytes4")),

            # --- tx ---
            "tx.gasprice": GlobalVariable(
                identifier="tx.gasprice",
                value=_u256(),
                typeInfo=_sol_elem("uint")),
            "tx.origin": GlobalVariable(
                identifier="tx.origin",
                value=_addr_fixed(100),
                typeInfo=_sol_elem("address")),
        }

    @staticmethod
    def make_contract_cfg(an:ContractAnalyzer, contract_name: str) -> ContractCFG:
        # ★ 재사용은 **ContractCFG 일 때만**.
        #   `make_library_cfg` 가 LibraryCFG 를 `contract_cfgs` 에도 등록하고
        #   (호환성), `Dependencies/main.analyze_file` 이 이전에 분석한 라이브러리를
        #   `ca.contract_cfgs.update(_global_library_cfgs)` 로 주입한다.
        #   그래서 **다른 프로젝트의 동명 라이브러리**가 먼저 들어와 있으면
        #   contract 의 CFG 를 만들 때 그 LibraryCFG 를 그대로 돌려줬고,
        #   contract 의 함수들이 LibraryCFG 안에 쌓여 pkl 로 저장됐다.
        #   실측: `contracts/112/Roles.sol`(library) 가 먼저 분석되어
        #   `contracts/Roles.sol`(contract) 의 `con_Roles.pkl` 이
        #   **LibraryCFG 인데 내용은 contract 함수**인 상태가 됐고,
        #   그 결과 `RoleAware.sol` 의 `Roles public immutable roles;` 가
        #   타입 해석에서 계속 거부됐다(5건 연쇄).
        #   ContractCFG 와 LibraryCFG 는 상속 관계가 없는 형제 클래스다.
        _existing = an.contract_cfgs.get(contract_name)
        if isinstance(_existing, ContractCFG):
            return _existing

        cfg = ContractCFG(contract_name)

        # 글로벌 변수 설정
        cfg.globals = StaticCFGFactory._create_global_variables(an)

        for gv in cfg.globals.values():
            an.register_var(gv)

        an.contract_cfgs[contract_name] = cfg
        return cfg

    @staticmethod
    def make_modifier_cfg(an, contract_cfg, modifier_name: str,
                          parameters: dict[str, SolType] | None = None) -> FunctionCFG:
        """
        • modifier 정의부를 한 번만 호출
        • FunctionCFG 와 기본 abstract env 를 만들어 contract_cfg.functions 에 등록
        """
        if modifier_name in contract_cfg.functions:
            overloads = contract_cfg.functions[modifier_name]
            if isinstance(overloads, dict):
                return next(iter(overloads.values()))
            return overloads  # 중복 선언 방지

        mod_cfg = FunctionCFG(function_type="modifier",
                              function_name=modifier_name)

        # ────────── 1. 파라미터 변수 생성 ──────────
        # 2) 파라미터 처리 (없으면 {} 로 대체)
        parameters = parameters or {}
        for var_name, type_info in parameters.items():
            # 파라미터용 Variables 객체 한 개 생성
            var_obj = Variables(identifier=var_name, scope="local")
            var_obj.typeInfo = type_info
            mod_cfg.parameters.append(var_name)      # (Phase 4) 인자 바인딩 순서

            # elementary 타입이면 보수적 default 값 부여
            if type_info.typeCategory == "elementary":
                et = type_info.elementaryTypeName
                if et.startswith(("int", "uint", "bool")):
                    var_obj.value = an.evaluator.calculate_default_interval(et)
                elif et == "address":
                    # 파라미터 address → 전체 범위
                    var_obj.value = UnsignedIntegerInterval(0, 2 ** 160 - 1, 160)
                else:  # bytes / string 등
                    var_obj.value = f"symbol_{var_name}"

            mod_cfg.add_related_variable(var_obj)

        # 상태·글로벌 변수 복사
        ccf = an.contract_cfgs[an.current_target_contract]

        # (1) state / constant variables  ─ 현재 contract + parent 체인 순회
        def _inject_state_vars(cfg_obj):
            sv_node = getattr(cfg_obj, "state_variable_node", None)
            if sv_node and getattr(sv_node, "variables", None):
                for v in sv_node.variables.values():
                    mod_cfg.add_related_variable(v)
            for parent_cfg in getattr(cfg_obj, "parent_cfgs", {}).values():
                _inject_state_vars(parent_cfg)
        _inject_state_vars(ccf)

        # (2) 글로벌 변수 (block.timestamp 등) ─ ContractCFG 만 가짐
        if getattr(ccf, "globals", None):
            for gv in ccf.globals.values():
                mod_cfg.add_related_variable(gv)

        # ───────────────────────────────────────────────────────────────
        # ❷  entry-env 스냅 + entry_node.variables 초기화
        # ───────────────────────────────────────────────────────────────
        # ────────── 3. 저장 & snapshot 등록 ──────────
        contract_cfg.add_function_cfg(modifier_name, mod_cfg)
        an.snapman.register(mod_cfg, an.ser)

        entry_vars = VariableEnv.copy_variables(mod_cfg.related_variables)
        mod_cfg.entry_env = entry_vars  # ★ 함수 진입 스냅샷
        mod_cfg.assign_env.update(entry_vars)
        mod_cfg.entry_node.variables.update(entry_vars)  # ★ entry_node에도 복사
        return mod_cfg

    @staticmethod
    def make_constructor_cfg(an: ContractAnalyzer,
                             name: str,
                             params: list[tuple[SolType, str]],
                             modifiers: list[str]) -> FunctionCFG:
        cfg = FunctionCFG(function_type="constructor", function_name=name)
        # (Phase 4) modifier·기반 생성자 호출 기록 (생성자 의미론은 Phase 7 — 합성은 modifier 만)
        cfg.modifier_invocations = list(modifiers or [])

        # 파라미터->Variables
        for typ, pname in params:
            if pname:
                var = StaticCFGFactory.make_param_variable(
                    an,  # 🔑 ContractAnalyzer 인스턴스
                    typ,  # SolType
                    pname,  # 식별자
                    scope="local"
                )
                cfg.add_related_variable(var)
                cfg.parameters.append(pname)

        # 상태·글로벌 변수 복사
        ccf = an.contract_cfgs[an.current_target_contract]

        # (1) state / constant variables  ─ 존재할 때만 주입
        sv_node = getattr(ccf, "state_variable_node", None)
        if sv_node and getattr(sv_node, "variables", None):
            for v in sv_node.variables.values():
                cfg.add_related_variable(v)

        # (2) 글로벌 변수 (block.timestamp 등) ─ ContractCFG 만 가짐
        if getattr(ccf, "globals", None):
            for gv in ccf.globals.values():
                cfg.add_related_variable(gv)

        # ───────────────────────────────────────────────────────────────
        # ❷  entry-env 스냅 + entry_node.variables 초기화
        # ───────────────────────────────────────────────────────────────
        entry_vars = VariableEnv.copy_variables(cfg.related_variables)
        cfg.entry_env = entry_vars  # ★ 함수 진입 스냅샷
        cfg.assign_env.update(entry_vars)
        cfg.entry_node.variables.update(entry_vars)  # ★ entry_node에도 복사
        return cfg

    @staticmethod
    def param_type_str(p_type) -> str:
        """파라미터 타입 → 시그니처 문자열 (오버로드 구분용)."""
        return (getattr(p_type, 'elementaryTypeName', None) or
                getattr(p_type, 'structTypeName', None) or
                getattr(p_type, 'enumTypeName', None) or
                getattr(p_type, 'interfaceName', None) or
                str(p_type.typeCategory or "unknown"))

    @staticmethod
    def make_function_cfg(an: ContractAnalyzer,
                          name: str,
                          params,
                          modifiers,
                          returns) -> FunctionCFG:

        fcfg = FunctionCFG(function_type="function", function_name=name)

        for p_type, p_name in params:
            # parameter_types에 타입 기록 (overloading signature용)
            fcfg.parameter_types.append(StaticCFGFactory.param_type_str(p_type))

            if p_name:  # 이름이 있는 것만 변수화
                var = StaticCFGFactory.make_param_variable(
                    an,  # 🔑
                    p_type,
                    p_name,
                    scope="local"
                )
                fcfg.add_related_variable(var)
                fcfg.parameters.append(p_name)

        # (Phase 4, D6) modifier 는 구성 시점에 splice 하지 않는다 — (이름, 인자 식) 만 기록하고,
        #   디버깅 해석 때 실행 사본 위에서 합성한다(`Engine._compose_modifiers`). 정의가 뒤에 와도 된다.
        fcfg.modifier_invocations = list(modifiers or [])

        for r_type, r_name in returns:
            if r_name:
                rv = StaticCFGFactory.make_param_variable(
                    an,  # 🔑
                    r_type,
                    r_name,
                    scope="local",
                    is_return_param=True  # ★ Solidity 규약: 0으로 초기화
                )
                fcfg.add_related_variable(rv)
                fcfg.return_vars.append(rv)
            else:
                fcfg.return_types.append(r_type)

        # ───────────────────────────────────────────────────────────────
        # ❶  상태 변수 / 전역 변수 주입 – 라이브러리면 건너뛴다
        # ───────────────────────────────────────────────────────────────
        ccf = an.contract_cfgs[an.current_target_contract]

        # (1) state / constant variables  ─ 존재할 때만 주입
        #     현재 contract + parent 체인 순회
        # (2026-10-25) 파라미터·named return 이 같은 이름의 상태변수를 가리면 명시적 미지원 — 예전엔 아래 주입이 파라미터를
        #   덮어썼다(31_H_01 `wantToCVX(uint256 want)` vs 상태변수 `address want`).
        _locals = {p_name: "parameter" for _, p_name in params if p_name}
        _locals.update({r_name: "named return" for _, r_name in returns if r_name})

        def _inject_state_vars(cfg_obj):
            sv_node = getattr(cfg_obj, "state_variable_node", None)
            if sv_node and getattr(sv_node, "variables", None):
                for v in sv_node.variables.values():
                    if v.identifier in _locals:
                        raise UnsupportedShadowing(name, v.identifier, _locals[v.identifier])
                    fcfg.add_related_variable(v)
            # parent 체인 재귀
            for parent_cfg in getattr(cfg_obj, "parent_cfgs", {}).values():
                _inject_state_vars(parent_cfg)
        _inject_state_vars(ccf)

        # (2) 글로벌 변수 (block.timestamp 등) ─ ContractCFG 만 가짐
        if getattr(ccf, "globals", None):
            for gv in ccf.globals.values():
                fcfg.add_related_variable(gv)

        # ───────────────────────────────────────────────────────────────
        # ❷  entry-env 스냅 + entry_node.variables 초기화
        # ───────────────────────────────────────────────────────────────
        entry_vars = VariableEnv.copy_variables(fcfg.related_variables)
        fcfg.entry_env = entry_vars  # ★ 함수 진입 스냅샷
        fcfg.assign_env.update(entry_vars)
        fcfg.entry_node.variables.update(entry_vars)  # ★ entry_node에도 복사
        return fcfg

    @staticmethod
    def make_param_variable(an: ContractAnalyzer,
                            sol_type: SolType,
                            ident: str,
                            *,
                            scope: str = "local",
                            is_return_param: bool = False
                            ) -> Variables | ArrayVariable | StructVariable | EnumVariable:
        """
        파라미터·리턴 변수 1개 생성 + 기본 interval 초기화.
        ▶ 기존 ContractAnalyzer._make_param_variable 의 로직 그대로,
          차이점은 `an` 인스턴스를 첫 인자로 받아 snap·structDef 등에 접근.

        ★ is_return_param=True인 경우, Solidity 규약에 따라 0으로 초기화 (sound & precise)
           - input parameter: TOP으로 초기화 (호출자가 어떤 값이든 전달 가능)
           - return parameter: 0으로 초기화 (Solidity가 자동으로 0 초기화)
        """
        ccf = an.contract_cfgs[an.current_target_contract]

        # ──────────────────────────── ① array ────────────────────────────
        if sol_type.typeCategory == "array":
            arr = ArrayVariable(
                identifier=ident,
                base_type=sol_type.arrayBaseType,
                array_length=sol_type.arrayLength,
                is_dynamic=sol_type.isDynamicArray,
                scope=scope,
            )

            arr.initialize_default_by_base_type(is_return_param=is_return_param)

            an.register_var(arr)
            return arr

        # ──────────────────────────── ② struct ───────────────────────────
        if sol_type.typeCategory == "struct":
            sname = sol_type.structTypeName
            struct_def = ccf.structDefs.get(sname) or an.sa.file_level_structs.get(sname)
            # parent chain 검색
            if struct_def is None:
                def _search_parent_structs(cfg, name):
                    for pcfg in getattr(cfg, 'parent_cfgs', {}).values():
                        if name in pcfg.structDefs:
                            return pcfg.structDefs[name]
                        found = _search_parent_structs(pcfg, name)
                        if found:
                            return found
                    return None
                struct_def = _search_parent_structs(ccf, sname)
            if struct_def is None:
                raise ValueError(f"Undefined struct '{sname}' used as parameter/return.")
            sv = StructVariable(identifier=ident, struct_type=sname, scope=scope)
            sv.initialize_struct(struct_def, struct_defs=ccf.structDefs, enum_defs=ccf.enumDefs)

            an.register_var(sv)
            return sv

        # ──────────────────────────── ③ enum ────────────────────────────
        if sol_type.typeCategory == "enum":
            ev = EnumVariable(identifier=ident,
                              enum_type=sol_type.enumTypeName,
                              scope=scope)
            ev.valueIndex = 0  # 기본값 : 첫 멤버
            return ev

        # ──────────────────────────── ④ interface (address로 취급) ────────
        if sol_type.typeCategory == "interface":
            v = Variables(identifier=ident, scope=scope)
            v.typeInfo = sol_type
            v.value = AddressSet.top()
            an.register_var(v)
            return v

        # ──────────────────────────── ④-b 함수 타입 (2026-10-23) ─────────
        #   함수 포인터 값은 ⊤ 만 표현한다(Domain/FunctionRef.py).
        if sol_type.typeCategory == "function":
            v = Variables(identifier=ident, scope=scope)
            v.typeInfo = sol_type
            v.value = FunctionRef.top()
            an.register_var(v)
            return v

        # ──────────────────────────── ⑥ mapping ──────────────────────────
        # storage 파라미터는 caller의 실제 mapping을 aliasing해야 하는 게
        # Solidity 시맨틱스지만, 이 함수의 ① array 분기도 storage 배열
        # 파라미터를 caller와 잇지 않고 독립적인 fresh 값으로 근사하는 것과
        # 같은 컨벤션으로, 여기서도 진짜 aliasing 없이 fresh & empty
        # MappingVariable을 만든다 — 키별 값은 기존 lazy 경로
        # (MappingVariable.get_or_create/_make_value)가 실제 접근 시점에
        # 알아서 채운다.
        if sol_type.typeCategory == "mapping":
            all_structs, all_enums = an.get_full_struct_enum_defs()
            mv = MappingVariable(
                identifier=ident,
                key_type=sol_type.mappingKeyType,
                value_type=sol_type.mappingValueType,
                scope=scope,
                struct_defs=all_structs,
                enum_defs=all_enums,
            )
            an.register_var(mv)
            return mv

        # ──────────────────────────── ⑤ elementary ───────────────────────
        if sol_type.typeCategory == "elementary":
            v = Variables(identifier=ident, scope=scope)
            v.typeInfo = sol_type
            et = sol_type.elementaryTypeName

            if et.startswith("int"):
                bits = sol_type.intTypeLength or 256
                v.value = IntegerInterval(0, 0, bits) if is_return_param else IntegerInterval.top(bits)
            elif et.startswith("uint"):
                bits = sol_type.intTypeLength or 256
                v.value = UnsignedIntegerInterval(0, 0, bits) if is_return_param else UnsignedIntegerInterval.top(bits)
            elif et == "bool":
                v.value = BoolInterval(0, 0) if is_return_param else BoolInterval.top()
            elif et == "address":
                # address의 기본값은 address(0)
                v.value = AddressSet(ids={0}) if is_return_param else AddressSet.top()
            else:  # bytes / string …
                v.value = f"symbol_{ident}"

            an.register_var(v)
            return v

        raise ValueError(f"Unsupported typeCategory '{sol_type.typeCategory}'")
