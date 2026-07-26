#!/usr/bin/env python3
"""td9981 - drive the touchdesigner-mcp WebServer (port 9981) from a terminal.

This talks to the API that the touchdesigner-mcp `.tox` (mcp_webserver_base)
exposes inside TouchDesigner, so you can profile and fix the OPEN project
directly from your own PC — no Claude Desktop, no npx, no extra install
(standard library only). It POSTs Python to /api/td/server/exec.

Run it on the SAME PC where TouchDesigner is open.

Examples
--------
    python td9981.py health                 # is the in-TD server up?
    python td9981.py perf                    # rank the slowest operators
    python td9981.py perf 30
    python td9981.py optimize                # diagnose (no changes)
    python td9981.py optimize --apply        # apply safe, reversible speedups
    python td9981.py optimize --apply --cap 1280x720
    python td9981.py undo                     # revert what --apply changed
    python td9981.py ls /project1            # list child operators
    python td9981.py exec "op('/project1').par"    # run any Python
    python td9981.py --url http://127.0.0.1:9981 perf

Set `result = <value>` in an exec script to get a value back.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:9981"

# Profiler that runs inside TD. Sets `result` so the API returns it.
PERF_SCRIPT = r"""
def _num(v):
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0
_ops = op('/').findChildren(maxDepth=100)
_rows = []
for _o in _ops:
    _r = {'path': _o.path, 'family': getattr(_o, 'family', ''),
          'type': getattr(_o, 'type', ''),
          'cook': round(_num(getattr(_o, 'cookTime', 0.0)), 3),
          'cooks': int(getattr(_o, 'totalCooks', 0) or 0)}
    try:
        if _o.family == 'TOP':
            _r['res'] = [int(_o.width), int(_o.height)]
    except Exception:
        pass
    _rows.append(_r)
_rows.sort(key=lambda r: r['cook'], reverse=True)
try:
    _fps = _num(getattr(project, 'cookRate', 0.0))
except Exception:
    _fps = 0.0
result = {'fps': _fps, 'count': len(_rows),
          'total': round(sum(r['cook'] for r in _rows), 3), 'top': _rows[:TOPN]}
"""

LS_SCRIPT = "result = (lambda _o: sorted(c.path for c in _o.children) if _o else 'no such op')(op(%r))"

# Diagnose + apply safe, fully reversible optimizations, then return a report.
# Two things are applied when APPLY_SAFE is set, both recorded to a backup store
# so `undo` can restore them exactly:
#   1) TOP node-viewer thumbnails off  — editor-only cost, rendered output is
#      unaffected.
#   2) Oversized TOPs shrunk to a resolution cap — ONLY TOPs that own their
#      resolution (mode custom/fixed); input-sized TOPs are never touched, so
#      the network's sizing logic is preserved.
OPTIMIZE_SCRIPT = r"""
def _num(v):
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0
_ops = op('/').findChildren(maxDepth=100)
_rows = []
for _o in _ops:
    _r = {'path': _o.path, 'family': getattr(_o, 'family', ''),
          'type': getattr(_o, 'type', ''),
          'cook': round(_num(getattr(_o, 'cookTime', 0.0)), 3),
          'cooks': int(getattr(_o, 'totalCooks', 0) or 0)}
    try:
        if _o.family == 'TOP':
            _r['res'] = [int(_o.width), int(_o.height)]
    except Exception:
        pass
    _rows.append(_r)
_rows.sort(key=lambda r: r['cook'], reverse=True)

_voff = 0
_down = []      # oversized TOPs whose resolution we shrank
_skip = []      # heavy TOPs left alone (needs a human) + why
if APPLY_SAFE:
    _bk = op('/').fetch('td9981_backup', None)
    if not isinstance(_bk, dict):
        _bk = {'viewer': [], 'res': {}}
    _bk.setdefault('viewer', [])
    _bk.setdefault('res', {})
    for _o in _ops:
        try:
            if _o.family != 'TOP':
                continue
            # (1) node-viewer thumbnail: pure editor cost, output unaffected
            if _o.viewer:
                _o.viewer = False
                _bk['viewer'].append(_o.path)
                _voff += 1
            # (2) shrink oversized self-sized TOPs to the cap
            _w = int(_o.width); _h = int(_o.height)
            _ck = _num(getattr(_o, 'cookTime', 0.0))
            if _w * _h <= CAPW * CAPH:
                continue
            _mp = getattr(_o.par, 'resolution', None)
            _rw = getattr(_o.par, 'resolutionw', None)
            _rh = getattr(_o.par, 'resolutionh', None)
            if _mp is None or _rw is None or _rh is None:
                _skip.append({'path': _o.path, 'why': '해상도 파라미터 없음',
                              'res': [_w, _h], 'cook': round(_ck, 3)})
                continue
            _mode = str(_mp.eval()).lower()
            if ('custom' not in _mode) and ('fixed' not in _mode):
                _skip.append({'path': _o.path,
                              'why': '입력에서 크기 받음(' + _mode + ') - 안전상 유지',
                              'res': [_w, _h], 'cook': round(_ck, 3)})
                continue
            _f = min(CAPW / float(_w), CAPH / float(_h))
            _nw = max(2, int(_w * _f)); _nh = max(2, int(_h * _f))
            if _o.path not in _bk['res']:
                _bk['res'][_o.path] = {'rw': _rw.eval(), 'rh': _rh.eval()}
            _rw.val = _nw; _rh.val = _nh
            _down.append({'path': _o.path, 'frm': [_w, _h], 'to': [_nw, _nh],
                          'cook': round(_ck, 3)})
        except Exception:
            pass
    op('/').store('td9981_backup', _bk)

