#!/usr/bin/env python3
"""
의존성 레지스트리 — **프로젝트 단위**, 단위(SourceUnit) = 전처리된 소스 파일 하나 (2026-10-23, 사용자 승인 R0~R3).

예전 빌드(`Dependencies/main.py --root`)는 원본 파일을 **선언별 조각**으로 나눠, 위상정렬(`_topo_order`) 순서대로
분석하면서 앞서 저장된 pkl 을 정규식으로 골라 읽었다. 그래서
  · 순환(인터페이스 IA ↔ IB 가 서로의 타입을 쓰는 것 등)이 있으면 빌드 전체가 예외로 멈췄고(w34·w36·w58·w65),
  · 부모를 못 찾으면 조용히 건너뛰었고, pkl 마다 부모·using 라이브러리 CFG 의 **사본**이 들어가 같은 선언이 여러 객체로 로드됐다.

이 모듈:
  1. 단위마다 선언 이름(contract/library/interface, 파일 수준 struct·enum, `type X is`)을 미리 모은다.
     같은 이름을 두 단위가 선언하면 둘 다 Invalid (프로젝트 전역 이름 공간 — 2026-10-23 사용자 결정).
  2. 의존 = 단위 본문에 나오는 식별자 중 **다른 단위가 선언한 이름**(어휘적 과근사). 의존 그래프의 SCC 를 위상 순서로 분석한다.
  3. 단위 분석 = 의존 폐포의 **현재 결과 객체**를 주입한 새 분석기로 한 번 분석. 의존 폐포 중 어느 단위의 결과가
     바뀌면(지문 비교) 다시 분석한다 — 순환(SCC)은 이 고정점 반복으로 풀린다(합의안의 "SCC 승격").
  4. 상태: 미해석 이름(`UnresolvedName`)이 남으면 Pending, 그 밖 오류가 남으면 Invalid, 깨끗하면 Complete.
     (의존 폐포 전체가 Complete 여야 타깃을 분석할 수 있다는 게이트는 R5 — 지금은 로드 보고에 상태를 싣는다.)
  5. 저장: 단위 pkl 에 다른 선언의 CFG 객체를 넣지 않는다 — 모든 CFG 참조를 **이름**으로 저장하고(persistent id),
     로드할 때 선언마다 객체 하나를 만들어 연결한다(사본 중복 제거).

배치: deps/<p>/sources/<단위>.sol (전처리본) → deps/<p>/builds/build_NNN/{manifest.json, units/<단위>.pkl}, deps/<p>/CURRENT
사용: python Dependencies/registry.py --root deps/<p>      (sources/ 를 다시 빌드)
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import io
import json
import pathlib
import pickle
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from Utils.CFG import ContractCFG, LibraryCFG, InterfaceCFG, FunctionCFG  # noqa: E402
from Utils.Errors import UnresolvedName  # noqa: E402

_CFG_CLASSES = (ContractCFG, LibraryCFG, InterfaceCFG)
MAX_ROUNDS_SLACK = 2
# (R5) 타깃 의존 폐포가 전부 Complete 여야 로드한다. (2026-10-24 사용자 승인으로 켬 — 42 프로젝트 전부 Complete 확인 후)
REQUIRE_COMPLETE = True


# ── 선언·참조 스캔 (어휘) ───────────────────────────────────────────────────────

_DECL_RE = re.compile(r'^[ \t]*(?:abstract[ \t]+)?(library|interface|contract)[ \t]+([A-Za-z_]\w*)', re.M)
_ALIAS_RE = re.compile(r'\btype[ \t]+([A-Za-z_]\w*)[ \t]+is\b')
_FILE_STRUCT_ENUM_RE = re.compile(r'^(struct|enum)[ \t]+([A-Za-z_]\w*)')
_IDENT_RE = re.compile(r'\b[A-Za-z_]\w*\b')
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _strip(src: str) -> str:
    src = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)
    src = re.sub(r'//[^\n]*', ' ', src)
    return _STRING_RE.sub('""', src)


def _file_level_names(text: str) -> list[tuple[str, str]]:
    """중괄호 깊이 0 의 struct/enum 선언."""
    out, depth = [], 0
    for line in text.splitlines():
        s = line.strip()
        if depth == 0:
            m = _FILE_STRUCT_ENUM_RE.match(s)
            if m:
                out.append((m.group(1), m.group(2)))
        depth = max(0, depth + s.count('{') - s.count('}'))
    return out


def scan_unit(text: str) -> dict:
    t = _strip(text)
    return {
        "decls": [(m.group(1), m.group(2)) for m in _DECL_RE.finditer(t)],
        "file_level": _file_level_names(t),
        "aliases": _ALIAS_RE.findall(t),
        "idents": set(_IDENT_RE.findall(t)),
    }


def unit_id_for(path: pathlib.Path, roots: list[pathlib.Path]) -> str:
    """원본 경로 → 단위 id (파일명으로 쓸 수 있게 구분자를 `__` 로)."""
    for r in roots:
        try:
            rel = path.resolve().relative_to(r.resolve())
            return "__".join(rel.parts)
        except ValueError:
            continue
    raise ValueError(f"source {path} is outside known roots {roots}")


# ── 단위 ────────────────────────────────────────────────────────────────────────

@dataclass
class SourceUnit:
    uid: str
    text: str
    origin: str = ""
    sha256: str = ""
    decls: list = field(default_factory=list)            # [(kind, name)]
    owned: set = field(default_factory=set)              # 이 단위가 선언하는 모든 이름 (선언 + 파일 수준 + 별칭)
    idents: set = field(default_factory=set)
    deps: set = field(default_factory=set)               # 직접 의존 단위 id (어휘)
    closure: set = field(default_factory=set)            # 의존 폐포 (자기 제외)
    # 분석 결과 (마지막 분석)
    cfgs: dict = field(default_factory=dict)             # 선언 이름 → CFG
    file_structs: dict = field(default_factory=dict)
    file_enums: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    code_lines: dict = field(default_factory=dict)
    unresolved: dict = field(default_factory=dict)       # 이름 → [메시지]
    errors: list = field(default_factory=list)
    fingerprint: str | None = None
    version: int = 0
    seen: dict = field(default_factory=dict)             # 분석 시점의 의존 단위 버전
    analyses: int = 0
    collision: list = field(default_factory=list)
    reanalyzed: bool = False                             # 이번 빌드에서 분석했나 (아니면 이전 빌드 결과를 그대로 씀)

    @property
    def status(self) -> str:
        if self.collision:
            return "Invalid"
        if self.unresolved:
            return "Pending"
        return "Invalid" if self.errors else "Complete"


def _cfg_name(cfg) -> str:
    for a in ("contract_name", "library_name", "interface_name", "name"):
        v = getattr(cfg, a, None)
        if isinstance(v, str):
            return v
    raise TypeError(f"CFG without name: {type(cfg).__name__}")


def _is_interface(cfg) -> bool:
    return isinstance(cfg, InterfaceCFG) or bool(getattr(cfg, "is_interface", False))


# ── 분석 ────────────────────────────────────────────────────────────────────────

def _inject(sa, ca, units: list[SourceUnit], *, mark_dependency: bool = True) -> None:
    """의존 단위들의 결과를 분석기에 넣는다 (빌드와 타깃 로드가 같은 규칙을 쓴다)."""
    for u in units:
        for name, cfg in u.cfgs.items():
            if mark_dependency:
                cfg.from_dependency = True
            ca.contract_cfgs[name] = cfg
            if isinstance(cfg, LibraryCFG):
                ca.library_cfgs[name] = cfg
            if _is_interface(cfg):
                ca.interface_names.add(name)
        sa.file_level_structs.update(u.file_structs)
        sa.file_level_enums.update(u.file_enums)
        sa.type_aliases.update(u.aliases)


def analyze_unit(unit: SourceUnit, dep_units: list[SourceUnit], log=print) -> None:
    """unit 을 새 분석기로 한 번 분석해 결과 필드를 채운다."""
    from Analyzer.SolidityAnalyzer import SolidityAnalyzer
    from Analyzer.EnhancedSolidityVisitor import EnhancedSolidityVisitor
    from Analyzer.EnhancedYulVisitor import EnhancedYulVisitor
    from Utils.Helper import ParserHelpers
    from Utils.YulHelper import YulParserHelpers
    from Dependencies.main import slice_solidity

    sa = SolidityAnalyzer()
    ca = sa.contract_analyzer
    _inject(sa, ca, dep_units)
    pre = {"structs": set(sa.file_level_structs), "enums": set(sa.file_level_enums), "aliases": set(sa.type_aliases)}

    unresolved: dict[str, list[str]] = {}
    errors: list[str] = []

    def record(where: str, line: int, ex: Exception):
        msg = f"L{line} {where}: {ex}"
        if isinstance(ex, UnresolvedName):
            unresolved.setdefault(ex.name, []).append(msg)
        else:
            errors.append(msg)
        log(f"  [{'UNRESOLVED' if isinstance(ex, UnresolvedName) else 'ERR'}] {msg}")

    try:
        records = slice_solidity(unit.text)
    except Exception as ex:                              # 입력 전제 위반 — 이 단위만 Invalid (빌드 전체를 멈추지 않는다)
        records = []
        record("slice", 0, ex)
    for rec in records:
        code, s, e, ev = rec["code"], rec["startLine"], rec["endLine"], rec["event"]
        try:
            sa.update_code(s, e, code, ev, rec.get("closeBefore", False))
        except Exception as ex:
            record("update_code", s, ex)
        stripped = code.strip()
        if stripped and not stripped.startswith("// @"):
            ctx = ca.get_current_context_type()
            try:
                if ctx == "assembly":
                    EnhancedYulVisitor(ca).visit(YulParserHelpers.generate_parse_tree(code))
                else:
                    EnhancedSolidityVisitor(ca).visit(ParserHelpers.generate_parse_tree(code, ctx, True))
            except Exception as ex:
                record(f"ctx={ctx}", s, ex)

    cfgs = {}
    for kind, name in unit.decls:
        cfg = ca.library_cfgs.get(name) if kind == "library" else ca.contract_cfgs.get(name)
        if cfg is None or getattr(cfg, "from_dependency", False):
            errors.append(f"'{kind} {name}' 를 선언했는데 분석 결과에 없다")
            continue
        if (kind == "library") != isinstance(cfg, LibraryCFG) or (kind == "interface") != _is_interface(cfg):
            errors.append(f"'{kind} {name}' 의 분석 결과 종류가 다르다: {type(cfg).__name__}")
            continue
        cfgs[name] = cfg

    unit.cfgs = cfgs
    unit.file_structs = {k: v for k, v in sa.file_level_structs.items() if k not in pre["structs"]}
    unit.file_enums = {k: v for k, v in sa.file_level_enums.items() if k not in pre["enums"]}
    unit.aliases = {k: v for k, v in sa.type_aliases.items() if k not in pre["aliases"]}
    unit.code_lines = dict(sa.full_code_lines)
    unit.unresolved = unresolved
    unit.errors = errors
    unit.analyses += 1
    unit.reanalyzed = True


def _fingerprint(u: SourceUnit) -> str:
    """다른 단위가 보는 결과의 요약. 바뀌면 의존하는 단위를 다시 분석한다."""
    def members(d):
        m = getattr(d, "members", None)
        return tuple(m.keys()) if isinstance(m, dict) else tuple(m or ())

    parts = [sorted(u.unresolved), sorted(u.errors)]
    for name in sorted(u.cfgs):
        c = u.cfgs[name]
        svn = getattr(c, "state_variable_node", None)
        funcs = []
        for fn, sub in sorted((getattr(c, "functions", None) or {}).items()):
            funcs.append((fn, tuple(sorted(sub)) if isinstance(sub, dict) else ("1",)))
        using = sorted((t, tuple(_cfg_name(l) for l in (ls if isinstance(ls, list) else [ls])))
                       for t, ls in (getattr(c, "using_libraries", None) or {}).items())
        parts.append((
            name, type(c).__name__, _is_interface(c), tuple(getattr(c, "parent_contracts", None) or ()),
            tuple(sorted((k, members(v)) for k, v in (getattr(c, "structDefs", None) or {}).items())),
            tuple(sorted((k, members(v)) for k, v in (getattr(c, "enumDefs", None) or {}).items())),
            tuple(sorted(svn.variables)) if svn is not None else (),
            tuple(funcs), tuple(sorted(getattr(c, "events", None) or {})), tuple(using),
            tuple(_cfg_name(l) for l in (getattr(c, "using_all_libraries", None) or ())),
            tuple(a for a in ("constructor", "fallback", "receive") if getattr(c, a, None) is not None),
        ))
    parts.append(tuple(sorted((k, members(v)) for k, v in u.file_structs.items())))
    parts.append(tuple(sorted((k, tuple(v)) for k, v in u.file_enums.items())))
    parts.append(tuple(sorted(u.aliases.items())))
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def _sccs(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan — 의존 대상 SCC 가 먼저 나온다(역위상 = 분석 순서). 노드 순서는 결정적."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = [0]
    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))

    def visit(v):
        index[v] = low[v] = counter[0]; counter[0] += 1
        stack.append(v); on.add(v)
        for w in sorted(edges.get(v, ())):
            if w not in index:
                visit(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            out.append(sorted(comp))

    for v in nodes:
        if v not in index:
            visit(v)
    return out


class ProjectRegistry:
    def __init__(self, units: list[SourceUnit]):
        self.units: dict[str, SourceUnit] = {}
        for u in units:
            if u.uid in self.units:
                raise ValueError(f"duplicate unit id {u.uid}")
            self.units[u.uid] = u
        self.owner: dict[str, str] = {}
        self.collisions: dict[str, list[str]] = {}
        self.rounds = 0

    # 1) 선언 이름 · 의존
    def index(self) -> None:
        owners: dict[str, list[str]] = {}
        self.owner, self.collisions = {}, {}
        for u in self.units.values():
            u.collision = []
            sc = scan_unit(u.text)
            u.sha256 = hashlib.sha256(u.text.encode("utf-8")).hexdigest()
            u.decls = sc["decls"]
            u.owned = {n for _, n in sc["decls"]} | {n for _, n in sc["file_level"]} | set(sc["aliases"])
            u.idents = sc["idents"]
            for n in u.owned:
                owners.setdefault(n, []).append(u.uid)
        for n, us in owners.items():
            if len(set(us)) > 1:
                self.collisions[n] = sorted(set(us))
                for uid in set(us):
                    self.units[uid].collision.append(f"이름 '{n}' 을 여러 단위가 선언: {sorted(set(us))}")
            else:
                self.owner[n] = us[0]
        for u in self.units.values():
            u.deps = {self.owner[n] for n in u.idents if n in self.owner and self.owner[n] != u.uid}
        for u in self.units.values():
            seen, todo = set(), list(u.deps)
            while todo:
                w = todo.pop()
                if w in seen:
                    continue
                seen.add(w)
                todo.extend(self.units[w].deps)
            u.closure = seen - {u.uid}

    # 2) 고정점 분석
    def build(self, log=print) -> None:
        self.index()
        self._fixpoint(set(self.units), log)

    def _fixpoint(self, initial: set, log=print) -> None:
        """initial 단위부터 분석하고, 의존 폐포 중 어느 단위의 지문이 바뀌면 다시 분석한다(SCC 역위상 순서)."""
        order = [uid for comp in _sccs(sorted(self.units), {k: v.deps for k, v in self.units.items()}) for uid in comp]
        queue = [uid for uid in order if uid in initial]
        limit = len(order) + MAX_ROUNDS_SLACK
        while queue:
            self.rounds += 1
            if self.rounds > limit:
                raise RuntimeError(f"레지스트리 고정점이 {limit} 라운드 안에 수렴하지 않음: {queue}")
            log(f"\n[round {self.rounds}] {len(queue)} units")
            for uid in queue:
                u = self.units[uid]
                log(f"\n{'=' * 60}\n[unit] {uid}  decls={[n for _, n in u.decls]}\n{'=' * 60}")
                analyze_unit(u, [self.units[w] for w in sorted(u.closure)], log=log)
                fp = _fingerprint(u)
                if fp != u.fingerprint:
                    u.fingerprint = fp
                    u.version += 1
                u.seen = {w: self.units[w].version for w in u.closure}
            queue = [uid for uid in order
                     if any(self.units[w].version != self.units[uid].seen.get(w) for w in self.units[uid].closure)]

    # 3) 저장
    def save(self, root: pathlib.Path) -> pathlib.Path:
        builds = root / "builds"
        builds.mkdir(parents=True, exist_ok=True)
        n = 1 + max([int(p.name.split("_")[1]) for p in builds.glob("build_*") if p.name.split("_")[1].isdigit()] or [0])
        bdir = builds / f"build_{n:03d}"
        (bdir / "units").mkdir(parents=True)
        manifest = {"build": bdir.name, "created": datetime.datetime.now().isoformat(timespec="seconds"),
                    "rounds": self.rounds, "collisions": self.collisions, "units": {}}
        for uid, u in sorted(self.units.items()):
            classes = {}
            if u.status != "Invalid" or u.cfgs:
                if u.reanalyzed:                         # 이전 빌드에서 가져온 단위는 이미 구워져 있다
                    _bake(u)
                classes = _write_unit(bdir / "units" / f"{uid}.pkl", u, self.owner)
            manifest["units"][uid] = {
                "origin": u.origin, "sha256": u.sha256, "status": u.status, "fingerprint": u.fingerprint,
                "decls": [[k, n, classes.get(n)] for k, n in u.decls],
                "file_structs": sorted(u.file_structs), "file_enums": sorted(u.file_enums), "aliases": sorted(u.aliases),
                "deps": sorted(u.deps), "unresolved": {k: v[:3] for k, v in sorted(u.unresolved.items())},
                "errors": u.errors, "collision": u.collision, "analyses": u.analyses,
                "reanalyzed": u.reanalyzed,
            }
        (bdir / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        (root / "CURRENT").write_text(bdir.name, encoding="utf-8")
        return bdir


    # 4) 증분 (R4) — 현재 빌드 위에 소스 추가·교체·삭제
    @classmethod
    def from_build(cls, root: pathlib.Path) -> "ProjectRegistry":
        """현재 빌드(CURRENT)를 단위 결과까지 복원한다 — 바뀌지 않은 단위는 다시 분석하지 않기 위해서."""
        bdir, manifest = current_build(root)
        units = []
        for uid, m in sorted(manifest["units"].items()):
            src = root / "sources" / f"{uid}.sol"
            text = src.read_text(encoding="utf-8")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != m["sha256"]:
                raise ValueError(f"{uid}: sources/ 의 내용이 빌드 {manifest['build']} 의 것과 다르다 — 빌드 밖에서 바뀌었다")
            units.append(SourceUnit(uid=uid, text=text, origin=m.get("origin", "")))
        reg = cls(units)
        reg.index()
        shells = _load_shells(bdir, manifest, sorted(manifest["units"]))
        for uid, m in manifest["units"].items():
            u = reg.units[uid]
            payload = shells["payloads"].get(uid)
            u.cfgs = {n: shells["objs"][n] for _, n, spec in m["decls"] if spec is not None}
            if payload is not None:
                u.file_structs, u.file_enums, u.aliases = (payload["file_structs"], payload["file_enums"],
                                                           payload["aliases"])
            u.unresolved = {k: list(v) for k, v in m["unresolved"].items()}
            u.errors = list(m["errors"])
            u.fingerprint = m.get("fingerprint")
            u.version = 1
        for u in reg.units.values():
            u.seen = {w: 1 for w in u.closure}
        return reg

    def apply(self, changes: dict, log=print) -> set:
        """changes = {단위 id: 새 텍스트 | None(삭제)}. 영향받는 단위만 다시 분석한다. 다시 분석한 단위 id 를 돌려준다.
        영향 = 바뀐 단위, 바뀐 단위를 (변경 전 또는 후) 의존 폐포에 가진 단위, 의존 집합이 달라진 단위
        (예: 새 소스가 미해석 이름을 선언 → 그 이름을 쓰던 단위)."""
        before = {uid: (set(u.deps), set(u.closure)) for uid, u in self.units.items()}
        for uid, text in changes.items():
            if text is None:
                if uid not in self.units:
                    raise KeyError(f"삭제할 단위 {uid} 가 없다")
                del self.units[uid]
            elif uid in self.units:
                self.units[uid].text = text
            else:
                self.units[uid] = SourceUnit(uid=uid, text=text)
        self.index()
        changed = set(changes)
        affected = {uid for uid in self.units if uid in changed}
        for uid, u in self.units.items():
            old_deps, old_closure = before.get(uid, (set(), set()))
            if (u.closure | old_closure) & changed or u.deps != old_deps:
                affected.add(uid)
        for uid in affected:                                   # 새로 분석할 단위의 이전 결과는 버린다
            u = self.units[uid]
            u.fingerprint, u.version = None, u.version + 1
        self._fixpoint(affected, log)
        return {uid for uid, u in self.units.items() if u.reanalyzed}


def _load_shells(bdir: pathlib.Path, manifest: dict, uids: list) -> dict:
    """빌드의 단위 pkl 들을 읽어 선언마다 객체 하나로 연결한다. → {"objs": 이름→CFG, "payloads": 단위→payload}"""
    units = manifest["units"]
    objs = {}
    for uid in uids:
        for _kind, name, spec in units[uid]["decls"]:
            if spec is None:
                continue                                 # 분석 결과에 없는 선언 (Invalid) — 참조하면 아래에서 오류
            cls = _class(spec)
            objs[name] = cls.__new__(cls)

    def persistent_load(pid):
        tag, name = pid
        if tag != "decl" or name not in objs:
            raise ValueError(f"의존성 로드: 선언 '{name}' 이 로드 대상(타깃 의존 폐포)에 없다")
        return objs[name]

    payloads = {}
    for uid in uids:
        p = bdir / "units" / f"{uid}.pkl"
        if not p.is_file():
            continue
        up = pickle.Unpickler(io.BytesIO(p.read_bytes()))
        up.persistent_load = persistent_load
        payload = up.load()
        for name, st in payload["cfg_state"].items():
            objs[name].__dict__.update(st)
            objs[name].from_dependency = True
        payloads[uid] = payload
    return {"objs": objs, "payloads": payloads}


def _bake(u: SourceUnit) -> None:
    """(2026-10-22, U3) if/else-if join 의 표시 줄을 노드에 저장 — 세션엔 이 파일 소스가 없으므로 빌드 시점에 계산."""
    from Utils.DisplayLines import join_display_lines
    for cfg in u.cfgs.values():
        fns = []
        for sub in (getattr(cfg, "functions", None) or {}).values():
            fns.extend(sub.values() if isinstance(sub, dict) else [sub])
        fns += [f for f in (getattr(cfg, a, None) for a in ("constructor", "fallback", "receive")) if f is not None]
        for fcfg in fns:
            disp = join_display_lines(fcfg, u.code_lines)
            for nd in fcfg.graph.nodes():
                if nd.uid in disp:
                    nd.display_line = disp[nd.uid]


def _write_unit(path: pathlib.Path, u: SourceUnit, owner: dict) -> dict:
    """단위 pkl. 모든 CFG(자기 것 포함) 참조는 ("decl", 이름) 으로 — 로드 때 선언마다 객체 하나로 연결한다."""
    own = set(u.cfgs)

    class _P(pickle.Pickler):
        def persistent_id(self, o):
            if isinstance(o, _CFG_CLASSES):
                n = _cfg_name(o)
                if n not in own and n not in owner:
                    raise ValueError(f"{u.uid}: 레지스트리에 없는 선언 '{n}' 의 CFG 를 참조한다")
                return ("decl", n)
            if isinstance(o, FunctionCFG):
                ow = getattr(o, "owner_contract", None)
                if ow is not None and ow not in own:
                    raise ValueError(f"{u.uid}: 다른 선언 '{ow}' 의 함수 CFG 를 CFG 를 거치지 않고 직접 참조한다")
            return None

    state = {n: {k: v for k, v in c.__dict__.items() if k != "from_dependency"} for n, c in u.cfgs.items()}
    payload = {"unit": u.uid, "cfg_state": state,
               "file_structs": u.file_structs, "file_enums": u.file_enums, "aliases": u.aliases}
    buf = io.BytesIO()
    _P(buf, protocol=pickle.HIGHEST_PROTOCOL).dump(payload)
    path.write_bytes(buf.getvalue())
    return {n: f"{type(c).__module__}:{type(c).__qualname__}" for n, c in u.cfgs.items()}


# ── 로드 (타깃 세션) ──────────────────────────────────────────────────────────────

def _class(spec: str):
    mod, qual = spec.split(":")
    obj = importlib.import_module(mod)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return obj


def current_build(root: pathlib.Path) -> tuple[pathlib.Path, dict]:
    cur = root / "CURRENT"
    if not cur.is_file():
        raise FileNotFoundError(f"project deps not built (no {cur}) — build_project_deps.py 먼저")
    bdir = root / "builds" / cur.read_text(encoding="utf-8").strip()
    return bdir, json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))


def select_units(manifest: dict, exclude) -> tuple[list[str], str | None]:
    """타깃이 스스로 선언하는 이름(exclude)으로 **타깃 단위**를 찾아 그 의존 폐포만 고른다.
    규칙(2026-10-23 사용자 결정): 타깃 파일은 같은 원본 파일의 단위를 **통째로** 대신한다 — 선언 집합이 다르면 오류."""
    units = manifest["units"]
    exclude = set(exclude or ())
    hit = [uid for uid, m in units.items() if exclude & {n for _, n, _ in m["decls"]}]
    if not hit:
        return sorted(units), None                      # 타깃이 이 프로젝트의 파일이 아니다 — 전체가 의존성
    if len(hit) > 1:
        raise ValueError(f"타깃 선언 {sorted(exclude)} 이 여러 단위에 걸쳐 있다: {hit}")
    tid = hit[0]
    declared = {n for _, n, _ in units[tid]["decls"]}
    if declared != exclude:
        raise ValueError(f"타깃 선언 집합 {sorted(exclude)} 이 단위 {tid} 의 선언 집합 {sorted(declared)} 과 다르다 "
                         f"(contraction 이 선언을 바꿨다)")
    seen, todo = set(), list(units[tid]["deps"])
    while todo:
        w = todo.pop()
        if w in seen:
            continue
        seen.add(w)
        todo.extend(units[w]["deps"])
    # 의존 단위가 타깃 이름을 **언급**만 하는 순환(인터페이스 IA ↔ IB 타입 참조)은 합법이고 객체 참조도 없다.
    # 의존 단위가 타깃 선언의 CFG 객체를 참조하면(예: 타깃을 상속) 로드(`_load_shells`)가 그 이름에서 오류를 낸다.
    seen.discard(tid)
    return sorted(seen), tid


class DependencyNotReady(RuntimeError):
    """(R5) 타깃이 의존하는 단위 중 Complete 가 아닌 것이 있다 — 분석을 시작하지 않는다."""


def load(sa, ca, deps_root, exclude=(), verbose: bool = True, require_complete: bool = REQUIRE_COMPLETE) -> dict:
    root = pathlib.Path(deps_root)
    bdir, manifest = current_build(root)
    uids, target_uid = select_units(manifest, exclude)
    units = manifest["units"]
    not_complete = {u: units[u]["status"] for u in uids if units[u]["status"] != "Complete"}
    if not_complete and require_complete:
        detail = "; ".join(f"{u} = {st} ({(units[u]['errors'] or list(units[u]['unresolved']) or units[u]['collision'])[:1]})"
                           for u, st in sorted(not_complete.items()))
        raise DependencyNotReady(f"의존성 {len(not_complete)}개 단위가 Complete 가 아니라 분석할 수 없다 "
                                 f"({manifest['build']}): {detail}")
    loaded = _load_shells(bdir, manifest, uids)
    shells = loaded["objs"]
    for payload in loaded["payloads"].values():
        sa.file_level_structs.update(payload["file_structs"])
        sa.file_level_enums.update(payload["file_enums"])
        sa.type_aliases.update(payload["aliases"])
    for name, cfg in shells.items():
        ca.contract_cfgs[name] = cfg
        if isinstance(cfg, LibraryCFG):
            ca.library_cfgs[name] = cfg
        if _is_interface(cfg):
            ca.interface_names.add(name)
    report = {"build": manifest["build"], "target_unit": target_uid, "units": uids, "not_complete": not_complete}
    if verbose:
        print(f"[Dependencies] {manifest['build']}: {len(uids)} units, {len(shells)} declarations"
              + (f" (target unit {target_uid})" if target_uid else ""))
        if not_complete:
            print(f"[Dependencies] ⚠ not Complete: {not_complete}")
    return report


# ── CLI ──────────────────────────────────────────────────────────────────────────

def build_root(root: pathlib.Path, origins: dict | None = None) -> pathlib.Path:
    """<root>/sources/*.sol 을 단위로 빌드해 <root>/builds 에 저장."""
    srcs = sorted((root / "sources").glob("*.sol"))
    if not srcs:
        raise FileNotFoundError(f"no sources in {root / 'sources'}")
    units = [SourceUnit(uid=p.stem, text=p.read_text(encoding="utf-8"), origin=(origins or {}).get(p.stem, ""))
             for p in srcs]
    reg = ProjectRegistry(units)
    reg.build()
    bdir = reg.save(root)
    cnt = {}
    for u in reg.units.values():
        cnt[u.status] = cnt.get(u.status, 0) + 1
    print(f"\n[registry] {bdir.name}: rounds={reg.rounds} units={len(units)} {cnt} "
          f"analyses={sum(u.analyses for u in reg.units.values())} collisions={len(reg.collisions)}")
    for u in sorted(reg.units.values(), key=lambda x: x.uid):
        if u.status != "Complete":
            print(f"  {u.status:<8} {u.uid}  unresolved={sorted(u.unresolved)} errors={len(u.errors)} {u.collision}")
    return bdir


def update_root(root: pathlib.Path, add: dict, remove: list) -> pathlib.Path:
    """(R4) 현재 빌드 위에 소스 추가·교체(add = {단위 id: 텍스트})·삭제(remove = [단위 id]) → 새 빌드.
    텍스트는 분석기가 받아들이는 형태(전처리본)여야 한다. sources/ 도 함께 갱신한다."""
    reg = ProjectRegistry.from_build(root)
    changes = {**{uid: t for uid, t in add.items()}, **{uid: None for uid in remove}}
    done = reg.apply(changes)
    for uid, t in add.items():
        (root / "sources" / f"{uid}.sol").write_text(t, encoding="utf-8")
    for uid in remove:
        (root / "sources" / f"{uid}.sol").unlink()
    bdir = reg.save(root)
    print(f"[registry] {bdir.name}: reanalyzed {len(done)} / {len(reg.units)} units: {sorted(done)}")
    return bdir


def main():
    ap = argparse.ArgumentParser(description="프로젝트 의존성 레지스트리 빌드")
    ap.add_argument("--root", required=True, help="deps/<p> (sources/ 를 빌드)")
    ap.add_argument("--add", nargs="*", default=[], help="(R4) 추가·교체할 전처리본 .sol (단위 id = 파일 stem)")
    ap.add_argument("--remove", nargs="*", default=[], help="(R4) 삭제할 단위 id")
    a = ap.parse_args()
    root = pathlib.Path(a.root).resolve()
    if a.add or a.remove:
        update_root(root, {pathlib.Path(f).stem: pathlib.Path(f).read_text(encoding="utf-8") for f in a.add}, a.remove)
        return
    origins_p = root / "sources" / "origins.json"
    origins = json.loads(origins_p.read_text(encoding="utf-8")) if origins_p.is_file() else None
    build_root(root, origins)


if __name__ == "__main__":
    main()
