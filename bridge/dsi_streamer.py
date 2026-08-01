#!/usr/bin/env python3
"""dsi_streamer - get the DSI Streamer input connected inside a running TouchDesigner.

When TouchDesigner shows the DSI Streamer feed as **not connected**, this tool
finds the stream node in the OPEN project, (re)points it at the right host/port,
and forces a fresh reconnect — then checks whether data is actually arriving.

"DSI Streamer" here means the network-input operator that carries the DSI feed
into TouchDesigner: a TCP/IP DAT (DSI-Streamer's TCP/IP output, default port
8844), or any DAT/CHOP whose name contains "dsi"/"streamer". The tool discovers
it automatically; you can also target it by name/path with --name.

It talks to the touchdesigner-mcp WebServer (port 9981), the same API used by
td9981.py / tdfast.py, so no extra install is needed (standard library only).
Run it on the SAME PC where TouchDesigner is open.

Examples
--------
    python dsi_streamer.py status                 # what is the stream node, is it live?
    python dsi_streamer.py connect                # reconnect the auto-found node
    python dsi_streamer.py connect --host 127.0.0.1 --port 8844
    python dsi_streamer.py connect --name /project1/dsi_in
    python dsi_streamer.py connect --name /project1/dsi_in --create   # make it if missing
    python dsi_streamer.py disconnect             # stop the stream (active off)
    python dsi_streamer.py --url http://127.0.0.1:9981 status

Nothing here is destructive: connect only sets address/port and toggles the
node's Active flag off→on. Undo with `disconnect` (or set Active back on).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:9981"
DSI_DEFAULT_PORT = 8844  # DSI-Streamer TCP/IP streaming default port

# --- helpers shared by every in-TD script -----------------------------------
# TARGET may be a full op path, a bare node name, or '' (auto-discover).
_HELPERS = r"""
def _pars(o):
    try:
        return {p.name.lower(): p for p in o.pars()}
    except Exception:
        return {}
def _pick(o, names):
    low = _pars(o)
    for n in names:
        if n in low:
            return low[n]
    return None
_HOST_PARS = ['netaddress', 'address', 'netaddr', 'ip', 'host', 'remoteip', 'server']
_PORT_PARS = ['port', 'netport', 'remoteport', 'serverport']
def _info(o):
    ap = _pick(o, ['active'])
    hp = _pick(o, _HOST_PARS)
    pp = _pick(o, _PORT_PARS)
    try:
        rows = int(o.numRows)
    except Exception:
        rows = None
    try:
        chans = int(o.numChans)
    except Exception:
        chans = None
    def _ev(p):
        try:
            return p.eval() if p is not None else None
        except Exception:
            return None
    return {'path': o.path, 'name': o.name, 'type': getattr(o, 'type', ''),
            'family': getattr(o, 'family', ''),
            'active': (bool(_ev(ap)) if ap is not None else None),
            'host': _ev(hp), 'port': _ev(pp),
            'rows': rows, 'chans': chans,
            'has_active': ap is not None, 'has_addr': hp is not None,
            'has_port': pp is not None}
_NET_TYPES = {'tcpip', 'udpin', 'udpout', 'oscin', 'oscout', 'websocket',
              'websocketdat', 'serial', 'serialdat', 'ndiin', 'ndi',
              'mqttclient', 'udtin', 'udtout'}
def _find(TARGET):
    allops = op('/').findChildren(maxDepth=100)
    if TARGET:
        # explicit target: exact path or exact name only (no fuzzy fallback,
        # so `connect --name X --create` really creates X when it's missing)
        o = op(TARGET)
        if o:
            return [o]
        return [c for c in allops if c.name == TARGET]
    # auto-discover: nodes named like the feed, else any network input/output
    hits = [c for c in allops
            if ('dsi' in c.name.lower() or 'streamer' in c.name.lower())]
    if hits:
        return hits
    return [c for c in allops if getattr(c, 'type', '').lower() in _NET_TYPES]
"""

# Variables (TARGET, HOST, ...) are injected as a header via _hdr() at call time,
# NOT %-formatted into the body — the bodies contain literal '%' that would clash.
STATUS_SCRIPT = _HELPERS + r"""
result = {'nodes': [_info(o) for o in _find(TARGET)], 'target': TARGET}
"""

# Set host/port, optionally create the node, then toggle Active off->on so the
# socket reconnects from scratch. Returns what it did + fresh node info.
CONNECT_SCRIPT = _HELPERS + r"""
targets = _find(TARGET)
created = False
err = None
if not targets and CREATE and TARGET:
    parent = op(PARENT) or op('/project1') or op('/')
    name = TARGET.split('/')[-1]
    try:
        newop = parent.create(tcpipDAT, name)
        targets = [newop]
        created = True
    except Exception as e:
        err = 'create failed: %s' % e
