#!/usr/bin/env python3
"""toe_ctl - control a running TouchDesigner project from the terminal.

Talks to the WebServer DAT bridge created by td_setup.py. Sends Python code to
TouchDesigner, which runs it and returns stdout / the resulting value / errors.

Requires no third-party packages (standard library only).

Usage
-----
Interactive REPL:
    python toe_ctl.py
    python toe_ctl.py --port 9980

One-shot expression / statement:
    python toe_ctl.py "op('/').name"
    python toe_ctl.py "op('/project1/moviefilein1').par.file = 'D:/clips/a.mov'"

Run a local .py file inside TouchDesigner:
    python toe_ctl.py --file setup_scene.py

Options
-------
    --host HOST     default 127.0.0.1
    --port PORT     default 9980
    --url  URL      full base url (overrides host/port), e.g. http://127.0.0.1:9980
    --token TOKEN   shared secret, if the bridge has one set
    --file FILE     send the contents of a local python file, then exit

REPL dot-commands
-----------------
    .help              show help
    .ping              check the connection
    .ls [path]         list child operators of an op (default '/')
    .pars <path>       list an operator's parameters and current values
    .perf [N]          rank the N slowest operators by cook time (default 20)
    .file <path>       run a local .py file inside TouchDesigner
    .quit / .exit      leave (Ctrl-D also works)

In the REPL, if a line ends with ':' or '\\', you enter multi-line mode:
keep typing and finish the block with an empty line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


class Bridge:
    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def exec(self, code: str) -> dict:
        """Send code to TouchDesigner and return the result dict."""
        body = json.dumps({"code": code, "token": self.token}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/exec",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "stdout": "", "value": None,
                    "error": "non-JSON response: " + raw[:500]}

    def ping(self) -> bool:
        try:
            req = urllib.request.Request(self.base_url + "/ping", method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            return bool(data.get("ok"))
        except Exception:
            return False


def render(result: dict) -> str:
    """Format a result dict for display."""
    lines = []
    out = result.get("stdout") or ""
    if out:
        lines.append(out.rstrip("\n"))
    if not result.get("ok", False):
        err = result.get("error") or "unknown error"
        lines.append(err.rstrip("\n"))
    else:
        val = result.get("value")
        if val is not None:  # None == a statement with no return value
            lines.append(val)
    return "\n".join(lines)


# --- REPL dot-command helpers ------------------------------------------------
def _ls_code(path: str) -> str:
    return (
        "(lambda _o: sorted(c.path for c in _o.children) if _o "
        "else 'no such op: %s')(op(%r))" % (path, path)
    )


def _pars_code(path: str) -> str:
    return (
        "(lambda _o: {p.name: p.val for p in _o.pars()} if _o "
        "else 'no such op: %s')(op(%r))" % (path, path)
    )


# Profiler that runs inside TouchDesigner and prints a JSON report to stdout.
# Kept in sync with PERF_PROBE in src/toe_mcp/server.py.
_PERF_PROBE = r'''
import json as _json
_TOPN = %d
try:
    _ops = op('/').findChildren(maxDepth=100)
except Exception:
    _ops = []
_rows = []
for _o in _ops:
    def _num(_v):
        try:
            return float(_v or 0.0)
        except Exception:
            return 0.0
    _row = {'path': _o.path, 'type': getattr(_o, 'type', ''),
            'family': getattr(_o, 'family', ''),
            'cook': round(_num(getattr(_o, 'cookTime', 0.0)), 3),
            'cooks': int(getattr(_o, 'totalCooks', 0) or 0)}
    try:
        if _o.family == 'TOP':
            _row['res'] = [int(_o.width), int(_o.height)]
    except Exception:
        pass
    _rows.append(_row)
_rows.sort(key=lambda r: r['cook'], reverse=True)
try:
    _fps = float(getattr(project, 'cookRate', 0.0) or 0.0)
except Exception:
    _fps = 0.0
print(_json.dumps({'summary': {'target_fps': _fps, 'op_count': len(_rows),
      'total_last_cook_ms': round(sum(r['cook'] for r in _rows), 3)},
      'top': _rows[:_TOPN]}))
'''


def _render_perf(result: dict) -> str:
    import json as _json
    if not result.get("ok", False):
        return "ERROR:\n" + (result.get("error") or "unknown error")
    try:
        data = _json.loads((result.get("stdout") or "").strip())
    except _json.JSONDecodeError:
        return "unexpected profiler output:\n" + (result.get("stdout") or "")
    s = data.get("summary", {})
    lines = ["fps=%g  ops=%d  last-cook total=%.1fms"
             % (s.get("target_fps", 0), s.get("op_count", 0), s.get("total_last_cook_ms", 0.0)),
             "slowest (ms):"]
    for r in data.get("top", []):
        res = (" %dx%d" % tuple(r["res"])) if r.get("res") else ""
        lines.append("  %8.3f  %-40s %-5s cooks=%d%s"
                     % (r["cook"], r["path"], r["family"], r["cooks"], res))
    return "\n".join(lines)


def run_repl(bridge: Bridge) -> int:
    connected = bridge.ping()
    status = "connected" if connected else "NOT reachable (is TouchDesigner running td_setup?)"
    print("toe_ctl -> %s  [%s]" % (bridge.base_url, status))
    print("Type Python to run it in TouchDesigner. .help for commands, Ctrl-D to quit.")

    while True:
        try:
            line = input("td> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        stripped = line.strip()
        if not stripped:
            continue

        # dot-commands
        if stripped == ".help":
            print(__doc__)
            continue
        if stripped in (".quit", ".exit"):
            return 0
        if stripped == ".ping":
            print("ok" if bridge.ping() else "unreachable")
            continue
        if stripped == ".ls" or stripped.startswith(".ls "):
            path = stripped[3:].strip() or "/"
            print(render(bridge.exec(_ls_code(path))))
            continue
        if stripped.startswith(".pars "):
            path = stripped[6:].strip()
            print(render(bridge.exec(_pars_code(path))))
            continue
        if stripped == ".perf" or stripped.startswith(".perf "):
            arg = stripped[5:].strip()
            topn = int(arg) if arg.isdigit() else 20
            print(_render_perf(bridge.exec(_PERF_PROBE % topn)))
            continue
        if stripped.startswith(".file "):
            path = stripped[6:].strip()
            code = _read_file(path)
            if code is None:
                continue
            print(render(bridge.exec(code)))
            continue

        # multi-line block mode
        code = line
        if stripped.endswith(":") or stripped.endswith("\\"):
            buf = [line.rstrip("\\")]
            while True:
                try:
                    cont = input("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if cont.strip() == "":
                    break
                buf.append(cont.rstrip("\\"))
            code = "\n".join(buf)

        try:
            print(render(bridge.exec(code)))
        except urllib.error.URLError as e:
            print("connection error: %s" % e, file=sys.stderr)


def _read_file(path: str) -> str | None:
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        print("cannot read file: %s" % e, file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="toe_ctl",
        description="Control a running TouchDesigner project from the terminal.",
    )
    ap.add_argument("code", nargs="?", help="Python to run in TD (omit for REPL)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9980)
    ap.add_argument("--url", default=None, help="base url; overrides host/port")
    ap.add_argument("--token", default=os.environ.get("TD_BRIDGE_TOKEN", ""))
    ap.add_argument("--file", default=None, help="run a local .py file in TD, then exit")
    args = ap.parse_args(argv)

    base = args.url or os.environ.get("TD_BRIDGE_URL") or "http://%s:%d" % (args.host, args.port)
    bridge = Bridge(base, token=args.token)

    # one-shot: --file
    if args.file:
        code = _read_file(args.file)
        if code is None:
            return 1
        return _oneshot(bridge, code)

    # one-shot: positional code
    if args.code:
        return _oneshot(bridge, args.code)

    # interactive
    return run_repl(bridge)


def _oneshot(bridge: Bridge, code: str) -> int:
    try:
        result = bridge.exec(code)
    except urllib.error.URLError as e:
        print("connection error: %s (is TouchDesigner running td_setup?)" % e, file=sys.stderr)
        return 2
    text = render(result)
    if text:
        print(text)
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
