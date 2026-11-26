"""Declarations — 소스 전체의 타입 선언과 파일 수준 상수를 먼저 모은다 (2026-11-25).

소스는 줄 레코드 단위로 위에서 아래로 분석하는데, Solidity 는 선언보다 먼저 쓰는 것을 허용한다
(struct 멤버가 뒤에 선언된 struct 를 쓴다, 인터페이스 둘이 서로를 쓴다). 그래서 줄 분석 전에

  1. 이름을 등록한다 — contract/interface/library 의 종류와 부모, struct·enum 선언 객체(내용은 아직 빈 것), 타입 별칭
  2. struct 멤버의 타입을 채운다 — 이때는 모든 이름이 등록돼 있다
  3. 파일 수준 상수의 식을 등록한다 — 값은 읽을 때 계산한다(`ContractAnalyzer.file_constant`)

줄 분석은 같은 선언 객체를 이어서 쓴다(`ContractAnalyzer.process_struct_definition` 등). 2 에서 해석하지 못한 struct
(예: 길이가 컨트랙트 상수인 배열)는 멤버를 비워 두고 줄 분석이 채운다 — 오류도 줄 분석이 그 줄에서 낸다.
"""
from __future__ import annotations

import re

from Domain.Variable import EnumDefinition, StructDefinition

_TOKEN = re.compile(r"""//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'""", re.S)
_ID = r"[A-Za-z_$][\w$]*"
_DECL = re.compile(
    rf"(?P<open>\{{)|(?P<close>\}})"
    rf"|\b(?:abstract\s+)?(?P<ckind>contract|interface|library)\s+(?P<cname>{_ID})(?P<bases>[^{{}};]*)\{{"
    rf"|\bstruct\s+(?P<sname>{_ID})\s*\{{(?P<sbody>[^{{}}]*)\}}"
    rf"|\benum\s+(?P<ename>{_ID})\s*\{{(?P<ebody>[^{{}}]*)\}}"
    rf"|\btype\s+(?P<tname>{_ID})\s+is\s+(?P<ttype>[^;{{}}]+);"
    rf"|(?P<const>\b[A-Za-z_$][\w$.\[\]]*\s+constant\s+{_ID}\s*=[^;]*;)")


def _blank(src: str, strings: bool) -> str:
    """주석(과 문자열 내용)을 같은 길이의 공백으로."""
    def sub(m):
        t = m.group()
        if t[0] == "/":
            return re.sub(r"[^\n]", " ", t)
        return t[0] + " " * (len(t) - 2) + t[-1] if strings else t
    return _TOKEN.sub(sub, src)


def scan(source: str) -> list[dict]:
    """[{kind, owner, name, ...}] — owner = 감싸는 컨트랙트/인터페이스/라이브러리(파일 수준 = None)."""
    text, code = _blank(source, True), _blank(source, False)
    out, depth, owner, pos = [], 0, None, 0
    while True:
        m = _DECL.search(text, pos)
        if m is None:
            return out
        pos = m.end()
        if m["open"]:
            depth += 1
        elif m["close"]:
            depth -= 1
            if depth <= 0:
                depth, owner = 0, None
        elif m["cname"]:
            if depth == 0:
                owner = m["cname"]
                bases = re.sub(r"\([^()]*\)", "", m["bases"].partition("is")[2]) if re.match(r"\s*is\b", m["bases"]) else ""
                out.append({"kind": m["ckind"], "owner": None, "name": owner,
                            "bases": [b.strip() for b in bases.split(",") if b.strip()]})
            depth += 1
        elif depth > 1 or (depth == 1 and owner is None):
            pos = m.start() + 1                      # 함수 본문 안 — 선언 자리가 아니다
        elif m["sname"]:
            out.append({"kind": "struct", "owner": owner, "name": m["sname"],
                        "members": [x.strip() + ";" for x in m["sbody"].split(";") if x.strip()]})
        elif m["ename"]:
            out.append({"kind": "enum", "owner": owner, "name": m["ename"],
                        "members": [x.strip() for x in m["ebody"].split(",") if x.strip()]})
        elif m["tname"]:
            out.append({"kind": "type", "owner": owner, "name": m["tname"], "type": " ".join(m["ttype"].split())})
        elif depth == 0:
            out.append({"kind": "constant", "owner": None, "name": None,
                        "code": " ".join(code[m.start("const"):m.end("const")].split())})


class Predeclared:
    """현재 소스가 선언하는 타입. kinds: 이름 → contract/interface/library, bases: 이름 → 부모 이름,
    defs: (owner, 이름) → StructDefinition | EnumDefinition."""

    def __init__(self):
        self.kinds, self.bases, self.defs = {}, {}, {}


def predeclare(sa, source: str) -> None:
    """소스 전체의 선언을 sa 와 그 ContractAnalyzer 에 등록한다. 이미 분석된 선언 객체는 그대로 둔다(증분 편집)."""
    from Analyzer.EnhancedSolidityVisitor import EnhancedSolidityVisitor
    from Utils.Helper import ParserHelpers

    ca = sa.contract_analyzer
    decls = scan(source)
    pre = Predeclared()

    def existing(owner, name, attr):
        if owner is None:
            return sa.file_level_structs.get(name) if attr == "structDefs" else None
        cfg = ca.contract_cfgs.get(owner)
        return None if cfg is None or getattr(cfg, "from_dependency", False) else getattr(cfg, attr).get(name)

    new = []
    for d in decls:
        owner, name = d["owner"], d["name"]
        if d["kind"] in ("contract", "interface", "library"):
            pre.kinds[name], pre.bases[name] = d["kind"], d["bases"]
        elif d["kind"] == "struct":
            sd = existing(owner, name, "structDefs")
            if sd is None:
                sd = StructDefinition(name)
                sd.owner, sd.predeclared = owner, "partial"
                new.append((sd, d))
                if owner is None:
                    sa.file_level_structs[name] = sd
            pre.defs[(owner, name)] = sd
        elif d["kind"] == "enum":
            ed = existing(owner, name, "enumDefs")
            if ed is None:
                ed = EnumDefinition(name)
                ed.owner, ed.members, ed.predeclared = owner, list(d["members"]), "complete"
            if owner is None:
                sa.file_level_enums[name] = list(d["members"])
            pre.defs[(owner, name)] = ed
        elif d["kind"] == "type":
            sa.type_aliases[name] = d["type"]
    ca.predeclared = pre

    saved = (ca.current_target_contract, ca.current_context_type)
    visitor = EnhancedSolidityVisitor(ca)
    try:
        for sd, d in new:
            ca.current_target_contract = d["owner"]
            try:
                for code in d["members"]:
                    visitor.add_struct_member(sd, ParserHelpers.generate_parse_tree(code, "structMember"))
                sd.predeclared = "complete"
            except ValueError:                       # UnresolvedName 포함 — 줄 분석이 그 줄에서 다시 해석한다
                sd.members.clear()
        ca.current_target_contract, ca.current_context_type = None, "fileLevelConstant"
        for d in decls:
            if d["kind"] == "constant":
                try:
                    visitor.visit(ParserHelpers.generate_parse_tree(d["code"], "fileLevelConstant"))
                except ValueError:
                    pass                             # 줄 분석이 그 줄에서 다시 해석한다
    finally:
        ca.current_target_contract, ca.current_context_type = saved