acted = []
for o in targets:
    hp = _pick(o, _HOST_PARS)
    pp = _pick(o, _PORT_PARS)
    ap = _pick(o, ['active'])
    changed = {}
    if HOST and hp is not None:
        try:
            hp.val = HOST
            changed['host'] = HOST
        except Exception:
            pass
    if PORT and pp is not None:
        try:
            pp.val = int(PORT)
            changed['port'] = int(PORT)
        except Exception:
            pass
    elif created and pp is not None and DEFPORT:
        try:
            pp.val = int(DEFPORT)
            changed['port'] = int(DEFPORT)
        except Exception:
            pass
    reconnected = False
    if ap is not None:
        try:
            ap.val = 0
            ap.val = 1
            reconnected = True
        except Exception:
            pass
    acted.append({'path': o.path, 'changed': changed,
                  'reconnected': reconnected, 'has_active': ap is not None})
result = {'acted': acted, 'created': created, 'error': err,
          'info': [_info(o) for o in targets]}
"""

DISCONNECT_SCRIPT = _HELPERS + r"""
off = []
for o in _find(TARGET):
    ap = _pick(o, ['active'])
    if ap is not None:
        try:
            ap.val = 0
            off.append(o.path)
        except Exception:
            pass
result = {'off': off}
"""


def _hdr(**kw) -> str:
    """Build a Python header assigning each kwarg via repr() (safe injection)."""
    return "".join("%s = %r\n" % (k, v) for k, v in kw.items())


def _coerce(x):
    """Some 9981 builds return nested values as JSON/py-literal strings."""
    if isinstance(x, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                return parse(x)
            except Exception:
                pass
    return x


def call(url: str, script: str, timeout: float = 30.0) -> dict:
    body = json.dumps({"script": script}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/td/server/exec",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise SystemExit(
            "TouchDesigner(9981)에 연결할 수 없습니다: %s\n"
            "TD가 열려 있고 mcp_webserver_base .tox가 실행 중인지 확인하세요 "
            "(브라우저에서 %s/health)." % (e, url.rstrip("/"))
        )


def result_of(res: dict, key: str):
    """Pull the script's `result`, tolerant of response-nesting differences."""
    d = res.get("data")
    cands = [d]
    if isinstance(d, dict):
        cands.append(d.get("result"))
        if isinstance(d.get("result"), dict):
            cands.append(d["result"].get("value"))
    for c in cands:
        c = _coerce(c)
        if isinstance(c, dict) and key in c:
            return c
    return None


def _fmt_node(n: dict) -> str:
    n = _coerce(n)
    act = {True: "on", False: "off", None: "?"}[n.get("active")]
    addr = n.get("host")
    port = n.get("port")
    where = ""
    if addr is not None or port is not None:
        where = "  %s:%s" % (addr if addr is not None else "?",
                             port if port is not None else "?")
    load = n.get("rows")
    load_s = ("rows=%s" % load) if load is not None else (
        "chans=%s" % n.get("chans") if n.get("chans") is not None else "")
    return "  %-38s %-9s active=%-3s%s  %s" % (
        n.get("path"), n.get("type"), act, where, load_s)


def _liveness(node: dict) -> tuple[bool, int]:
    """(has_data, load) — rows for DATs, channels for CHOPs."""
    node = _coerce(node)
    load = node.get("rows")
    if load is None:
        load = node.get("chans")
    load = int(load or 0)
    return load > 0, load


# --- commands ----------------------------------------------------------------
def cmd_status(url: str, target: str) -> int:
    v = result_of(call(url, _hdr(TARGET=target) + STATUS_SCRIPT), "nodes")
    if v is None:
        print("결과 구조를 못 찾음. TD 서버 응답을 확인하세요.")
        return 1
    nodes = [_coerce(n) for n in (_coerce(v.get("nodes")) or [])]
    if not nodes:
        print("스트림 노드를 못 찾았습니다.")
        print("  - DSI Streamer 입력 노드(TCP/IP DAT 등)가 프로젝트에 있는지 확인하거나")
        print("  - 이름을 지정해 다시 시도: python dsi_streamer.py status --name <경로>")
        print("  - 없으면 새로 만들기: python dsi_streamer.py connect --name /project1/dsi_in --create")
        return 1
    print("DSI Streamer 후보 노드 %d개:" % len(nodes))
    for n in nodes:
        live, _ = _liveness(n)
        flag = "  ← 데이터 수신중" if (n.get("active") and live) else (
            "  ← 활성이지만 데이터 없음" if n.get("active") else "  ← 비활성(not connected)")
        print(_fmt_node(n) + flag)
    return 0


