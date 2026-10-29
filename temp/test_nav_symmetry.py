# -*- coding: utf-8 -*-
"""탐색 그래프 NG 의 회귀 가드 (2026-10-29, 라운드10 P0).

`frontend/src/debug/navGraph.ts` 의 규칙을 그대로 이식해 다음을 단언한다.

  S1 대칭성      m in Succ(n)  <=>  n in Pred(m)
  S2 줄 단사성   한 소스 줄의 착지 노드는 최대 1개
  S3 frontier    land closure 가 통과하는 비착지 노드는 out-degree <= 1
                 (`landFwd` 가 예외를 던지는 조건 — 깨지면 UI 가 죽는다)
  S4 탈출 요약   루프 헤더의 Succ 은 `loop_exits` 의 feasible 경로에서만 나온다

왜 필요한가: 예전에는 forward 와 backward 가 서로 다른 규칙을 썼다(forward 는 루프 본문에
들어가지 않는데 backward 는 `loop_body` 를 통과해 나왔다) → 루프 본문 안 `return` 에서
`Pred != Succ^-1`. navEdges 단일 관계로 바꿔 대칭이 구성상 참이 됐고, 이 스크립트가 그
보장을 지킨다.

실행:  PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe temp\\test_nav_symmetry.py
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Dependencies.main import slice_solidity  # noqa: E402
import engine_api  # noqa: E402

LOOP_CONDS = ("while", "for", "do_while")
BOTTOM = "⊥"


def has(n, f):
    return f in n.flags


def is_loop_head(n):
    return has(n, "condition") and (n.cond_type or "") in LOOP_CONDS


class NG:
    """navGraph.ts 의 이식 — 규칙을 바꿀 때 반드시 양쪽을 같이 고친다."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.by_id = {n.id: n for n in cfg.nodes}
        self.succ_raw = {}
        self.pred_raw = {}
        for e in cfg.edges:
            if e.kind == "back_edge":        # fixpoint 로 흡수 — 탐색에서 숨긴다
                continue
            self.succ_raw.setdefault(e.from_, []).append(e)
            self.pred_raw.setdefault(e.to, []).append(e)
        self._edges = None

    # ── 원시 전방 간선 (루프 헤더는 false 간선만) ─────────────────────────
    def fwd_edges(self, nid):
        es = self.succ_raw.get(nid, [])
        if is_loop_head(self.by_id[nid]):
            return [e for e in es if e.kind == "false"]
        return es

    def is_dead(self, nid):
        if not nid or nid == self.cfg.error_exit:
            return True
        n = self.by_id.get(nid)
        if n is None:
            return True
        env = n.in_state if n.in_state else n.state
        return any(BOTTOM in v for v in env.values())

    def is_collapsed_join(self, n):
        if not has(n, "merge"):
            return False
        out = self.fwd_edges(n.id)
        return len(out) == 1 and has(self.by_id[out[0].to], "merge")

    def is_landable(self, n):
        if n is None or n.id == self.cfg.entry:
            return False
        if has(n, "exit"):
            return n.id == self.cfg.exit
        if has(n, "fixpoint_evaluation") or has(n, "loop_body"):
            return False
        if has(n, "loop_init") or has(n, "loop_incr"):      # K1
            return False
        if (has(n, "branch_then") or has(n, "branch_else")) and not has(n, "loop_exit"):
            return False
        if self.is_collapsed_join(n):
            return False
        return True

    def land_fwd(self, first, seen=None):
        """한 간선을 따라가 처음 만나는 착지 노드. S3 위반이면 RuntimeError."""
        seen = set() if seen is None else seen
        cur = first
        while cur and cur not in seen:
            seen.add(cur)
            if cur == self.cfg.return_exit:
                return self.cfg.exit
            if self.is_dead(cur):
                return None
            n = self.by_id.get(cur)
            if self.is_landable(n):
                return cur
            out = [e for e in self.fwd_edges(cur) if not self.is_dead(e.to)]
            if len(out) > 1:                                # S3
                label = n.name if n is not None else cur
                raise RuntimeError("S3: non-landable '%s' has %d successors" % (label, len(out)))
            cur = out[0].to if out else None
        return None

    # ── NG 간선 (단일 관계) ──────────────────────────────────────────────
    def _loop_exit_edges(self, h):
        les = h.loop_exits
        if not les:
            return None
        out = []
        for le in les:
            if not le.feasible or le.kind == "revert":
                continue
            target = self.cfg.exit if le.to == self.cfg.return_exit else self.land_fwd(le.to)
            if not target:
                continue
            if le.kind == "break":
                kind = "loop_exit_break"
            elif le.kind == "return":
                kind = "loop_exit_return"
            else:
                kind = "loop_exit_normal"
            out.append((h.id, target, kind, le.from_))
        return out

    def edges(self):
        if self._edges is not None:
            return self._edges
        es = []
        for n in self.cfg.nodes:
            if n.id != self.cfg.entry and not self.is_landable(n):
                continue
            if n.id != self.cfg.entry and self.is_dead(n.id):
                continue
            if is_loop_head(n):
                le = self._loop_exit_edges(n)
                if le is not None:
                    es.extend(le)
                    continue          # 요약이 있으면 그것이 유일한 출구 정의
            for e in self.fwd_edges(n.id):
                landed = self.land_fwd(e.to)
                if landed:
                    es.append((n.id, landed, e.kind, None))
        self._edges = es
        return es

    def succ(self, nid):
        return list(dict.fromkeys(t for f, t, _k, _v in self.edges() if f == nid))

    def pred(self, nid):
        srcs = dict.fromkeys(f for f, t, _k, _v in self.edges() if t == nid)
        return [f for f in srcs if f != self.cfg.entry]

    def landables(self):
        return [n.id for n in self.cfg.nodes
                if self.is_landable(n) and not self.is_dead(n.id)]

    def label(self, uid):
        n = self.by_id.get(uid)
        return str(n.name) if n is not None else uid[:8]


