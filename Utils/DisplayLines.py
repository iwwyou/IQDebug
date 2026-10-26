"""노드 **표시 줄** (2026-10-22, 프론트 탐색 U3 결정).

CFG 노드의 `src_line` 은 빌더가 노드를 만든 줄이다. 대부분은 그대로 화면 줄이지만 **if/else-if 체인의 join** 은
조건 줄(`if_join_4` → 4, `else_if_join_6` → 6)에 만들어지고, line_info 등록도 첫 `} else …` 줄에 된다.
사용자에게 join 은 "분기 블록이 모두 끝난 지점" = **체인을 닫는 `}` 줄**이어야 한다.

계산: join 의 `scope_anchor`(= 그 join 을 만든 if / else-if 조건 노드)의 줄부터 중괄호를 세어 내려가며,
깊이가 0 이 되는 `}` 뒤에 `else` 가 이어지면 체인이 계속되는 것이고, 아니면 그 줄이 체인의 끝이다.

의존성 CFG 는 소스가 세션에 없으므로 **빌드 시점에** 계산해 노드에 `display_line` 으로 저장한다
(`Dependencies/main.analyze_file`). 세션 소스 CFG 는 증분 편집으로 줄이 바뀌므로 직렬화할 때마다 계산한다.
"""
import re

_STR_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _code_part(text: str) -> str:
    """문자열 리터럴·한 줄 주석 제거 (중괄호 오탐 방지)."""
    t = _STR_RE.sub('""', text)
    i = t.find("//")
    return t if i < 0 else t[:i]


def chain_end_line(code_lines: dict, cond_line: int) -> int:
    """if / else-if 조건 줄에서 시작하는 체인을 닫는 `}` 의 줄 번호."""
    first = _code_part(code_lines.get(cond_line, ""))
    m = re.search(r"\bif\b", first)
    if m is None:
        raise ValueError(f"line {cond_line}: not an if/else-if header: {first.strip()!r}")
    depth = 0
    opened = False
    ln = cond_line
    text = first[m.start():]          # `} else if (...) {` 의 앞쪽 `}` 는 이전 블록을 닫는 것 — 제외
    last = max(code_lines) if code_lines else cond_line
    while ln <= last:
        for i, ch in enumerate(text):
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth == 0:
                    if re.match(r"\s*else\b", text[i + 1:]):
                        continue      # `} else …` — 체인이 이어진다
                    return ln
        ln += 1
        text = _code_part(code_lines.get(ln, ""))
    raise ValueError(f"line {cond_line}: if-chain is not closed")


def _is_if_anchor(n) -> bool:
    return (getattr(n, "condition_node", False)
            and getattr(n, "condition_node_type", None) in ("if", "else if", "else_if"))


def join_display_lines(fcfg, code_lines: dict | None) -> dict:
    """{uid: 표시 줄} — if/else-if join 만. 다른 노드는 src_line 이 표시 줄이다.

    노드에 빌드 시점 값(`display_line`, 의존성 CFG)이 있으면 그것을 쓴다. 없으면 code_lines 로 계산하고,
    code_lines 도 없으면 오류(조용히 src_line 으로 대체하지 않는다)."""
    out: dict = {}
    for n in fcfg.graph.nodes():
        if not getattr(n, "join_point_node", False):
            continue
        anchor = getattr(n, "scope_anchor", None)
        if anchor is None or not _is_if_anchor(anchor):
            continue
        pre = getattr(n, "display_line", None)
        if pre is not None:
            out[n.uid] = pre
            continue
        if code_lines is None:
            raise ValueError(f"join '{n.name}': no source lines and no build-time display_line")
        out[n.uid] = chain_end_line(code_lines, anchor.src_line)
    return out
