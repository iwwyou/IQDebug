# -*- coding: utf-8 -*-
"""
데이터셋 소스 정규화 (2026-11-06, 사용자 승인) — 엔진의 입력 전제에 맞추되 의미는 바꾸지 않는다.

  1. 주석을 공백으로 비운다 (줄 번호 유지).
  2. 중괄호 없는 제어문 본문을 `{ }` 로 감싼다. `if (x) return;` → `if (x) {` ⏎ `return;` ⏎ `}`

줄 단위가 아니라 **토큰 + 문장 경계 추적**으로 동작한다. 그래서 다음을 구조적으로 구분한다.
  - 문자열·주석 안의 키워드와 중괄호
  - `else if` 체인 (`else` 를 감싸지 않는다)
  - do-while 꼬리 `} while (c);` (빈 본문 while 이 아니다)
  - dangling else (`else` 는 가장 가까운 `if` 에 붙는다)
  - assembly 블록 안의 Yul `if`/`for` (건드리지 않는다)

삽입으로 줄이 늘어나므로 `line_map`(정규화 줄 → 원본 줄)을 함께 돌려준다.

사용:
  .venv/Scripts/python.exe evaluation/Dataset/normalize_source.py in.sol -o out.sol
"""
import argparse
import json
import os
import re
from dataclasses import dataclass, field

_TOK = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d[\w.]*|\S")
_CLOSE = {"(": ")", "[": "]", "{": "}"}


def read_source(path: str) -> str:
    """Windows 260자 경로 제한을 피해 읽는다 (DAppSCAN 타깃 29개가 이 제한에 걸린다)."""
    p = os.path.abspath(path)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    with open(p, encoding="utf-8", errors="replace", newline="") as f:
        return f.read().lstrip("﻿")


def blank_comments(src: str) -> tuple[str, str]:
    """(주석만 비운 소스, 주석·문자열 내용을 비운 소스). 둘 다 길이와 줄 구조가 입력과 같다."""
    code, mask = list(src), list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                code[i] = mask[i] = " "
                i += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            while i < j:
                if src[i] != "\n":
                    code[i] = mask[i] = " "
                i += 1
        elif c in "\"'":
            i += 1
            while i < n and src[i] != c and src[i] != "\n":
                if src[i] == "\\" and i + 1 < n:
                    mask[i] = " "
                    i += 1
                mask[i] = " "
                i += 1
            i += 1
        else:
            i += 1
    return "".join(code), "".join(mask)


class _Tokens:
    def __init__(self, masked: str):
        self.items = [(m.group(), m.start(), m.end()) for m in _TOK.finditer(masked)]
        self.n = len(self.items)

    def text(self, i: int) -> str:
        return self.items[i][0] if 0 <= i < self.n else ""

    def match(self, i: int) -> int:
        """여는 괄호 토큰 i 의 짝 인덱스."""
        opener, closer, depth = self.text(i), _CLOSE[self.text(i)], 0
        while i < self.n:
            t = self.text(i)
            if t == opener:
                depth += 1
            elif t == closer:
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        raise ValueError("괄호 짝 없음")

    def _scan_to(self, i: int, tok: str) -> int:
        while self.text(i) != tok:
            i += 1
            if i >= self.n:
                raise ValueError(f"`{tok}` 없음")
        return i

    def statement_end(self, i: int) -> int:
        """토큰 i 에서 시작하는 문장의 끝 다음 인덱스."""
        t = self.text(i)
        if t == "{":
            return self.match(i) + 1
        if t == "unchecked" and self.text(i + 1) == "{":
            return self.match(i + 1) + 1
        if t == "if":
            j = self.statement_end(self.match(i + 1) + 1)
            return self.statement_end(j + 1) if self.text(j) == "else" else j
        if t in ("for", "while"):
            return self.statement_end(self.match(i + 1) + 1)
        if t == "do":
            j = self.statement_end(i + 1)
            if self.text(j) != "while":
                raise ValueError("do 뒤에 while 없음")
            return self.match(j + 1) + 2
        if t == "assembly":
            return self.match(self._scan_to(i, "{")) + 1
        if t == "try":
            return self._try_end(i)
        j = i
        while True:
            if j >= self.n:
                raise ValueError("문장 끝 없음")
            x = self.text(j)
            if x == ";":
                return j + 1
            if x in _CLOSE:
                j = self.match(j) + 1
            elif x == "}":
                raise ValueError("문장 끝 없음")
            else:
                j += 1

    def _try_end(self, i: int) -> int:
        j = i + 1
        while True:                                  # 호출 옵션 `{value: v}(...)` 은 뒤에 `(` 가 온다
            x = self.text(j)
            if x in ("(", "["):
                j = self.match(j) + 1
            elif x == "{":
                k = self.match(j)
                j = k + 1
                if self.text(j) != "(":
                    break
            else:
                j += 1
                if j >= self.n:
                    raise ValueError("try 본문 없음")
        while self.text(j) == "catch":
            j = self.match(self._scan_to(j, "{")) + 1
        return j


