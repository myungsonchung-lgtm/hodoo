#!/usr/bin/env python3
# TouchDesigner 속도 진단 + 안전 최적화 (touchdesigner-mcp 9981 사용)
# --------------------------------------------------------------------------
# 저장: 이 파일을 tdspeed.py 로 저장 (예: C:\Users\<you>\tdspeed.py).
# 사용:
#   python tdspeed.py                      -> 제일 느린 노드 진단 + 안전 최적화(TOP 뷰어 끄기)
#   python tdspeed.py "op('/x').par.a=1"   -> 따옴표 안 파이썬을 TD에서 즉시 실행 (수정 적용)
# TD 가 열려 있고 mcp_webserver_base(.tox) 가 /project1 에서 돌아야 합니다.
# --------------------------------------------------------------------------
import ast
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
_o = op('/').findChildren(maxDepth=100)
_r = []
for x in _o:
    d = {'p': x.path, 'f': getattr(x, 'family', ''), 't': getattr(x, 'type', ''),
         'c': round(_n(getattr(x, 'cookTime', 0)), 3),
         'k': int(getattr(x, 'totalCooks', 0) or 0)}
    try:
        if x.family == 'TOP':
            d['w'] = int(x.width); d['h'] = int(x.height)
    except Exception:
        pass
    _r.append(d)
_r.sort(key=lambda z: z['c'], reverse=True)
_f = 0
for x in _o:
    try:
        if x.family == 'TOP' and x.viewer:
            x.viewer = False; _f += 1
    except Exception:
        pass
try:
    _fp = _n(getattr(project, 'cookRate', 0))
except Exception:
    _fp = 0.0
result = {'fps': _fp, 'n': len(_r), 'tot': round(sum(z['c'] for z in _r), 3),
          'top': _r[:15], 'off': _f}
"""


def coerce(x):
    """Some MCP builds return dicts/lists as JSON or Python-repr strings."""
    if isinstance(x, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                return parse(x)
            except Exception:
                pass
    return x


def num(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def call(script):
    body = json.dumps({"script": script}).encode("utf-8")
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise SystemExit(
            "9981 연결 실패: %s\nTouchDesigner가 열려 있고 mcp_webserver_base(.tox)가 "
            "도는지 확인하세요." % e)


def find_report(res):
    d = res.get("data")
    cands = [d]
    if isinstance(d, dict):
        cands.append(d.get("result"))
        if isinstance(d.get("result"), dict):
            cands.append(d["result"].get("value"))
    for c in cands:
        c = coerce(c)
        if isinstance(c, dict) and "fps" in c:
            return c
    return None


def fix(script):
    res = call(script)
    d = res.get("data")
    if isinstance(d, dict) and d.get("stdout"):
        print(str(d["stdout"]).rstrip())
    if not res.get("success", True):
        print("오류:", json.dumps(res, ensure_ascii=False)[:800], file=sys.stderr)
        return 1
    print("적용 완료.")
    return 0


def diagnose():
    res = call(PROFILE)
    v = find_report(res)
    if v is None:
        print("결과 구조를 못 찾음. 아래 원본 응답을 복사해서 알려주세요:")
        print(json.dumps(res, ensure_ascii=False)[:2000])
        return 1
    fps = num(v.get("fps"))
    budget = 1000.0 / fps if fps else 0.0
    top = coerce(v.get("top")) or []
    print("=" * 60)
    print("fps=%g  ops=%s  cook합=%.1fms  (프레임 예산 ~%.1fms)"
          % (fps, v.get("n"), num(v.get("tot")), budget))
    if budget and num(v.get("tot")) > budget:
        print(">>> 프레임 예산 초과 — 아래 상위 노드가 원인입니다.")
    print("-" * 60)
    print("가장 느린 오퍼레이터 (ms):")
    for x in top:
        x = coerce(x)
        if not isinstance(x, dict):
            continue
        rs = ("  %sx%s" % (x.get("w"), x.get("h"))) if "w" in x else ""
        print("  %8.3f  %-38s %-4s cooks=%s%s"
              % (num(x.get("c")), x.get("p"), x.get("f"), x.get("k", 0), rs))
    print("-" * 60)
    print("안전 최적화 적용: TOP 노드 뷰어 %s개 끔 (송출 화면 영향 없음)." % v.get("off"))
    print("  되돌리기: python tdspeed.py \"[setattr(o,'viewer',True) for o in op('/').findChildren(type=TOP)]\"")
    tips = []
    for x in top[:6]:
        x = coerce(x)
        if not isinstance(x, dict):
            continue
        if "w" in x and num(x["w"]) * num(x["h"]) > 1280 * 720:
            tips.append(
                "# %s (%sx%s, %.1fms) 해상도 절반으로 (해당 TOP이 자체 해상도일 때):\n"
                "python tdspeed.py \"o=op('%s'); o.par.resolutionw=%d; o.par.resolutionh=%d\""
                % (x["p"], x["w"], x["h"], num(x["c"]), x["p"],
                   int(num(x["w"])) // 2, int(num(x["h"])) // 2))
        if x.get("f") in ("DAT", "CHOP") and budget and num(x.get("c")) > budget * 0.25:
            tips.append(
                "# %s (%s, %.1fms) 매 프레임 무거움 — 비용 확인용 bypass:\n"
                "python tdspeed.py \"op('%s').bypass=True\"" % (x["p"], x.get("t"), num(x["c"]), x["p"]))
    if tips:
        print("-" * 60)
        print("추천 수정 명령 (내용 확인 후 실행):")
        for t in tips:
            print(t)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(fix(sys.argv[1]) if len(sys.argv) > 1 else diagnose())
