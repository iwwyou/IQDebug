from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Any, Union

from Domain.IR import Expression
from Domain.Variable import (
    Variables,
    StructVariable,
    ArrayVariable,
    array_length,
    MappingVariable,
    EnumVariable,
)
from Domain.AddressSet import AddressSet

from Utils.CFG import FunctionCFG

class RecordManager:

    def __init__(self) -> None:
        # line_no -> list[ record-dict ]
        self.ledger: defaultdict[int, List[Dict[str, Any]]] = defaultdict(list)

    # ------------------------------------------------------ public accessors
    def __getitem__(self, line_no: int) -> List[Dict[str, Any]]:
        """Syntactic sugar so legacy `self.analysis_per_line[ln]` still works."""
        return self.ledger[line_no]     # ← list · auto-created by defaultdict

    def get_range(self, start: int, end: int) -> Dict[int, List[Dict[str, Any]]]:
        return {ln: self.ledger[ln] for ln in range(start, end + 1) if ln in self.ledger}

    def clear_line(self, line_no: int) -> None:
        """해당 라인의 레코드 삭제 (재분석 전 초기화용)"""
        if line_no in self.ledger:
            self.ledger[line_no].clear()

    # ─────────────────────────────────────────────────────
    # 지역변수 선언 기록
    # ─────────────────────────────────────────────────────
    def record_variable_declaration(
            self,
            *,
            line_no: int,
            var_name: str,
            var_obj
    ) -> None:
        """
        · line_no : 선언이 등장한 소스 라인
        · var_name: 식별자
        · var_obj : Variables / ArrayVariable / StructVariable …
        """
        lhs_expr = Expression(identifier=var_name,
                              context="IdentifierExpContext")

        # ① 기록용 dict 준비
        record = {
            "kind": "varDeclaration",
            "vars": {}
        }

        # ② 복합-타입 flatten / 단일-값 직렬화
        if isinstance(var_obj, (ArrayVariable, StructVariable, MappingVariable)):
            self._flatten_var(var_obj, var_name, record["vars"])
        else:  # Variables / EnumVariable
            key = self._expr_to_str(lhs_expr)
            record["vars"][key] = self._serialize_val(
                getattr(var_obj, "value", None)
            )

        # ③ analysis_per_line[line_no] 에 저장/교체
        rec_list = self.ledger[line_no]
        # 같은 식별자 선언이 이미 있으면 덮어쓰기
        for i, old in enumerate(rec_list):
            if old.get("kind") == "varDeclaration" and \
                    set(old.get("vars", {}).keys()) == set(record["vars"].keys()):
                rec_list[i] = record
                break
        else:
            rec_list.append(record)


    def record_assignment(
        self,
        *,
        line_no: int,
        expr: Expression,
        var_obj,
        base_obj=None,
    ) -> None:

        key_prefix = self._expr_to_str(expr)

        # ② payload 작성
        if isinstance(var_obj, (ArrayVariable, StructVariable, MappingVariable)):
            flat: Dict[str, Any] = {}
            self._flatten_var(var_obj, key_prefix, flat)
            payload: Dict[str, Any] = {"kind": "assignment", "vars": flat}
        else:
            payload = {
                "kind": "assignment",
                "vars": {
                    key_prefix: self._serialize_val(
                        getattr(var_obj, "value", None)
                    )
                },
            }

        # ③ line_no → rec_list 가져오기
        rec_list = self.ledger[line_no]          #   self._acc  == defaultdict(list)

        # ④ “같은 루트-키” 기록이 이미 있으면 **교체**, 없으면 append
        new_keys = set(payload["vars"].keys())
        for idx, rec in enumerate(rec_list):
            if (
                rec.get("kind") == "assignment"
                and set(rec.get("vars", {}).keys()) == new_keys
            ):
                rec_list[idx] = payload          # ← 덮어쓰기
                break
        else:
            rec_list.append(payload)             # ← 새로 추가

    def record_return(
            self,
            *,
            line_no: int,
            return_expr: Expression | None,
            return_val,
            fn_cfg: FunctionCFG,
    ) -> None:

        if return_expr and return_expr.context == "TupleExpressionContext":
            # 단일 값이 괄호로 감싸진 경우 (return (expr)) → return_val이 iterable이 아닐 수 있음
            if hasattr(return_val, '__iter__') and not isinstance(return_val, (str, dict)):
                flat = {
                    self._expr_to_str(e): self._serialize_val(v)
                    for e, v in zip(return_expr.elements, return_val)
                }
            else:
                flat = {self._expr_to_str(return_expr): self._serialize_val(return_val)}
            payload = {"kind": "return", "vars": flat}

        elif return_expr is None and fn_cfg.return_vars:
            flat = {
                rv.identifier: self._serialize_val(rv.value)
                for rv in fn_cfg.return_vars
            }
            payload = {"kind": "return", "vars": flat}

        else:
            key = self._expr_to_str(return_expr) if return_expr else "<value>"
            payload = {"kind": "return",
                       "vars": {key: self._serialize_val(return_val)}}

        self.ledger[line_no].append(payload)

    def record_revert(
            self,
            *,
            line_no: int,
            revert_id: str | None,
            string_literal: str | None,
            call_args: list[Expression] | None,
    ) -> None:
        payload = {
            "kind": "revert",
            "detail": {
                "id": revert_id or "",
                "msg": string_literal or "",
                "args": [self._expr_to_str(a) for a in call_args] if call_args else [],
            },
        }
        self.ledger[line_no].append(payload)

    def record_verification_result(
            self,
            line_no: int,
            verification_type: str,
            result: Dict[str, Any]
    ) -> None:
        """Record the result of a @During or @Post verification."""
        payload = {
            "kind": "verification",
            "verification_type": verification_type,
            "status": result.get("status", "unknown"),
            "message": result.get("message", ""),
            "details": result.get("details", {}),
        }
        self.ledger[line_no].append(payload)

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def add_env_record(
            self,
            line_no: int,
            stmt_type: str,
            env: Dict[str, Variables]
    ) -> None:
        """
        Flatten *changed* variable environment and store it under line_no.
        """
        flat: Dict[str, Any] = {}
        for name, var in env.items():  # 🔸 key(변수명) 사용
            self._flatten_var(var, name, flat)  # v.identifier 대신 name
        self._append_or_replace(
            line_no,
            {"kind": stmt_type, "vars": flat},
            replace_rule=lambda old, new: old.get("kind") == new["kind"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_or_replace(self, line_no: int, new_rec: Dict[str, Any], *, replace_rule) -> None:
        existing = self.ledger[line_no]
        for idx, rec in enumerate(existing):
            if replace_rule(rec, new_rec):
                existing[idx] = new_rec  # replace in‑place
                return
        existing.append(new_rec)

    # ------------------------------------------------------------
    # (de)serialisation utilities – mostly copied from ContractAnalyzer
    # ------------------------------------------------------------

    def _expr_to_str(self, e: Expression) -> str:
        """Convert *partial* Expression trees (identifier / member / index) back
        to a Solidity‑like string representation so that the UI can display a
        familiar *path* to the variable.
        """
        if e is None:
            return ""

        # ① root identifier or literal
        if e.base is None:
            return e.identifier or str(e.literal)

        # ② member access
        if e.member is not None:
            return f"{self._expr_to_str(e.base)}.{e.member}"

        # ③ index access
        if e.index is not None:
            return f"{self._expr_to_str(e.base)}[{self._expr_to_str(e.index)}]"

        return "<expr>"  # fallback – should rarely happen for LHS paths

    # -------------------------------------- flatten composite variables ----

    def _flatten_var(self, var_obj: Any, prefix: str, out: Dict[str, Any]):
        # ArrayVariable ---------------------------------------------------
        if isinstance(var_obj, ArrayVariable):
            # (2026-10-23) len 은 추상 길이(array_length) — 구체화한 원소 수가 아니다(선언 `T[]` 의 arrayLength 0 도 아니다)
            arr_len = self._serialize_val(array_length(var_obj))
            if not var_obj.elements and not var_obj.sym_cells:
                out[prefix] = f"array(len={arr_len})"
                self._flatten_summary(var_obj, prefix, out)
                return
            # 모든 원소가 ⊤(top)이면 배열 요약 형태로 표시
            all_top = all(
                isinstance(getattr(elem, 'value', None), AddressSet) and getattr(elem, 'value', None).is_top
                for elem in var_obj.elements.values()
                if isinstance(elem, Variables)
            )
            if all_top and len(var_obj.elements) > 0:
                out[prefix] = f"array(len={arr_len})"
                return
            for idx, elem in sorted(var_obj.elements.items()):      # (2026-10-23) 희소 — 구체화된 원소만, 나머지는 [*]
                self._flatten_var(elem, f"{prefix}[{idx}]", out)
            for k, cell in var_obj.sym_cells.items():          # (2026-10-23) 선택자 셀 arr[i]
                self._flatten_var(cell, f"{prefix}[{k}]", out)
            self._flatten_summary(var_obj, prefix, out)
            return

        # StructVariable --------------------------------------------------
        if isinstance(var_obj, StructVariable):
            for m, mem in var_obj.members.items():
                self._flatten_var(mem, f"{prefix}.{m}", out)
            return

        # MappingVariable -------------------------------------------------
        if isinstance(var_obj, MappingVariable):
            for k, mv in var_obj.mapping.items():
                self._flatten_var(mv, f"{prefix}[{k}]", out)
            self._flatten_summary(var_obj, prefix, out)
            return

        # Leaf (Variables / EnumVariable) ---------------------------------
        val_ser = self._serialize_val(getattr(var_obj, "value", None))
        out[prefix] = val_ser

    def _flatten_summary(self, c, prefix, out):
        """(2026-10-23, 사용자 결정) 구체화하지 않은 위치들의 요약 `x[*]` — 선언 기본값에서 **바뀌었을 때만** 보인다
        (미확정 위치 쓰기가 다른 키에 영향을 줬을 수 있다는 표시)."""
        if c.summary is None:
            return
        cur, dflt = {}, {}
        self._flatten_var(c.summary, f"{prefix}[*]", cur)
        self._flatten_var(c.fresh_default(), f"{prefix}[*]", dflt)
        if cur != dflt:
            out.update(cur)

    # -------------------------------------- value serialisation  ---------

    def _serialize_val(self, v: Any) -> str:
        # AddressSet  ----------------------------------------------------
        if isinstance(v, AddressSet):
            if v.is_top:
                return "address(⊤)"
            if not v.ids:
                return "address(⊥)"
            # 구체적인 ID들을 정렬해서 표시
            return f"address({{{', '.join(map(str, sorted(v.ids)))}}})"

        # Interval / BoolInterval  ---------------------------------------
        if hasattr(v, "min_value"):
            return f"[{v.min_value},{v.max_value}]"

        # ArrayVariable – return에서는 실제 원소 값을 리스트로 표시
        if isinstance(v, ArrayVariable):
            # (2026-10-23, 희소 배열) 길이가 정해져 있고 작으면 전 원소(Read), 아니면 길이만
            n = v.static_length()
            if n is None and v.length is not None and v.length.min_value == v.length.max_value:
                n = v.length.min_value
            if n is not None and n <= 64:
                elem_vals = [self._serialize_val(getattr(e, 'value', e)) for e in (v.read_element(k) for k in range(n))]
                return f"[{', '.join(elem_vals)}]"
            return f"array(len={self._serialize_val(array_length(v))})"

        # StructVariable / MappingVariable – brief summary only
        if isinstance(v, StructVariable):
            return "struct"  # UI will show individual members anyway
        if isinstance(v, MappingVariable):
            return f"mapping(size={len(v.mapping)})"

        # List/Tuple – 튜플 반환값 처리
        if isinstance(v, (list, tuple)):
            elem_vals = [self._serialize_val(e) for e in v]
            return f"({', '.join(elem_vals)})"

        # Fallback str()
        return str(v)