# ═══════════════════════════ 케이스 ═══════════════════════════
def wrap(body, returns=False):
    sig = ("function f(uint256 n) public returns (uint256)" if returns
           else "function f(uint256 n) public")
    return ("contract C {\n"
            "    uint256 public total;\n"
            "    " + sig + " {\n"
            "        uint256 s = 0;\n"
            "        " + body + "\n"
            "    }\n"
            "}")


CASES = {
    "straight line": wrap(
        "s = s + 1;\n"
        "        total = s;"),
    "if/else": wrap(
        "if (n > 8) {\n"
        "            s = 1;\n"
        "        } else {\n"
        "            s = 2;\n"
        "        }\n"
        "        total = s;"),
    "if/else-if/else": wrap(
        "if (n > 8) {\n"
        "            s = 1;\n"
        "        } else if (n > 5) {\n"
        "            s = 2;\n"
        "        } else {\n"
        "            s = 3;\n"
        "        }\n"
        "        total = s;"),
    "if, no else": wrap(
        "if (n > 8) {\n"
        "            s = 1;\n"
        "        }\n"
        "        total = s;"),
    "nested if": wrap(
        "if (n > 8) {\n"
        "            if (n > 9) {\n"
        "                s = 1;\n"
        "            }\n"
        "        }\n"
        "        total = s;"),
    "require as branch tail": wrap(
        "if (n > 8) {\n"
        "            require(n < 3);\n"
        "        } else {\n"
        "            s = 2;\n"
        "        }\n"
        "        total = s;"),
    "while": wrap(
        "while (s < 3) {\n"
        "            s = s + 1;\n"
        "        }\n"
        "        total = s;"),
    "while + break": wrap(
        "while (s < 3) {\n"
        "            if (n > 5) {\n"
        "                break;\n"
        "            }\n"
        "            s = s + 1;\n"
        "        }\n"
        "        total = s;"),
    "while + continue": wrap(
        "while (s < 3) {\n"
        "            if (n > 5) {\n"
        "                continue;\n"
        "            }\n"
        "            s = s + 1;\n"
        "        }\n"
        "        total = s;"),
    "while(true) + break": wrap(
        "while (true) {\n"
        "            if (n > 5) {\n"
        "                break;\n"
        "            }\n"
        "            s = s + 1;\n"
        "        }\n"
        "        total = s;"),
    "while + require": wrap(
        "while (s < 3) {\n"
        "            require(n > 5);\n"
        "            s = s + 1;\n"
        "        }\n"
        "        total = s;"),
    "for": wrap(
        "for (uint256 j = 0; j < 5; j = j + 1) {\n"
        "            s = s + 1;\n"
        "        }\n"
        "        total = s;"),
    "nested loops": wrap(
        "for (uint256 j = 0; j < 3; j = j + 1) {\n"
        "            while (s < 2) {\n"
        "                s = s + 1;\n"
        "            }\n"
        "        }\n"
        "        total = s;"),
    "return in loop body": wrap(
        "while (s < 3) {\n"
        "            if (n > 5) {\n"
        "                return n;\n"
        "            }\n"
        "            s = s + 1;\n"
        "        }\n"
        "        return s;", returns=True),
    "return after loop": wrap(
        "while (s < 3) {\n"
        "            s = s + 1;\n"
        "        }\n"
        "        return s;", returns=True),
    "loop inside if": wrap(
        "if (n > 8) {\n"
        "            while (s < 3) {\n"
        "                s = s + 1;\n"
        "            }\n"
        "        }\n"
        "        total = s;"),
}