try:
    _fps = _num(getattr(project, 'cookRate', 0.0))
except Exception:
    _fps = 0.0
result = {'fps': _fps, 'count': len(_rows),
          'total': round(sum(r['cook'] for r in _rows), 3),
          'top': _rows[:TOPN], 'viewers_off': _voff,
          'downsized': _down, 'skipped': _skip}
"""

# Restore everything a previous `optimize --apply` changed, from the backup store.
UNDO_SCRIPT = r"""
_bk = op('/').fetch('td9981_backup', None)
if not isinstance(_bk, dict):
    _bk = {}
_von = 0
for _p in list(_bk.get('viewer', []) or []):
    _o = op(_p)
    if not _o:
        continue
    try:
        _o.viewer = True; _von += 1
    except Exception:
        pass
_rres = 0
for _p, _b in dict(_bk.get('res', {}) or {}).items():
    _o = op(_p)
    if not _o:
        continue
    try:
        _o.par.resolutionw.val = _b['rw']; _o.par.resolutionh.val = _b['rh']; _rres += 1
    except Exception:
        pass
try:
    op('/').unstore('td9981_backup')
except Exception:
    pass
result = {'viewers_on': _von, 'res_restored': _rres}
"""


def exec_td(base: str, script: str, timeout: float = 30.0) -> dict:
    body = json.dumps({"script": script}).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/td/server/exec",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise SystemExit(
            "Cannot reach the TouchDesigner server at %s (%s).\n"
            "Make sure TouchDesigner is open and the mcp_webserver_base .tox is "
            "running (check http://127.0.0.1:9981/health in a browser)." % (base, e)
        )


def _result_value(res: dict):
    """Return the script's `result`, tolerant of response nesting differences.

    Different touchdesigner-mcp builds return the value at data.result.value,
    data.result, or data directly. Try each.
    """
    d = res.get("data")
    if isinstance(d, dict):
        r = d.get("result")
        if isinstance(r, dict) and "value" in r:
            return r["value"]
        if r is not None:
            return r
    return d


def _print_exec(res: dict) -> int:
    data = res.get("data") or {}
    out = (data.get("stdout") or "").rstrip()
    err = (data.get("stderr") or "").rstrip()
    if out:
        print(out)
    val = _result_value(res)
    if val is not None:
        print(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
    if not res.get("success", True):
        print("ERROR:", res.get("error") or err or "unknown", file=sys.stderr)
        return 1
    if err:
        print(err, file=sys.stderr)
    return 0


def cmd_perf(base: str, topn: int) -> int:
    res = exec_td(base, ("TOPN = %d\n" % topn) + PERF_SCRIPT)
    if not res.get("success", False):
        print("ERROR:", res.get("error") or (res.get("data") or {}).get("stderr"), file=sys.stderr)
        return 1
    v = _result_value(res) or {}
    fps = v.get("fps", 0) or 0
    budget = (1000.0 / fps) if fps else 0.0
    print("fps=%g  ops=%d  last-cook total=%.1fms  (budget ~%.1fms/frame)"
          % (fps, v.get("count", 0), v.get("total", 0.0), budget))
    if budget and v.get("total", 0) > budget:
        print(">>> 프레임 예산 초과 — 아래 상위 노드가 원인입니다.")
    print("slowest operators (ms):")
    for r in v.get("top", []):
        res_s = ("  %dx%d" % tuple(r["res"])) if r.get("res") else ""
        print("  %8.3f  %-42s %-5s cooks=%d%s"
              % (r["cook"], r["path"], r["family"], r["cooks"], res_s))
    return 0


def _suggest(rows: list, budget: float) -> list:
    """Build tailored, exact fix commands for the heaviest operators."""
    tips = []
    for r in rows[:6]:
        path = r["path"]
        res = r.get("res")
        if res and res[0] * res[1] > 1280 * 720:
            hw, hh = max(2, res[0] // 2), max(2, res[1] // 2)
            tips.append(
                "# %s is %dx%d, cook %.1fms — halve its resolution (if it owns its res):\n"
                "python td9981.py exec \"o=op('%s'); o.par.resolutionw=%d; o.par.resolutionh=%d\""
                % (path, res[0], res[1], r["cook"], path, hw, hh)
            )
        if r["family"] in ("DAT", "CHOP") and budget and r["cook"] > budget * 0.25:
            tips.append(
                "# %s (%s) cook %.1fms — heavy every frame; consider bypass to test cost:\n"
                "python td9981.py exec \"op('%s').bypass=True\""
                % (path, r["type"], r["cook"], path)
            )
    return tips


def cmd_optimize(base: str, topn: int, apply_safe: bool, capw: int, caph: int) -> int:
    script = ("TOPN = %d\nAPPLY_SAFE = %s\nCAPW = %d\nCAPH = %d\n"
              % (topn, bool(apply_safe), capw, caph)) + OPTIMIZE_SCRIPT
    res = exec_td(base, script)
    if not res.get("success", False):
        print("ERROR:", res.get("error") or (res.get("data") or {}).get("stderr"), file=sys.stderr)
        return 1
    v = _result_value(res) or {}
    fps = v.get("fps", 0) or 0
    budget = (1000.0 / fps) if fps else 0.0
    print("=" * 66)
    print("fps=%g  ops=%d  last-cook total=%.1fms  (budget ~%.1fms/frame)"
          % (fps, v.get("count", 0), v.get("total", 0.0), budget))
    if budget and v.get("total", 0) > budget:
        print(">>> 프레임 예산 초과 — 아래 상위 노드가 원인입니다.")
    print("-" * 66)
    print("가장 느린 오퍼레이터 (ms):")
    for r in v.get("top", []):
        res_s = ("  %dx%d" % tuple(r["res"])) if r.get("res") else ""
        print("  %8.3f  %-42s %-5s cooks=%d%s"
              % (r["cook"], r["path"], r["family"], r["cooks"], res_s))
    print("-" * 66)
    if apply_safe:
        down = v.get("downsized") or []
        print("적용됨(안전):")
        print("  · TOP 노드 뷰어 %d개 끔 (렌더 출력 영향 없음)." % v.get("viewers_off", 0))
        print("  · 과대 TOP 해상도 %d개 축소 (cap %dx%d, 자체 해상도 노드만)."
              % (len(down), capw, caph))
        for d in down:
            print("      %-42s %dx%d -> %dx%d  (%.1fms)"
                  % (d["path"], d["frm"][0], d["frm"][1], d["to"][0], d["to"][1], d.get("cook", 0.0)))
        skip = v.get("skipped") or []
        if skip:
            print("  안전상 건드리지 않은 무거운 TOP (수동 확인):")
            for s in skip[:10]:
                print("      %-42s %dx%d  %.1fms  [%s]"
                      % (s["path"], s["res"][0], s["res"][1], s.get("cook", 0.0), s.get("why", "")))
        print("  전체 되돌리기: python td9981.py undo")
    else:
        print("안전 최적화 미적용 — --apply 를 붙이면 TOP 뷰어 끄기 + 과대 TOP 해상도 축소를 "
              "한 번에 적용합니다 (모두 undo 로 복구 가능).")
    tips = _suggest(v.get("top", []), budget)
    if tips:
        print("-" * 66)
        print("추천 수정 명령 (내용 확인 후 실행하세요):")
        for t in tips:
            print(t)
    print("=" * 66)
    return 0


def cmd_undo(base: str) -> int:
    res = exec_td(base, UNDO_SCRIPT)
    if not res.get("success", False):
        print("ERROR:", res.get("error") or (res.get("data") or {}).get("stderr"), file=sys.stderr)
        return 1
    v = _result_value(res) or {}
    print("되돌리기 완료: TOP 뷰어 %d개 다시 켬, 해상도 %d개 원복."
          % (v.get("viewers_on", 0), v.get("res_restored", 0)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="td9981")
    ap.add_argument("--url", default=DEFAULT_URL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    p_perf = sub.add_parser("perf")
    p_perf.add_argument("top", nargs="?", type=int, default=20)
    p_ls = sub.add_parser("ls")
    p_ls.add_argument("path", nargs="?", default="/")
    p_exec = sub.add_parser("exec")
    p_exec.add_argument("script")
    p_opt = sub.add_parser("optimize")
    p_opt.add_argument("top", nargs="?", type=int, default=15)
    p_opt.add_argument("--apply", action="store_true",
                       help="apply safe fixes: TOP viewers off + shrink oversized TOPs (reversible)")
    p_opt.add_argument("--cap", default="1920x1080",
                       help="max resolution for oversized self-sized TOPs (default 1920x1080)")
    sub.add_parser("undo")
    args = ap.parse_args(argv)

    if args.cmd == "health":
        res = exec_td(args.url, "result = 'ok'")
        ok = res.get("success") and _result_value(res) == "ok"
        print("connected: %s responds" % args.url if ok else "unexpected: %s" % res)
        return 0 if ok else 1
    if args.cmd == "perf":
        return cmd_perf(args.url, args.top)
    if args.cmd == "ls":
        return _print_exec(exec_td(args.url, LS_SCRIPT % args.path))
    if args.cmd == "exec":
        return _print_exec(exec_td(args.url, args.script))
    if args.cmd == "optimize":
        try:
            _cw, _ch = (int(x) for x in str(args.cap).lower().split("x", 1))
        except Exception:
            print("ERROR: --cap 형식은 WxH 입니다 (예: 1920x1080).", file=sys.stderr)
            return 2
        return cmd_optimize(args.url, args.top, args.apply, _cw, _ch)
    if args.cmd == "undo":
        return cmd_undo(args.url)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
