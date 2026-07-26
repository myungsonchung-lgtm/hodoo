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
    return ((res.get("data") or {}).get("result") or {}).get("value")


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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
