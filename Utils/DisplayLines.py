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


def chain_end_line(code_lines: dict, cond_line: int,
                   head_kw: str = "if", cont_kw: str = "else") -> int:
    """조건 체인을 닫는 `}` 의 줄 번호.

    (2026-10-29) `(head_kw, cont_kw)` 로 일반화했다 — if 체인은 `("if", "else")`,
    try/catch 체인은 `("try", "catch")`. 중괄호를 세는 로직은 같고 "체인이 이어지는가" 판정만
    다르다(`} else …` / `} catch …`). 예전엔 if 전용이라 try join 이 이 재계산을 **건너뛰고**
    `src_line`(= `} catch {` 줄)을 그대로 썼다 → 그 줄에 착지 노드가 2~3개가 되고(줄 단사성
    위반) 체인을 닫는 `}` 줄에는 하나도 없었다.
    """
    first = _code_part(code_lines.get(cond_line, ""))
    m = re.search(r"\b%s\b" % re.escape(head_kw), first)
    if m is None:
        raise ValueError(f"line {cond_line}: not a {head_kw} header: {first.strip()!r}")
    depth = 0
    opened = False
    ln = cond_line
    text = first[m.start():]          # `} else if (...) {` 의 앞쪽 `}` 는 이전 블록을 닫는 것 — 제외
    last = max(code_lines) if code_lines else cond_line
    cont_re = re.compile(r"\s*%s\b" % re.escape(cont_kw))
    while ln <= last:
        for i, ch in enumerate(text):
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth == 0:
                    if cont_re.match(text[i + 1:]):
                        continue      # `} else …` / `} catch …` — 체인이 이어진다
                    return ln
        ln += 1
        text = _code_part(code_lines.get(ln, ""))
    raise ValueError(f"line {cond_line}: {head_kw}-chain is not closed")


#  표시 줄을 재계산할 join 의 anchor 종류 → (그 anchor **자기 줄**에서 찾을 키워드, 체인 연속 키워드)
#  머리 키워드는 anchor 가 놓인 줄에서 찾는다 — `} else if (…) {` 줄에는 `if` 가, `} catch … {`
#  줄에는 `catch` 가 있다. 그래서 중간 링크와 체인 머리의 키워드가 다르다.
_CHAIN_KW = {
    "if": ("if", "else"), "else if": ("if", "else"), "else_if": ("if", "else"),
    "try": ("try", "catch"), "catch": ("catch", "catch"),
}


def _chain_kw(n):
    """이 anchor 가 조건 체인의 조건 노드면 `(머리, 연속)` 키워드, 아니면 None."""
    if not getattr(n, "condition_node", False):
        return None
    return _CHAIN_KW.get(getattr(n, "condition_node_type", None))


def join_display_lines(fcfg, code_lines: dict | None) -> dict:
    """{uid: 표시 줄} — if/else-if join 만. 다른 노드는 src_line 이 표시 줄이다.

    노드에 빌드 시점 값(`display_line`, 의존성 CFG)이 있으면 그것을 쓴다. 없으면 code_lines 로 계산하고,
    code_lines 도 없으면 오류(조용히 src_line 으로 대체하지 않는다)."""
    out: dict = {}
    for n in fcfg.graph.nodes():
        if not getattr(n, "join_point_node", False):
            continue
        anchor = getattr(n, "scope_anchor", None)
        kw = _chain_kw(anchor)
        if kw is None:
            continue
        pre = getattr(n, "display_line", None)
        if pre is not None:
            out[n.uid] = pre
            continue
        if code_lines is None:
            raise ValueError(f"join '{n.name}': no source lines and no build-time display_line")
        out[n.uid] = chain_end_line(code_lines, anchor.src_line, *kw)
    return out