# ── try/catch (2026-10-29) ────────────────────────────────────────────────────
# 별도 빌더: `try` 는 **외부 호출**에만 쓸 수 있어 호출 대상 함수가 필요하다.
# `h` 를 `f` 보다 앞에 두므로 스코프를 명시해 분석한다(CLAUDE.md: 스코프 없는 fallback 은
# 컨트랙트의 마지막 함수를 집는다).
def wrap_try(body):
    return ("contract C {\n"
            "    uint256 public total;\n"
            "    function h(uint256 x) external returns (uint256) {\n"
            "        return x + 1;\n"
            "    }\n"
            "    function f(uint256 n) public {\n"
            "        uint256 s = 0;\n"
            "        " + body + "\n"
            "    }\n"
            "}")


TRY_CASES = {
    "try/catch": wrap_try(
        "try this.h(n) returns (uint256 r) {\n"
        "            s = r;\n"
        "        } catch {\n"
        "            s = 99;\n"
        "        }\n"
        "        total = s;"),
    "try/catch typed": wrap_try(
        "try this.h(n) returns (uint256 r) {\n"
        "            s = r;\n"
        "        } catch Error(string memory reason) {\n"
        "            s = 99;\n"
        "        }\n"
        "        total = s;"),
    "try/catch 2 clauses": wrap_try(
        "try this.h(n) returns (uint256 r) {\n"
        "            s = r;\n"
        "        } catch Error(string memory reason) {\n"
        "            s = 97;\n"
        "        } catch (bytes memory d) {\n"
        "            s = 98;\n"
        "        }\n"
        "        total = s;"),
    "try/catch 3 clauses": wrap_try(
        "try this.h(n) returns (uint256 r) {\n"
        "            s = r;\n"
        "        } catch Error(string memory reason) {\n"
        "            s = 96;\n"
        "        } catch Panic(uint256 code) {\n"
        "            s = 97;\n"
        "        } catch (bytes memory d) {\n"
        "            s = 98;\n"
        "        }\n"
        "        total = s;"),
    "try/catch, empty catch body": wrap_try(
        "try this.h(n) returns (uint256 r) {\n"
        "            s = r;\n"
        "        } catch {\n"
        "        }\n"
        "        total = s;"),
    "try/catch inside if": wrap_try(
        "if (n > 5) {\n"
        "            try this.h(n) returns (uint256 r) {\n"
        "                s = r;\n"
        "            } catch {\n"
        "                s = 99;\n"
        "            }\n"
        "        }\n"
        "        total = s;"),
    "try/catch inside loop": wrap_try(
        "while (s < 3) {\n"
        "            try this.h(n) returns (uint256 r) {\n"
        "                s = s + 1;\n"
        "            } catch {\n"
        "                s = 99;\n"
        "            }\n"
        "        }\n"
        "        total = s;"),
    "catch body has if": wrap_try(
        "try this.h(n) returns (uint256 r) {\n"
        "            s = r;\n"
        "        } catch {\n"
        "            if (n > 5) {\n"
        "                s = 98;\n"
        "            }\n"
        "        }\n"
        "        total = s;"),
    "catch body returns": wrap_try(
        "try this.h(n) returns (uint256 r) {\n"
        "            s = r;\n"
        "        } catch {\n"
        "            return;\n"
        "        }\n"
        "        total = s;"),
}

