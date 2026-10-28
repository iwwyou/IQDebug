# -*- coding: utf-8 -*-
"""objectfile/*.pkl 건강검진.

왜 필요한가: `analyze_file` 이 `sa.update_code` 실패를 **무시(pass)** 한다.
그 실패가 누적되면 함수 CFG 에 statement 가 하나도 안 들어간 채 pkl 로 저장되고,
나중에 그 함수를 호출하면 **크래시도 경고도 없이** 선언된 리턴변수의 기본값
(예: uint160 이면 0)을 조용히 반환한다. 재생성 로그의 `[UPDATE_CODE ERR]` 는
*파싱* 실패만 잡으므로, 결과물 쪽에서 한 번 더 확인한다.

실행: .venv\Scripts\python.exe Dependencies\check_pkl.py --root <deps\<p>> [--verbose]
종료코드: 손상 의심이 있으면 1
"""
from __future__ import annotations

import pathlib
import pickle
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# (2026-10-22) 전역 Dependencies/objectfile 폐기 — 프로젝트 deps 루트를 받는다: --root <deps/<p>>
if "--root" not in sys.argv:
    sys.exit("usage: check_pkl.py --root <deps/<p>> [--verbose]")
OBJ_DIR = pathlib.Path(sys.argv[sys.argv.index("--root") + 1]) / "objectfile"
VERBOSE = "--verbose" in sys.argv

# interface 는 본문이 없는 게 정상. 아래는 "함수 0개가 정상" 으로 알려진 것들
# (Dependencies/ISSUES.md 참조) — 빈 interface / event·enum·struct 전용.
KNOWN_EMPTY_OK = {
    "IMochi", "IPreparable", "IPooledCreditLineEnums", "IPooledCreditLineDeclarations",
}


def load(p: pathlib.Path):
    with open(p, "rb") as f:
        raw = pickle.load(f)
    return raw["cfg"] if isinstance(raw, dict) and "cfg" in raw else raw


def fn_stmt_count(fcfg) -> int:
    n = 0
    for node in fcfg.graph.nodes():
        n += len(getattr(node, "statements", []) or [])
    return n


def main() -> int:
    pkls = sorted(OBJ_DIR.glob("*.pkl"))
    if not pkls:
        print("objectfile 에 pkl 이 없다. Dependencies/main.py 를 먼저 실행할 것.")
        return 1

    load_fail, empty_fn, ok_fn, n_contract = [], [], 0, 0
    for p in pkls:
        try:
            cfg = load(p)
        except Exception as e:                       # pkl 자체가 깨짐
            load_fail.append((p.name, f"{type(e).__name__}: {e}"))
            continue
        n_contract += 1
        is_iface = p.name.startswith("ifc_")
        for fname, subs in (getattr(cfg, "functions", {}) or {}).items():
            for sig, fcfg in subs.items():
                c = fn_stmt_count(fcfg)
                if c == 0:
                    # interface 함수는 본문이 없는 게 정상
                    if is_iface or getattr(cfg, "contract_name", "") in KNOWN_EMPTY_OK:
                        continue
                    empty_fn.append((p.name, f"{fname}{sig}"))
                else:
                    ok_fn += 1

    print("=" * 70)
    print(f"pkl {len(pkls)}개 / 로드 성공 {n_contract}개")
    print(f"본문 있는 함수 {ok_fn}개")
    print("=" * 70)

    if load_fail:
        print(f"\n[X] 로드 실패 {len(load_fail)}개")
        for n, e in load_fail:
            print(f"    {n}: {e}")

    if empty_fn:
        print(f"\n[!] ★ 본문이 비어 저장된 함수 {len(empty_fn)}개 (조용한 오분석 위험)")
        from collections import defaultdict
        by = defaultdict(list)
        for n, f in empty_fn:
            by[n].append(f)
        for n in sorted(by):
            fs = by[n]
            print(f"    {n}: {len(fs)}개")
            for f in (fs if VERBOSE else fs[:4]):
                print(f"        {f}")
            if not VERBOSE and len(fs) > 4:
                print(f"        ... (+{len(fs)-4}, --verbose 로 전체)")

    if not load_fail and not empty_fn:
        print("\n[OK] 손상 의심 없음")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