def cmd_connect(url: str, target: str, host: str, port_arg: str,
                create: bool, parent: str, wait: float) -> int:
    # snapshot before, so we can tell whether data started flowing
    before = result_of(call(url, _hdr(TARGET=target) + STATUS_SCRIPT), "nodes")
    before_load = {}
    for n in (_coerce((before or {}).get("nodes")) or []):
        n = _coerce(n)
        before_load[n.get("path")] = _liveness(n)[1]

    script = _hdr(TARGET=target, HOST=host, PORT=port_arg, CREATE=bool(create),
                  PARENT=parent, DEFPORT=DSI_DEFAULT_PORT) + CONNECT_SCRIPT
    v = result_of(call(url, script), "acted")
    if v is None:
        print("결과 구조를 못 찾음. TD 서버 응답을 확인하세요.")
        return 1
    if v.get("error"):
        print("오류:", v.get("error"))
    acted = [_coerce(a) for a in (_coerce(v.get("acted")) or [])]
    if not acted:
        print("연결할 스트림 노드를 못 찾았습니다.")
        print("  새로 만들려면: python dsi_streamer.py connect --name /project1/dsi_in --create"
              " [--host H] [--port P]")
        return 1
    if v.get("created"):
        print(">> 새 TCP/IP DAT를 만들었습니다.")
    for a in acted:
        ch = a.get("changed") or {}
        bits = []
        if "host" in ch:
            bits.append("host=%s" % ch["host"])
        if "port" in ch:
            bits.append("port=%s" % ch["port"])
        setmsg = ("  설정: " + ", ".join(bits)) if bits else ""
        if a.get("reconnected"):
            print(">> %s 재연결(Active off→on)%s" % (a.get("path"), setmsg))
        elif not a.get("has_active"):
            print(">> %s 에 Active 파라미터가 없어 토글 못함%s" % (a.get("path"), setmsg))
        else:
            print(">> %s 처리%s" % (a.get("path"), setmsg))

    # give sockets a moment, then verify data is actually arriving
    if wait > 0:
        time.sleep(wait)
    after = result_of(call(url, _hdr(TARGET=target) + STATUS_SCRIPT), "nodes")
    print("-" * 60)
    any_live = False
    for n in (_coerce((after or {}).get("nodes")) or []):
        n = _coerce(n)
        live, load = _liveness(n)
        delta = load - before_load.get(n.get("path"), 0)
        if n.get("active") and (live or delta > 0):
            any_live = True
            print("연결됨 ✓  %s  (데이터 수신중, load=%s%s)"
                  % (n.get("path"), load, (" +%d" % delta) if delta else ""))
        elif n.get("active"):
            print("활성 but 데이터 없음  %s  (%s:%s)"
                  % (n.get("path"), n.get("host"), n.get("port")))
    if not any_live:
        print("아직 데이터가 안 들어옵니다. 확인하세요:")
        print("  1) DSI-Streamer 앱에서 TCP/IP 스트리밍이 켜져 있는가 (기본 포트 %d)" % DSI_DEFAULT_PORT)
        print("  2) 주소/포트가 맞는가 — 필요시: --host <IP> --port <PORT>")
        print("  3) 헤드셋이 DSI-Streamer에 연결되어 실제로 신호가 나오는가")
        return 1
    return 0


def cmd_disconnect(url: str, target: str) -> int:
    v = result_of(call(url, _hdr(TARGET=target) + DISCONNECT_SCRIPT), "off")
    off = _coerce((v or {}).get("off")) or []
    if off:
        print("Active off: %s" % ", ".join(off))
    else:
        print("끌 노드를 못 찾았습니다.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dsi_streamer",
                                 description="Connect the DSI Streamer feed in a running TouchDesigner.")
    ap.add_argument("--url", default=DEFAULT_URL, help="TD server base url (default %s)" % DEFAULT_URL)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_st = sub.add_parser("status", help="show the stream node(s) and whether data is flowing")
    p_st.add_argument("--name", default="", help="target node name or path (default: auto-find)")

    p_cn = sub.add_parser("connect", help="(re)connect the stream node")
    p_cn.add_argument("--name", default="", help="target node name or path (default: auto-find)")
    p_cn.add_argument("--host", default="", help="DSI-Streamer host/IP to point at")
    p_cn.add_argument("--port", default="", help="DSI-Streamer TCP port (default keep / %d)" % DSI_DEFAULT_PORT)
    p_cn.add_argument("--create", action="store_true",
                      help="create a TCP/IP DAT if --name doesn't exist")
    p_cn.add_argument("--parent", default="/project1",
                      help="where to create the node (default /project1)")
    p_cn.add_argument("--wait", type=float, default=2.0,
                      help="seconds to wait before checking for data (default 2)")

    p_dc = sub.add_parser("disconnect", help="turn the stream node's Active off")
    p_dc.add_argument("--name", default="", help="target node name or path (default: auto-find)")

    args = ap.parse_args(argv)
    if args.cmd == "status":
        return cmd_status(args.url, args.name)
    if args.cmd == "connect":
        return cmd_connect(args.url, args.name, args.host, args.port,
                           args.create, args.parent, args.wait)
    if args.cmd == "disconnect":
        return cmd_disconnect(args.url, args.name)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
