# Analyzer/SolidityAnalyzer.py
"""
Solidity 소스 코드의 최상위 분석기.

소스 코드 저장 및 라인 관리(insert/shift/delete)를 담당하고,
file-level 구조(struct, type alias)를 자체 처리한다.
Contract-level 분석은 ContractAnalyzer에 위임한다.
"""
import re
from Domain.Type import SolType


class SolidityAnalyzer:

    def __init__(self):
        # ── Source storage ──────────────────────────────────────
        self.full_code = None
        self.full_code_lines = {}   # line_no -> code string
        self.line_info = {}         # line_no -> {"open": int, "close": int, "cfg_nodes": []}

        # ── Source management state ────────────────────────────
        self.current_start_line = None
        self.current_end_line = None
        self.current_edit_event = None
        self.current_close_before = False

        # ── File-level definitions ─────────────────────────────
        self.file_level_structs = {}    # {"StructName": {field_name: SolType, ...}}
        self.file_level_enums = {}      # {"EnumName": ["Member0", "Member1", ...]} — contract/
                                        # interface 밖 파일 최상위 enum.
                                        # ★ 배치 경로(Dependencies/main.py Phase 0)는 regex 로
                                        #   미리 스캔해 채우고, 에디터 경로는 아래 file-level
                                        #   처리(process_file_level_enum_*)가 채운다.
        self.type_aliases = {}          # {"TypeName": "underlyingElementaryType"}
        self._current_file_struct = None  # 현재 열린 file-level struct 이름
        self._current_file_enum = None    # 현재 열린 file-level enum 이름

        # ── ContractAnalyzer 생성 (self 참조 전달) ──────────────
        from Analyzer.ContractAnalyzer import ContractAnalyzer
        self.contract_analyzer = ContractAnalyzer(solidity_analyzer=self)

    # =================================================================
    #  Source management (update_code, _insert_lines, etc.)
    # =================================================================

    def update_code(self, start_line: int, end_line: int, new_code: str,
                    event: str, close_before: bool = False):
        """소스 코드 업데이트의 진입점."""
        self.current_start_line = start_line
        self.current_end_line = end_line
        self.current_edit_event = event
        self.current_close_before = close_before

        # CA에도 동기화 (CA의 process_* 메서드들이 current_start_line 등을 사용)
        ca = self.contract_analyzer
        ca.current_start_line = start_line
        ca.current_end_line = end_line
        ca.current_edit_event = event
        ca.current_close_before = close_before

        if event not in {"add", "modify", "delete"}:
            raise ValueError(f"unknown event '{event}'")

        if event == "add":
            lines = new_code.split("\n")
            # During annotation inline: 기존 코드가 있는 라인에 붙는 경우 밀기 불필요
            if self._is_during_inline(new_code, start_line):
                self.analyze_context(start_line, new_code)
                return
            self._insert_lines(start_line, lines)

        elif event == "modify":
            raw_lines = new_code.split("\n")
            norm_lines = self.normalize_compound_control_lines(raw_lines)
            if (end_line - start_line + 1) != len(norm_lines):
                self.update_code(start_line, end_line, "", event="delete")
                self.update_code(start_line, start_line + len(norm_lines) - 1,
                                 "\n".join(norm_lines), event="add")
                return

            ln = start_line
            for i, line in enumerate(norm_lines):
                self.full_code_lines[ln] = self._stored_text(line, i == 0)
                self.update_brace_count(ln, self.full_code_lines[ln])
                if self._should_trigger_analysis(line):
                    self.analyze_context(ln, line)
                ln += 1

        elif event == "delete":
            offset = end_line - start_line + 1

            # 기존 라인 제거
            for ln in range(start_line, end_line + 1):
                self.full_code_lines.pop(ln, None)
                self.line_info.pop(ln, None)
                ca.recorder.ledger.pop(ln, None)

            # 뒤쪽 라인 당기기
            keys_to_shift = sorted([ln for ln in self.full_code_lines if ln > end_line])
            for old_ln in keys_to_shift:
                new_ln = old_ln - offset
                self.full_code_lines[new_ln] = self.full_code_lines.pop(old_ln)
                self._shift_source_meta(old_ln, new_ln)
                ca._shift_cfg_meta(old_ln, new_ln)

        # full-code 재조합
        self.full_code = "\n".join(
            self.full_code_lines[ln] for ln in sorted(self.full_code_lines))

        # add/modify 후 전체 블록의 컨텍스트 설정
        if event in {"add", "modify"} and new_code.strip():
            self.analyze_context(start_line, new_code)

    def _insert_lines(self, start: int, new_lines: list[str]):
        new_lines = self.normalize_compound_control_lines(new_lines)
        # offset = 소스 스팬 (endLine - startLine + 1)
        if self.current_end_line is not None and self.current_end_line >= start:
            offset = self.current_end_line - start + 1
        else:
            offset = len(new_lines)

        # start 라인에 control flow 노드가 있고, 새 코드가 연속되는 control flow인지 체크
        skip_shift_at_start = False
        if start in self.line_info:
            cfg_nodes = self.line_info[start].get('cfg_nodes', [])
            first_new_line = new_lines[0].strip() if new_lines else ""

            for node in cfg_nodes:
                # (2026-10-23) catch 도 else 와 같다 — 앞 블록(try 또는 앞 catch)의 닫는 줄에는 try_join 이 있다.
                #   예전엔 line_info 에 등록된 적 없는 `try_false_stub` 이름을 찾아 catch 마다 줄이 하나씩 더 밀렸다.
                if (getattr(node, 'join_point_node', False) and
                    first_new_line.startswith(('else if', 'else', 'catch')) and
                    self.current_close_before):
                    skip_shift_at_start = True
                    break
                if (getattr(node, 'is_do_end', False) and
                    first_new_line.startswith('while') and
                    self.current_close_before):
                    skip_shift_at_start = True
                    break

        # 뒤 라인 밀기
        shift_from = start + 1 if skip_shift_at_start else start
        # skip_shift_at_start: start 라인을 재사용하므로 실제 필요한 shift 양은 1 줄음
        actual_offset = offset - 1 if skip_shift_at_start else offset
        ca = self.contract_analyzer

        for old_ln in sorted([ln for ln in self.full_code_lines if ln >= shift_from], reverse=True):
            self.full_code_lines[old_ln + actual_offset] = self.full_code_lines.pop(old_ln)
            self._shift_source_meta(old_ln, old_ln + actual_offset)
            ca._shift_cfg_meta(old_ln, old_ln + actual_offset)

        # 삽입 - 실제 코드 줄은 스팬 끝에, 빈 슬롯은 앞에 채운다
        write_count = min(len(new_lines), offset)
        write_start = start + offset - write_count

        # 앞쪽 빈 슬롯 채우기
        for i in range(write_start - start):
            ln = start + i
            if ln not in self.full_code_lines:
                self.full_code_lines[ln] = ""
            if ln not in self.line_info:
                self.line_info[ln] = {"open": 0, "close": 0, "cfg_nodes": []}

        # 실제 코드 줄 쓰기
        for i in range(write_count):
            ln = write_start + i
            line = new_lines[i]
            self.full_code_lines[ln] = self._stored_text(line, i == 0)
            self.update_brace_count(ln, self.full_code_lines[ln])
            if self._should_trigger_analysis(line):
                self.analyze_context(ln, line)

    def _stored_text(self, line: str, is_first: bool) -> str:
        """저장소(full_code_lines)에 둘 텍스트. (Phase 4b)

        slice 는 `} else {` / `} catch` / `} while` 의 닫는 괄호를 떼어 record 의 `closeBefore` 로
        옮긴다. 그 줄은 앞 블록의 `}` 자리를 재사용하므로, 떼어낸 괄호를 저장소에 되돌려 놓지 않으면
        **앞 블록의 `}` 가 소스에서 사라진다** — 괄호 균형으로 블록을 찾는 모든 곳
        (`_enclosing_open_brace_line`, `_function_body_range`, `_function_span` …)이 한 단계씩
        어긋나, if/else 뒤의 줄이 if 블록 안으로, 함수 뒤의 `event` 가 함수 안 문장으로 분류됐다.
        분석(파싱)에는 괄호를 뗀 원래 줄을 그대로 쓴다."""
        if is_first and self.current_close_before:
            return "} " + line.lstrip()
        return line

    def _shift_source_meta(self, old_ln: int, new_ln: int):
        """소스 메타데이터(line_info) key 이동"""
        if old_ln in self.line_info:
            self.line_info[new_ln] = self.line_info.pop(old_ln)

    def _should_trigger_analysis(self, code_line: str) -> bool:
        s = (code_line or "").strip()
        if not s:
            return False
        if s == "}":
            return False
        if s.startswith("pragma ") or s.startswith("import "):
            return False
        if s.startswith("//"):
            return s.startswith("// @")
        if s.endswith(";"):
            return True
        if ')' in s and '{' in s and not s.startswith(('if', 'for', 'while', 'else')):
            return True
        return bool(re.match(
            r"^(abstract\s+contract|contract|library|interface|function|constructor|modifier|"
            r"struct|enum|event|if|else(\s+if)?\b|for|while|do\b|try|catch|unchecked|assembly)\b", s))

    def normalize_compound_control_lines(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        pat = re.compile(r'}\s*(?=else\b|while\b)')
        for s in lines:
            rest = s
            while True:
                m = pat.search(rest)
                if not m:
                    out.append(rest)
                    break
                left = rest[:m.start()] + "}"
                right = rest[m.end():].lstrip()
                out.append(left)
                rest = right
        return out

    def update_brace_count(self, line_number, code):
        open_braces = code.count('{')
        close_braces = code.count('}')
        if line_number not in self.line_info:
            self.line_info[line_number] = {"open": 0, "close": 0, "cfg_nodes": []}
        info = self.line_info[line_number]
        info['open'] = open_braces
        info['close'] = close_braces

    def _is_during_inline(self, code: str, start_line: int) -> bool:
        """@During 이 기존 코드 라인을 참조만 하므로 밀기가 불필요한 inline 인지 확인.

        @GlobalVar/@StateVar 등 디버그 값 annotation 은 여전히 항상 standalone —
        실제 값을 배치 재생 시점에 주입하는 것이라 자기 줄을 차지해야 한다. @During 은
        반대로 이미 존재하는 코드 줄을 참조만 하므로, 그 줄에 실제 코드가 있으면 밀 필요가
        없다 — 오히려 밀면 이미 만들어진 CFG/exit-node 상태가 깨진다.

        여러 줄에 걸친 statement 는 실제 텍스트가 span 의 끝에 쓰이고 시작 줄의
        full_code_lines 는 빈 placeholder 로 남는다 — 반면 CFG 노드는 그 statement 의
        선언 시점 startLine 기준으로 등록된다. full_code_lines 만 보면 이런 경우 시작 줄이
        "비어있다"고 오판해 밀기 경로로 빠지므로, line_info 의 CFG 노드 존재도 함께 본다.
        (@Post 는 특정 줄이 아니라 함수 exit 노드에 붙으므로 여기 대상이 아니다 —
        IntentKeeper 의 주 경로인 engine_api._attach_intents 는 애초에 update_code 를
        호출하지 않아 밀림 자체가 없다. 이 함수는 레거시 CLI(main.py) 경로 전용.)"""
        stripped = code.strip()
        if not stripped.startswith("// @During"):
            return False
        target_line = self.full_code_lines.get(start_line, "").strip()
        # 순수 구조적 문자({, })만 있는 줄은 실제 target statement 가 아님 — 밀려난 함수
        # skeleton 의 닫는 `}` placeholder 를 "이미 텍스트 있음"으로 오판하지 않게 제외.
        is_structural_only = target_line != "" and all(c in "{}" for c in target_line)
        has_text = target_line != "" and not is_structural_only
        has_cfg_node = bool(self.line_info.get(start_line, {}).get("cfg_nodes"))
        return has_text or has_cfg_node

    def get_full_code(self):
        return self.full_code

    def compile_check(self) -> None:
        from solcx import (install_solc, set_solc_version, compile_source,
                           get_installed_solc_versions)
        from solcx.exceptions import SolcError

        wanted = '0.8.0'
        if wanted not in get_installed_solc_versions():
            print(f"[info] installing solc {wanted} …")
            install_solc(wanted)
        set_solc_version(wanted)
        try:
            compile_source(self.full_code)
            print("[ok] solidity compiled successfully")
        except SolcError as e:
            print("[err] Solidity compiler reported:\n", e)
        except Exception as e:
            print("[err] unexpected:", e)

    # =================================================================
    #  Context analysis dispatcher
    # =================================================================

    def analyze_context(self, start_line, code):
        """Context dispatcher: file-level → 자체 처리, contract-level → CA 위임."""
        stripped = (code or "").strip()
        if not stripped or stripped == "}":
            return
        if stripped.startswith("pragma ") or stripped.startswith("import "):
            return

        # debug annotations → 항상 CA
        if stripped.startswith("// @"):
            self.contract_analyzer.analyze_context(start_line, code)
            return

        # assembly 내부 감지: assembly 블록 안이면 context를 "assembly"로 설정
        # ★ F40: 예전엔 `find_parent_context(...) == "assembly"` 즉 **직근 부모**만 봤다.
        #   그래서 `if iszero(...) {` / `case 32 {` 같은 **중첩 Yul 블록** 안의 줄은
        #   부모가 "if"/"case" 라 검사를 통과하지 못하고 **Solidity 파서로** 갔다
        #   (`[ANTLR]` 12건 중 8건). `is_inside_assembly` 는 괄호 체인을 위로 걸어올라간다.
        if self.contract_analyzer.is_inside_assembly(start_line):
            ca = self.contract_analyzer
            ca.current_context_type = "assembly"
            ca.current_target_contract = ca.find_contract_context(start_line)
            # ★ find_function_context 는 (이름, 파라미터타입리스트) **튜플**을 돌려준다.
            #   여기만 언패킹을 빠뜨려 current_target_function 에 튜플이 통째로 들어갔고,
            #   ContractCFG.get_function_cfg 의 `self.functions.get(function_name)` 에서
            #   `TypeError: unhashable type: 'list'` 로 터졌다. assembly 를 가진 모든
            #   소스가 첫 Yul 문장부터 실패해 함수 본문이 빈 채 pkl 로 저장됐다
            #   (전수 확인: 나머지 5개 호출부는 전부 정상 언패킹).
            ca.current_target_function, ca.current_target_function_param_types =                 ca.find_function_context(start_line)
            return

        # file-level 판별: enclosing contract가 없으면 file level
        contract = self.contract_analyzer.find_contract_context(start_line)
        if contract is None:
            if self._handle_file_level(start_line, code, stripped):
                return  # SA가 처리함

        # 나머지는 CA에 위임 (contract-level + contract/library/interface 정의)
        self.contract_analyzer.analyze_context(start_line, code)

    def _handle_file_level(self, start_line, code, stripped):
        """File-level 구조 처리. 처리했으면 True 반환."""
        ca = self.contract_analyzer

        # type alias: type Fixed18 is int256;
        if stripped.startswith("type ") and " is " in stripped and stripped.endswith(";"):
            ca.current_context_type = "fileLevelTypeAlias"
            ca.current_target_contract = None
            ca.current_target_function = None
            ca.current_target_struct = None
            return True

        # (2026-10-23) file-level error/event: `error E(uint256 a);` (0.8.4+) / `event Ev(...);` (0.8.22+)
        #   이 분기가 없어 CA 로 위임됐고 "Contract context not found" → 문장(simpleStatement)으로 파싱 실패했다
        #   (PRBMath.sol 의 file-level error 21개). 선언 자체는 해석에 쓰는 정보가 없다(contract 안의 error 정의와 같다).
        if re.match(r"(?:error|event)\s+[A-Za-z_]\w*\s*\(", stripped) and stripped.endswith(";"):
            ca.current_context_type = "error" if stripped.startswith("error") else "event"
            ca.current_target_contract = None
            ca.current_target_function = None
            ca.current_target_struct = None
            return True

        # struct definition header: struct X {
        if stripped.startswith("struct ") and "{" in stripped:
            ca.current_context_type = "fileLevelStruct"
            ca.current_target_contract = None
            ca.current_target_function = None
            ca.current_target_struct = None
            return True

        # struct member (inside file-level struct)
        if stripped.endswith(";") and self._current_file_struct is not None:
            parent = ca.find_parent_context(start_line)
            if parent == "struct":
                ca.current_context_type = "fileLevelStructMember"
                ca.current_target_contract = None
                ca.current_target_function = None
                ca.current_target_struct = ca.find_struct_context(start_line)
                return True

        # enum definition header: enum X {
        # ★ struct 와 대칭. 이 분기가 없어서 파일 최상위 enum 이 CA 로 위임됐고,
        #   CA 는 "contract 를 못 찾았다" 며 raise → 그 예외로 analyze_context 가 중단되는
        #   바람에 current_context_type 이 "enum" 인 채로 남아, 드라이버가 그대로 파싱·visit
        #   하다 process_enum_definition 의 contract_cfgs.get(None) 에서 또 터졌다
        #   (IMochiProfile / IMochiVault, 파일당 2건 × 2종 = 8건).
        if stripped.startswith("enum ") and "{" in stripped:
            ca.current_context_type = "fileLevelEnum"
            ca.current_target_contract = None
            ca.current_target_function = None
            ca.current_target_struct = None
            return True

        # enum member (inside file-level enum)
        # ★ struct member 와 달리 `;` 로 끝나지 않는다(`Invalid, Stable, Alpha`). 그래서
        #   종결자 대신 "열린 file-level enum 이 있고 부모 블록이 enum 인가" 로 판정한다.
        if self._current_file_enum is not None and not stripped.startswith("}"):
            if ca.find_parent_context(start_line) == "enum":
                ca.current_context_type = "fileLevelEnumMember"
                ca.current_target_contract = None
                ca.current_target_function = None
                ca.current_target_struct = None
                return True

        return False  # SA가 처리하지 못함 → CA에 위임

    # =================================================================
    #  File-level process 메서드
    # =================================================================

    def process_file_level_struct_definition(self, struct_name: str):
        """file-level struct 정의 시작 (contract level과 동일하게 StructDefinition 사용)"""
        from Domain.Variable import StructDefinition
        self._current_file_struct = struct_name
        self.file_level_structs[struct_name] = StructDefinition(struct_name)

    def process_file_level_struct_member(self, var_name: str, type_obj):
        """file-level struct member 추가"""
        if self._current_file_struct and self._current_file_struct in self.file_level_structs:
            self.file_level_structs[self._current_file_struct].add_member(var_name, type_obj)

    def end_file_level_struct(self):
        """file-level struct 정의 종료"""
        self._current_file_struct = None

    def process_file_level_enum_definition(self, enum_name: str):
        """file-level enum 정의 시작 (contract level 의 EnumDefinition 과 달리
        여기서는 **멤버 이름 리스트**로 보관한다 — `file_level_enums` 의 기존 형식이
        `{"EnumName": ["Member0", ...]}` 이고 Evaluation/Visitor 소비부가 그걸 전제한다)."""
        self._current_file_enum = enum_name
        self.file_level_enums[enum_name] = []

    def process_file_level_enum_item(self, items: list[str]):
        """file-level enum 멤버 추가 (선언 순서 = 값)"""
        if self._current_file_enum and self._current_file_enum in self.file_level_enums:
            self.file_level_enums[self._current_file_enum].extend(items)

    def end_file_level_enum(self):
        """file-level enum 정의 종료"""
        self._current_file_enum = None

    def process_type_alias(self, alias_name: str, underlying_type: str):
        """user-defined value type alias 등록 (e.g., type Fixed18 is int256)"""
        self.type_aliases[alias_name] = underlying_type

    # =================================================================
    #  File-level 조회 API
    # =================================================================

    def get_file_level_struct(self, name: str):
        """file-level struct 정의 조회 (StructDefinition 또는 None)"""
        return self.file_level_structs.get(name)

    def resolve_type(self, type_name: str) -> str | None:
        """user-defined type alias를 underlying type으로 resolve.
        alias가 없으면 None 반환."""
        return self.type_aliases.get(type_name)