@dataclass
class _Body:
    kw: str
    header_end: int       # 헤더 끝 문자 오프셋
    start: int            # 본문 시작 문자 오프셋
    end: int              # 본문 끝 문자 오프셋
    next_start: int       # 뒤따르는 else/while 의 시작 오프셋, 없으면 -1


@dataclass
class Normalized:
    text: str
    line_map: dict = field(default_factory=dict)   # {정규화 줄: 원본 줄}
    braces_inserted: int = 0
    chains_joined: int = 0


def find_braceless(masked: str) -> tuple[list[_Body], list[tuple[int, int]]]:
    """(중괄호 없는 본문들, `else` ⏎ `if` 처럼 줄바꿈으로 벌어진 체인의 간격들)."""
    tk = _Tokens(masked)
    bodies, gaps = [], []
    i = 0
    while i < tk.n:
        t = tk.text(i)
        if t == "assembly":
            j = i
            while j < tk.n and tk.text(j) not in ("{", ";"):
                j += 1
            if tk.text(j) == "{":
                i = tk.match(j) + 1
                continue
        if t not in ("if", "for", "while", "else", "do") or tk.text(i - 1) == ".":
            i += 1
            continue
        if t in ("if", "for", "while"):
            if tk.text(i + 1) != "(":
                i += 1
                continue
            head = tk.match(i + 1)
            body = head + 1
            if t == "while" and tk.text(body) == ";":     # do-while 꼬리
                i += 1
                continue
        else:
            head, body = i, i + 1
            if t == "else" and tk.text(body) == "if":     # else-if 체인
                if "\n" in masked[tk.items[head][2]:tk.items[body][1]]:
                    gaps.append((tk.items[head][2], tk.items[body][1]))
                i += 1
                continue
        if tk.text(body) != "{":
            end = tk.statement_end(body)
            nx = tk.text(end)
            follows = (t == "if" and nx == "else") or (t == "do" and nx == "while")
            bodies.append(_Body(t, tk.items[head][2], tk.items[body][1], tk.items[end - 1][2],
                                tk.items[end][1] if follows else -1))
        i += 1
    return bodies, gaps


def _indent(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    return text[start:start + len(text[start:]) - len(text[start:].lstrip(" \t"))]


def normalize(src: str) -> Normalized:
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    code, masked = blank_comments(src)
    bodies, gaps = find_braceless(masked)

    edits = []            # (시작, 정렬키, 끝, 삽입 텍스트)
    stack = []            # 바깥 본문의 (끝, 들여쓰기, 헤더 줄 시작)
    for b in sorted(bodies, key=lambda x: x.header_end):
        while stack and stack[-1][0] <= b.header_end:
            stack.pop()
        line_start = code.rfind("\n", 0, b.header_end) + 1
        ind = _indent(code, b.header_end)
        if stack and stack[-1][2] == line_start:      # 바깥 헤더와 같은 줄이면 한 단계 더 들인다
            ind = stack[-1][1] + "    "
        edits.append((b.header_end, 0, b.start, " {\n" + ind + "    "))
        if b.next_start >= 0:
            edits.append((b.end, -b.start, b.next_start, "\n" + ind + "} "))
        else:
            edits.append((b.end, -b.start, b.end, "\n" + ind + "}"))
        stack.append((b.end, ind, line_start))
    for a, z in gaps:
        edits.append((a, 0, z, " "))

    out, line_map, cur_line, src_line, pos = [], {}, 1, 1, 0

    def copy(upto):
        nonlocal pos, cur_line, src_line
        for ch in code[pos:upto]:
            if ch == "\n":
                line_map.setdefault(cur_line, src_line)
                cur_line += 1
                src_line += 1
            elif not ch.isspace():
                line_map.setdefault(cur_line, src_line)
            out.append(ch)
        pos = max(pos, upto)

    for start, _, stop, text in sorted(edits, key=lambda e: (e[0], e[1])):
        copy(start)
        src_line += code.count("\n", pos, stop)
        pos = max(pos, stop)
        for ch in text:
            if ch == "\n":
                line_map.setdefault(cur_line, src_line)
                cur_line += 1
            out.append(ch)
    copy(len(code))
    line_map.setdefault(cur_line, src_line)

    text = "\n".join(ln.rstrip() for ln in "".join(out).split("\n"))
    return Normalized(text, line_map, len(bodies), len(gaps))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    r = normalize(read_source(a.source))
    with open(a.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(r.text)
    with open(a.output + ".linemap.json", "w", encoding="utf-8") as f:
        json.dump(r.line_map, f)
    print(f"{a.source}: 중괄호 {r.braces_inserted}건 삽입, 체인 {r.chains_joined}건 결합")


if __name__ == "__main__":
    main()
