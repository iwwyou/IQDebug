"""엔진이 내놓는 디버그 슬롯 키는 디버그 입력 문법으로 다시 읽혀야 한다 (2026-10-25).

48 rebuilt 케이스(cases74.csv expressible=Yes)의 타깃 함수마다 analyze → 모든 슬롯 키를 `@<Kind> <key> = <값>` 으로 만들어
`ParserHelpers.annotation_syntax_errors` 로 검사한다. 예전엔 `IF(feeds[t]).get()[k]` 같은 키를 내놓고, 입력하면 파서가 오류를
출력만 하고 진행해 visitor 가 죽었다. 문법이 못 읽는 키는 KNOWN(사용자 결정: 문법 확장 = future work)에 적어 둔다 —
KNOWN 밖의 새 키가 나오거나 KNOWN 키가 사라지면 실패.
"""
import csv, gc, io, contextlib, pathlib, re, sys, os

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation" / "intent_validation"))
sys.path.insert(0, str(ROOT / "evaluation" / "Dataset"))
os.chdir(ROOT)

import api_driver                                  # noqa: E402
import engine_api                                  # noqa: E402
from reassemble import project_key                 # noqa: E402
from Utils.Helper import ParserHelpers             # noqa: E402

# 문법이 아직 못 읽는 슬롯 키 — IReturn 캐스트 인자가 식별자가 아닌 식(문법 확장 = future work)
KNOWN = {
    "web3bugs_52_H_04": {"AggregatorV3Interface(_aggregators[pairData.token1]).latestRoundData()"},
    "web3bugs_52_H_34": {"AggregatorV3Interface(_aggregators[pairData.token1]).latestRoundData()"},
    "web3bugs_192_H_01": {"IERC20(assets[i]).balanceOf()"},
    "web3bugs_79_H_02": {"IJoePair(IJoeFactory(factory).getPair()).totalSupply()"},
}
_VALUE = {"interval": "[0,0]", "bool": "true", "address": "symbolicAddress 1"}

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    ok += bool(cond)
    fail += (not cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


DS = ROOT / "evaluation" / "Dataset"
rows = [r for r in csv.DictReader(open(DS / "cases74.csv", encoding="utf-8")) if r["expressible"] == "Yes"]
for r in rows:
    cid = r["case_id"]
    row = api_driver.case_row(cid)
    source, _debug, intents = api_driver.case_inputs(cid)
    items = [engine_api.IntentItem(scopeKind=k, text=t, line=(ln if k == "During" else None)) for k, t, ln in intents]
    with contextlib.redirect_stdout(io.StringIO()):
        sess = engine_api.EngineSession(str(DS / "deps" / project_key(row)), tuple(api_driver.declared_names(source)))
        st = sess.analyze_source(source, contract=row["contract"], func=row["function"], intents=items)
    bad = set()
    for s in st.debug_slots:
        line = f"@{s.kind} {s.key} = {_VALUE.get(s.valueKind, '[0,0]')}"
        if ParserHelpers.annotation_syntax_errors(line):
            # IReturn 튜플 원소 키(`...()[k]`)는 호출 부분으로 묶어 비교
            bad.add(re.sub(r"(\))\[\d+\]$", r"\1", s.key))
    want = KNOWN.get(cid, set())
    check(f"{cid} slot keys parse", bad == want, f"unparseable={sorted(bad)} known={sorted(want)}")
    del sess, st
    gc.collect()

print(f"{ok} passed, {fail} failed")
