#!/usr/bin/env python3
# TouchDesigner 속도 진단 + 안전 최적화 (touchdesigner-mcp 9981 사용)
# --------------------------------------------------------------------------
# 저장: 이 파일을 바탕화면 등에 tdspeed.py 로 저장.
# 사용:
#   python tdspeed.py                      -> 제일 느린 노드 진단 + 안전 최적화(TOP 뷰어 끄기)
#   python tdspeed.py "op('/x').par.a=1"   -> 따옴표 안 파이썬을 TD에서 즉시 실행 (수정 적용)
# TD 가 열려 있고 mcp_webserver_base(.tox) 가 /project1 에서 돌아야 합니다.
# --------------------------------------------------------------------------
import json
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:9981/api/td/server/exec"

PROFILE = r"""
def _n(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0
_ops = op('/').findChildren(maxDepth=100)
_r = []
for _o in _ops:
    d = {'p': _o.path, 'f': getattr(_o, 'family', ''), 't': getattr(_o, 'type', ''),
         'c': round(_n(getattr(_o, 'cookTime', 0)), 3),
         'k': int(getattr(_o, 'totalCooks', 0) or 0)}
    try:
        if _o.family == 'TOP':
            d['w'] = int(_o.width); d['h'] = int(_o.height)
    except Exception:
        pass
    _r.append(d)
_r.sort(key=lambda x: x['c'], reverse=True)
_off = 0
for _o in _ops:
    try:
        if _o.family == 'TOP' and _o.viewer:
            _o.viewer = False; _off += 1
    except Exception:
        pass
try:
    _fps = _n(getattr(project, 'cookRate', 0))
except Exception:
    _fps = 0.0
result = {'fps': _fps, 'n': len(_r), 'tot': round(sum(x['c'] for x in _r), 3),
          'top': _r[:15], 'off': _off}
"""


def run(script):
    body = json.dumps({"script": script}).encode("utf-8")
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise SystemExit(
            "9981 서버에 연결 실패: %s\n"
            "TouchDesigner가 열려 있고 mcp_webserver_base(.tox)가 도는지 확인하세요.\n"
            "브라우저에서 http://127.0.0.1:9981/health 가 응답하는지 먼저 보세요." % e)


def fix(script):
    res = run(script)
    data = res.get("data") or {}
    if data.get("stdout"):
        print(data["stdout"].rstrip())
    val = (data.get("result") or {}).get("value")
    if val is not None:
        print(val)
    if not res.get("success", True):
        print("오류:", res.get("error") or data.get("stderr"), file=sys.stderr)
        return 1
    print("적용 완료.")
    return 0


def diagnose():
    res = run(PROFILE)
    if not res.get("success", False):
        print("오류:", res.get("error") or (res.get("data") or {}).get("stderr"), file=sys.stderr)
        return 1
    v = res["data"]["result"]["value"]
    fps = v.get("fps") or 0
    budget = 1000.0 / fps if fps else 0.0
    print("=" * 60)
    print("fps=%g  ops=%d  cook합=%.1fms  (프레임 예산 ~%.1fms)"
          % (fps, v["n"], v["tot"], budget))
    if budget and v["tot"] > budget:
        print(">>> 프레임 예산 초과 — 아래 상위 노드가 원인입니다.")
    print("-" * 60)
    print("가장 느린 오퍼레이터 (ms):")
    for x in v["top"]:
        rs = ("  %dx%d" % (x["w"], x["h"])) if "w" in x else ""
        print("  %8.3f  %-38s %-4s cooks=%d%s" % (x["c"], x["p"], x["f"], x["k"], rs))
    print("-" * 60)
    print("안전 최적화 적용: TOP 노드 뷰어 %d개 끔 (송출 화면 영향 없음)." % v["off"])
    print("  되돌리기: python tdspeed.py \"[setattr(o,'viewer',True) for o in op('/').findChildren(type=TOP)]\"")
    tips = []
    for x in v["top"][:6]:
        if "w" in x and x["w"] * x["h"] > 1280 * 720:
            tips.append(
                "# %s (%dx%d, %.1fms) 해상도 절반으로 (해당 TOP이 자체 해상도일 때):\n"
                "python tdspeed.py \"o=op('%s'); o.par.resolutionw=%d; o.par.resolutionh=%d\""
                % (x["p"], x["w"], x["h"], x["c"], x["p"], max(2, x["w"] // 2), max(2, x["h"] // 2)))
        if x["f"] in ("DAT", "CHOP") and budget and x["c"] > budget * 0.25:
            tips.append(
                "# %s (%s, %.1fms) 매 프레임 무거움 — 비용 확인용 bypass:\n"
                "python tdspeed.py \"op('%s').bypass=True\"" % (x["p"], x["t"], x["c"], x["p"]))
    if tips:
        print("-" * 60)
        print("추천 수정 명령 (내용 확인 후 실행):")
        for t in tips:
            print(t)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(fix(sys.argv[1]) if len(sys.argv) > 1 else diagnose())