CASES.update(TRY_CASES)
SCOPED = set(TRY_CASES)          # 스코프를 명시해 분석해야 하는 케이스

INPUT = {"n": {"t": "interval", "lo": "0", "hi": "10"}}


def analyze(src, scoped=False):
    kw = {"contract": "C", "func": "f"} if scoped else {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sess = engine_api.EngineSession()
        sess.analyze(slice_solidity(src), **kw)
        ev = sess.evaluate(INPUT, **kw)
    return ev.cfg


def check(label, cfg):
    """한 CFG 에 대해 S1~S4 를 검사하고 실패 메시지 목록을 돌려준다."""
    ng = NG(cfg)
    bad = []

    try:
        land = ng.landables()
        ng.edges()
    except RuntimeError as e:                       # S3
        return [str(e)]

    # S1 대칭성
    for a in land:
        for b in ng.succ(a):
            if a not in ng.pred(b):
                bad.append("S1: %s in Succ -> %s, but %s not in Pred(%s)"
                           % (ng.label(a), ng.label(b), ng.label(a), ng.label(b)))
    for b in land:
        for a in ng.pred(b):
            if b not in ng.succ(a):
                bad.append("S1: %s in Pred(%s), but %s not in Succ(%s)"
                           % (ng.label(a), ng.label(b), ng.label(b), ng.label(a)))

    # S2 줄 단사성
    by_line = {}
    for n in cfg.nodes:
        if n.display_line >= 0 and ng.is_landable(n):
            by_line.setdefault(n.display_line, []).append(n)
    for ln in sorted(by_line):
        ns = by_line[ln]
        if len(ns) > 1:
            bad.append("S2: L%d has %d landing nodes - %s"
                       % (ln, len(ns), [str(x.name) for x in ns]))

    # S4 탈출 요약
    for n in cfg.nodes:
        if not (is_loop_head(n) and n.loop_exits):
            continue
        allowed = set()
        for le in n.loop_exits:
            if le.feasible and le.kind != "revert":
                allowed.add(cfg.exit if le.to == cfg.return_exit else le.to)
        for t in ng.succ(n.id):
            if t not in allowed:
                bad.append("S4: loop head %s has Succ %s not in its exit summary"
                           % (ng.label(n.id), ng.label(t)))
    return bad


def main():
    fails = []
    for label, src in CASES.items():
        try:
            cfg = analyze(src, scoped=label in SCOPED)
        except Exception as e:          # 분석 실패는 이 테스트의 대상이 아니다 — 드러내고 넘긴다
            msg = "analyze failed: %s: %s" % (type(e).__name__, str(e)[:120])
            fails.append("[%s] %s" % (label, msg))
            print("  %-24s ANALYZE-FAIL  %s" % (label, msg))
            continue

        bad = check(label, cfg)
        print("  %-24s %s" % (label, "OK" if not bad else "FAIL"))
        for b in bad:
            print("      " + b)
        fails += ["[%s] %s" % (label, b) for b in bad]

    print()
    if fails:
        print("FAILED - %d" % len(fails))
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("PASSED - %d cases, S1/S2/S3/S4 all green" % len(CASES))


if __name__ == "__main__":
    main()
