"""필요한 solc(windows-amd64) 바이너리를 공식 목록에서 받아 sha256 을 검증한다.

`stopAfter: "parsing"` 이라도 solc 는 **버전 pragma 를 파싱 단계에서 강제**하므로
(`Source file requires different compiler version`), 파일의 허용 버전에 맞는 바이너리가 필요하다.

사용: python -I fetch_solc.py <버전...>      예) python -I fetch_solc.py 0.8.0 0.8.4 0.8.38
     python -I fetch_solc.py --from-manifest <targets_v*.json> <select_targets.py 가 있는 디렉터리>
"""
import hashlib
import io
import json
import os
import sys
import urllib.request

BASE = "https://binaries.soliditylang.org/windows-amd64/"
HERE = os.path.dirname(os.path.abspath(__file__))
# urllib 기본 User-Agent 는 403 을 받는다.
_OPENER = urllib.request.build_opener()
_OPENER.addheaders = [("User-Agent", "curl/8.0 (solc fetch for research)")]
urllib.request.install_opener(_OPENER)


def load_list():
    p = os.path.join(HERE, "list.json")
    if not os.path.exists(p):
        urllib.request.urlretrieve(BASE + "list.json", p)
    return json.load(io.open(p, encoding="utf-8"))


def fetch(version: str, lst) -> str:
    fn = lst["releases"].get(version)
    if not fn:
        raise SystemExit(f"릴리스 없음: {version}")
    want = next((b["sha256"] for b in lst["builds"] if b["version"] == version), None)
    dst = os.path.join(HERE, f"solc-{version}.exe")
    if os.path.exists(dst):
        got = hashlib.sha256(open(dst, "rb").read()).hexdigest()
        if want and got == want.removeprefix("0x"):
            print(f"  {version}: 이미 있음(검증 완료)")
            return dst
    urllib.request.urlretrieve(BASE + fn, dst)
    got = hashlib.sha256(open(dst, "rb").read()).hexdigest()
    ok = (want is None) or (got == want.removeprefix("0x"))
    print(f"  {version}: {os.path.getsize(dst) // 1024} KB | sha256 {'OK' if ok else '불일치!'}")
    if not ok:
        os.remove(dst)
        raise SystemExit(f"{version} 체크섬 불일치 — 삭제했다")
    return dst


def versions_from_manifest(mpath: str, sel_dir: str):
    sys.path.insert(0, sel_dir)
    import select_targets as st  # noqa: E402
    d = json.load(io.open(mpath, encoding="utf-8"))
    out = set()
    for m in d["selected_meta"].values():
        a = st.file_allowed(m["specs"])
        if a:
            out.add("%d.%d.%d" % max(a))
    return sorted(out, key=lambda v: [int(x) for x in v.split(".")])


def main():
    lst = load_list()
    if sys.argv[1] == "--from-manifest":
        vs = versions_from_manifest(sys.argv[2], sys.argv[3])
    else:
        vs = sys.argv[1:]
    print("필요한 버전:", vs)
    for v in vs:
        fetch(v, lst)
    print("완료. latestRelease =", lst.get("latestRelease"))


if __name__ == "__main__":
    main()
