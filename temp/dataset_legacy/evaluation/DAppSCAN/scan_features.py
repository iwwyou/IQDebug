"""주어진 .sol 파일 목록에서 언어 구성요소 빈도를 센다 (엔진 미지원 후보 리스트업용).

사용: python -I scan_features.py <contracts_root> <files_json> <out_json>
  files_json = {"clean_closure_files": [...]} 또는 ["rel/path.sol", ...]
"""
import json
import os
import re
import sys
from collections import Counter

PATTERNS = {
    # 엔진이 명시적으로 미지원/제약이라고 적어 둔 것들
    "try_catch":            r"\btry\b[\s\S]{0,400}?\bcatch\b",
    "do_while":             r"\bdo\s*\{",
    "break":                r"\bbreak\s*;",
    "continue":             r"\bcontinue\s*;",
    "assembly":             r"\bassembly\b",
    "constructor":          r"\bconstructor\s*\(",
    "new_contract":         r"\bnew\s+[A-Z]\w*\s*\(",
    "storage_ref_local":    r"\b\w+\s+storage\s+\w+\s*=",
    # 0.8 이후 문법 (파서/의미 지원 확인 필요)
    "custom_error_decl":    r"(?m)^\s*error\s+\w+\s*\(",
    "revert_custom":        r"\brevert\s+\w+\s*\(",
    "user_defined_vtype":   r"\btype\s+\w+\s+is\s+\w+\s*;",
    "unchecked_block":      r"\bunchecked\s*\{",
    "immutable":            r"\bimmutable\b",
    "using_for_global":     r"\busing\s+[\w.]+\s+for\s+[\w\[\]]+\s+global",
    "free_function":        r"(?m)^function\s+\w+\s*\(",
    "abi_decode":           r"\babi\.decode\s*\(",
    "abi_encode":           r"\babi\.encode(?:Packed|WithSelector|WithSignature|Call)?\s*\(",
    "bytes_concat":         r"\b(?:bytes|string)\.concat\s*\(",
    "transient_tstore":     r"\bt(?:store|load)\s*\(",
    # 호출/제어 흐름 (operation 행사 여부 + opaque 비율 추정)
    "low_level_call":       r"\.(?:call|delegatecall|staticcall)\s*[\{\(]",
    "addr_transfer_send":   r"\.(?:transfer|send)\s*\(",
    "modifier_decl":        r"\bmodifier\s+\w+",
    "inheritance":          r"(?m)^\s*(?:abstract\s+)?contract\s+\w+\s+is\b",
    "library_decl":         r"(?m)^\s*library\s+\w+",
    "interface_decl":       r"(?m)^\s*interface\s+\w+",
    "virtual_override":     r"\b(?:virtual|override)\b",
    "for_loop":             r"\bfor\s*\(",
    "while_loop":           r"\bwhile\s*\(",
    "nested_mapping":       r"\bmapping\s*\(\s*[^)]*=>\s*mapping\s*\(",
    "struct_decl":          r"(?m)^\s*struct\s+\w+",
    "enum_decl":            r"(?m)^\s*enum\s+\w+",
    "array_push_pop":       r"\.(?:push|pop)\s*\(",
    "payable":              r"\bpayable\b",
    "receive_fallback":     r"\b(?:receive|fallback)\s*\(\s*\)",
    "selfdestruct":         r"\bselfdestruct\s*\(",
    "delete_stmt":          r"\bdelete\s+\w",
}
COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}

ASM = re.compile(r"\bassembly\b[^{]*\{")
YUL_CTRL = {
    "yul_if":       re.compile(r"\bif\b"),
    "yul_switch":   re.compile(r"\bswitch\b"),
    "yul_for":      re.compile(r"\bfor\b"),
    "yul_function": re.compile(r"\bfunction\s+\w+\s*\("),
    "yul_leave":    re.compile(r"\bleave\b"),
    "yul_return":   re.compile(r"\breturn\s*\("),
    "yul_stop":     re.compile(r"\bstop\s*\("),
}


def asm_bodies(txt):
    for m in ASM.finditer(txt):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(txt)):
            if txt[j] == "{":
                depth += 1
            elif txt[j] == "}":
                depth -= 1
                if depth == 0:
                    yield txt[i + 1:j]
                    break



def _lp(p):
    r"""Windows MAX_PATH 회피: 260자 넘는 경로는 \\?\ 확장 경로로 연다."""
    ap = os.path.abspath(p).replace("/", os.sep)
    pre = "\\\\?\\"
    return pre + ap if os.name == "nt" and not ap.startswith(pre) else ap


def main():
    root, files_json, out = sys.argv[1], sys.argv[2], sys.argv[3]
    data = json.load(open(files_json, encoding="utf-8"))
    rels = data["clean_closure_files"] if isinstance(data, dict) else data
    nfiles = Counter()   # 구성요소를 포함한 파일 수
    nocc = Counter()     # 총 출현 수
    import os
    for rel in rels:
        try:
            txt = open(_lp(os.path.join(root, rel)), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for k, rx in COMPILED.items():
            hits = len(rx.findall(txt))
            if hits:
                nfiles[k] += 1
                nocc[k] += hits
        bodies = list(asm_bodies(txt))
        if bodies:
            nfiles["assembly_block"] += 1
            nocc["assembly_block"] += len(bodies)
            seen = set()
            for b in bodies:
                for k, rx in YUL_CTRL.items():
                    h = len(rx.findall(b))
                    if h:
                        nocc[k] += h
                        seen.add(k)
            for k in seen:
                nfiles[k] += 1
    res = {"files_scanned": len(rels),
           "by_feature": {k: {"files": nfiles[k], "occurrences": nocc[k]}
                          for k in sorted(nfiles, key=lambda x: -nfiles[x])}}
    print(json.dumps(res, indent=2, ensure_ascii=False))
    json.dump(res, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("wrote", out)


if __name__ == "__main__":
    main()
