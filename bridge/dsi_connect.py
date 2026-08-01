#!/usr/bin/env python3
"""dsi_connect - diagnose why the DSI-Streamer app itself shows "disconnected".

This is the layer BEFORE TouchDesigner. A DSI (Dry Sensor Interface) headset
pairs over Bluetooth and shows up on your PC as a virtual serial / COM port;
the DSI-Streamer app connects to that port. When DSI-Streamer says
"disconnected", the app never reached the headset over that port — TouchDesigner
is not involved yet. (Once DSI-Streamer connects, use dsi_streamer.py to get the
feed into TouchDesigner over TCP.)

    [headset] --Bluetooth (virtual COM port)--> [DSI-Streamer] --TCP 8844--> [TD]
              ^^^^^^^^^^^^^ THIS tool ^^^^^^^^^^^^              dsi_streamer.py

What this tool does (no TouchDesigner, standard library only; uses pyserial if
installed for the most reliable results):

    python dsi_connect.py ports        # list serial ports, flag likely-DSI, test if busy
    python dsi_connect.py check COM4   # is this port free, or held by another app?
    python dsi_connect.py check /dev/cu.DSI7-0123
    python dsi_connect.py guide        # step-by-step: get DSI-Streamer connected

The single most common reason DSI-Streamer stays "disconnected" even though
everything looks right: the COM port is the wrong one, or it is already held by
another process (a stale DSI-Streamer, a serial DAT in TouchDesigner, or a crash
that never released it). `ports` / `check` find exactly that.

TCP/IP layer
------------
DSI-Streamer can also stream over TCP/IP: it runs a TCP **server** (default
127.0.0.1:8844) and its "TCP/IP" panel shows "not connected" until a **client**
connects to that port. TouchDesigner is normally that client. The `tcp` command
connects a plain socket to prove the server is up (and, with --hold, keeps the
DSI-Streamer TCP status "connected" without TouchDesigner):

    python dsi_connect.py tcp                 # can we reach DSI-Streamer's TCP server?
    python dsi_connect.py tcp --read          # connect and confirm data is streaming
    python dsi_connect.py tcp --hold          # stay connected (Ctrl-C to stop)
    python dsi_connect.py tcp --host 127.0.0.1 --port 8844
"""
from __future__ import annotations

import argparse
import glob
import socket
import subprocess
import sys
import time

DSI_TCP_PORT = 8844  # DSI-Streamer TCP/IP streaming default port

# Substrings that suggest a port is the DSI headset's Bluetooth link.
_DSI_HINTS = ("dsi", "wearable", "rfcomm", "bluetooth", "serial over bluetooth",
              "standard serial over bluetooth", "bt port")


def _has_pyserial():
    try:
        import serial  # noqa: F401
        import serial.tools.list_ports  # noqa: F401
        return True
    except Exception:
        return False


def _looks_like_dsi(*fields: str) -> bool:
    blob = " ".join(f for f in fields if f).lower()
    if "incoming" in blob:  # macOS generic Bluetooth-Incoming-Port, never the DSI link
        return False
    return any(h in blob for h in _DSI_HINTS)


# --- port enumeration --------------------------------------------------------
def _ports_pyserial() -> list[dict]:
    import serial.tools.list_ports as lp
    rows = []
    for p in lp.comports():
        rows.append({
            "device": p.device,
            "desc": (p.description or "").strip(),
            "hwid": (p.hwid or "").strip(),
            "dsi": _looks_like_dsi(p.device, p.description, p.hwid,
                                   getattr(p, "manufacturer", "") or ""),
        })
    return rows


def _ports_fallback() -> list[dict]:
    """List ports without pyserial (device path only, no description)."""
    rows = []
    if sys.platform == "darwin":
        # /dev/cu.* is the one to connect to (calling-unit); tty.* is incoming.
        devs = sorted(set(glob.glob("/dev/cu.*") + glob.glob("/dev/tty.*")))
    elif sys.platform.startswith("linux"):
        devs = sorted(set(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
                          + glob.glob("/dev/rfcomm*")))
    elif sys.platform.startswith("win"):
        devs = _win_com_ports()
    else:
        devs = []
    for d in devs:
        rows.append({"device": d, "desc": "", "hwid": "", "dsi": _looks_like_dsi(d)})
    return rows


def _win_com_ports() -> list[str]:
    """Best-effort COM port list on Windows without pyserial (via PowerShell)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[System.IO.Ports.SerialPort]::GetPortNames() -join ','"],
            capture_output=True, text=True, timeout=10)
        names = (out.stdout or "").strip()
        return [n for n in names.split(",") if n.strip()]
    except Exception:
        return []


def list_ports() -> tuple[list[dict], bool]:
    if _has_pyserial():
        return _ports_pyserial(), True
    return _ports_fallback(), False


# --- open / busy test --------------------------------------------------------
def probe_port(device: str) -> tuple[str, str]:
    """Return (state, detail). state in {free, busy, missing, unknown}.

    Needs pyserial. 'busy' means another process holds the port — that alone
    keeps DSI-Streamer from connecting.
    """
    if not _has_pyserial():
        return "unknown", "pyserial 미설치 — `pip install pyserial` 후 다시 시도"
    import serial
    try:
        s = serial.Serial(device)  # opening then closing is the standard test
        s.close()
        return "free", "열 수 있음 — DSI-Streamer가 이 포트로 붙을 수 있는 상태"
    except serial.SerialException as e:
        msg = str(e).lower()
        if any(k in msg for k in ("permission", "access is denied", "busy",
                                  "in use", "resource")):
            return "busy", "다른 프로그램이 포트를 잡고 있음 (%s)" % e
        if any(k in msg for k in ("no such", "could not open", "filenotfound",
                                  "cannot find")):
            return "missing", "그런 포트가 없음 — 헤드셋이 페어링/전원 상태인지 확인 (%s)" % e
        return "unknown", str(e)
    except Exception as e:
        return "unknown", str(e)


# --- commands ----------------------------------------------------------------
def cmd_ports() -> int:
    rows, precise = list_ports()
    if not precise:
        print("(pyserial 미설치 — 포트 이름만 표시합니다. 더 정확한 진단: pip install pyserial)")
    if not rows:
        print("시리얼/COM 포트를 하나도 못 찾았습니다.")
        print("  → 헤드셋 전원이 켜져 있고 블루투스로 '페어링'되어 있는지 먼저 확인하세요.")
        print("    (페어링이 되어야 가상 COM 포트가 생깁니다. 그 다음 DSI-Streamer에서 선택)")
        return 1
    dsi = [r for r in rows if r["dsi"]]
    print("시리얼/COM 포트 %d개%s:" % (len(rows), (", DSI 후보 %d개" % len(dsi)) if dsi else ""))
    for r in rows:
        mark = "  ★DSI 후보" if r["dsi"] else ""
        state = ""
        if _has_pyserial():
            st, _ = probe_port(r["device"])
            state = {"free": "  [열림가능]", "busy": "  [사용중=다른앱이잡음]",
                     "missing": "  [없음]", "unknown": ""}.get(st, "")
        desc = ("  — " + r["desc"]) if r["desc"] else ""
        print("  %-24s%s%s%s" % (r["device"], desc, state, mark))
    print("-" * 60)
    if dsi:
        print("→ DSI-Streamer 앱의 포트 선택에서 위 ★ 포트를 고르고 Connect 하세요.")
        if sys.platform == "darwin":
            print("  (macOS는 tty.* 가 아니라 cu.* 로 연결하세요.)")
        busy = [r["device"] for r in dsi
                if _has_pyserial() and probe_port(r["device"])[0] == "busy"]
        if busy:
            print("  ⚠ %s 는 이미 다른 앱이 잡고 있습니다 — 그 앱(이전 DSI-Streamer/TD serial DAT 등)을")
            print("     닫거나, 안 되면 블루투스 페어링 해제→재연결(또는 재부팅) 후 다시 Connect.")
    else:
        print("→ 이름만으로는 DSI 포트를 특정 못했습니다. `check <포트>` 로 각 포트를 열어보거나,")
        print("  헤드셋을 껐다 켜서 어떤 포트가 새로 생기는지 비교하세요.")
    print("자세한 순서: python dsi_connect.py guide")
    return 0


def cmd_check(device: str) -> int:
    state, detail = probe_port(device)
    label = {"free": "사용 가능", "busy": "사용 중(다른 앱이 잡음)",
             "missing": "없음", "unknown": "알 수 없음"}.get(state, state)
    print("%s : %s" % (device, label))
    print("  %s" % detail)
    if state == "busy":
        print("  조치: 그 포트를 쓰는 프로그램을 닫으세요 — 흔한 범인:")
        print("    · 이전에 떠 있던 DSI-Streamer 창 (완전 종료)")
        print("    · TouchDesigner의 Serial DAT/CHOP 가 같은 포트를 Active로 잡음")
        print("    · 크래시로 포트가 안 풀림 → 블루투스 재페어링 또는 재부팅")
    elif state == "missing":
        print("  조치: 헤드셋 전원 ON + 블루투스 페어링을 먼저 확인하세요 (포트가 생겨야 함).")
    elif state == "free":
        print("  → 포트는 정상. DSI-Streamer에서 이 포트를 선택해 Connect 하세요.")
        print("     그래도 disconnected면: 헤드셋 배터리/전원, 올바른 포트인지, 앱 버전을 확인.")
    return 0 if state == "free" else 1


_GUIDE = """DSI-Streamer 연결 순서 (앱이 'disconnected'일 때)

1. 헤드셋 전원 ON — 배터리가 충분한지 확인 (저전압이면 붙었다 끊깁니다).
2. 블루투스 페어링 — OS 블루투스 설정에서 DSI 헤드셋을 '페어링' 합니다.
   페어링이 되면 가상 시리얼/COM 포트가 생깁니다.
     · Windows: 장치관리자 > 포트(COM & LPT) 에 "Standard Serial over
       Bluetooth link (COMx)" 로 보통 2개 생김 → 'Outgoing' 쪽 COM 사용.
     · macOS: /dev/cu.DSI... (tty. 가 아니라 cu. 를 사용).
3. 포트 확인 — python dsi_connect.py ports  (★DSI 후보 + 사용중 여부 표시)
4. 포트 점검 — python dsi_connect.py check <포트>
     · '사용 중' 이면 그 포트를 잡은 앱(이전 DSI-Streamer, TD serial DAT 등)을 닫기.
     · '없음' 이면 2번 페어링/전원부터 다시.
5. DSI-Streamer 앱에서 그 포트를 선택하고 Connect. 여기서 'connected' 가 뜨면 성공.
6. 그 다음에야 TouchDesigner로: DSI-Streamer의 TCP/IP 스트리밍을 켜고(기본 8844),
   python dsi_streamer.py connect --port 8844  로 TD 노드를 붙입니다.

가장 흔한 함정: (a) 포트를 다른 앱이 잡고 있음, (b) COM 포트를 잘못 고름(2개 중 하나),
(c) 헤드셋 저전압, (d) 페어링은 됐지만 헤드셋이 sleep. 위 3~4번으로 (a)(b)를 바로 잡습니다.
"""


def cmd_guide() -> int:
    print(_GUIDE)
    return 0


# --- TCP/IP layer: DSI-Streamer's TCP server -------------------------------
def probe_tcp(host: str, port: int, timeout: float = 3.0):
    """Try to open a TCP connection. Return (sock_or_None, state, detail).

    state in {connected, refused, timeout, error}. On 'connected' the caller
    owns the returned socket and must close it.
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        return s, "connected", "TCP 서버에 접속됨"
    except ConnectionRefusedError as e:
        return None, "refused", "접속 거부 — 그 host:port 에서 서버가 안 열려 있음 (%s)" % e
    except socket.timeout:
        return None, "timeout", "응답 없음(timeout) — 방화벽이거나 다른 IP일 수 있음"
    except OSError as e:
        return None, "error", str(e)


def cmd_tcp(host: str, port: int, do_read: bool, hold: bool, read_timeout: float) -> int:
    sock, state, detail = probe_tcp(host, port)
    tag = {"connected": "연결됨 ✓", "refused": "거부됨", "timeout": "응답없음",
           "error": "오류"}.get(state, state)
    print("%s:%d  →  %s" % (host, port, tag))
    print("  %s" % detail)

    if state != "connected":
        print("  조치:")
        print("    1) DSI-Streamer 앱에서 'TCP/IP' 스트리밍을 실제로 켰는지 (Start/Enable)")
        print("    2) 포트가 %d 이 맞는지 (앱 설정과 --port 를 일치)" % port)
        print("    3) 앱이 127.0.0.1 이 아닌 다른 IP로 열려 있진 않은지 (--host 로 지정)")
        print("    4) 방화벽이 그 포트를 막고 있진 않은지")
        return 1

    # connected — optionally confirm data actually streams
    try:
        if do_read or hold:
            sock.settimeout(read_timeout)
            first = b""
            try:
                first = sock.recv(4096)
            except socket.timeout:
                first = b""
            if first:
                print("  데이터 수신중: 첫 %d바이트 도착 (스트리밍 정상)" % len(first))
            else:
                print("  접속은 됐지만 %.0f초간 데이터 없음 — 앱에서 스트리밍이 '시작' 상태인지,"
                      % read_timeout)
                print("    헤드셋 신호가 실제로 흐르는지 확인하세요.")
            if hold:
                total = len(first)
                print("  --hold: 접속 유지중. 이 창을 열어두면 DSI-Streamer TCP 상태가"
                      " 'connected' 로 유지됩니다. (Ctrl-C 로 종료)")
                sock.settimeout(1.0)
                try:
                    while True:
                        try:
                            chunk = sock.recv(65536)
                        except socket.timeout:
                            chunk = b""
                        if chunk:
                            total += len(chunk)
                            print("\r  누적 %d bytes" % total, end="", flush=True)
                        else:
                            time.sleep(0.1)
                except KeyboardInterrupt:
                    print("\n  종료.")
        else:
            print("  → DSI-Streamer 의 TCP/IP 상태가 이제 'connected' 로 바뀌었을 겁니다.")
            print("    (이 스크립트는 곧 접속을 닫습니다. 계속 유지하려면 --hold,")
            print("     실제 소비자는 TouchDesigner 입니다 → python dsi_streamer.py connect --port %d)" % port)
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="dsi_connect",
        description="Diagnose the DSI-Streamer app <-> headset (serial/COM) connection.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ports", help="list serial ports, flag likely-DSI and busy ports")
    p_ck = sub.add_parser("check", help="test whether a specific port is free or busy")
    p_ck.add_argument("device", help="port, e.g. COM4 or /dev/cu.DSI7-0123")
    sub.add_parser("guide", help="print the step-by-step connection checklist")
    p_tcp = sub.add_parser("tcp", help="probe DSI-Streamer's TCP server (default 127.0.0.1:8844)")
    p_tcp.add_argument("--host", default="127.0.0.1")
    p_tcp.add_argument("--port", type=int, default=DSI_TCP_PORT)
    p_tcp.add_argument("--read", action="store_true", help="after connecting, confirm data is streaming")
    p_tcp.add_argument("--hold", action="store_true", help="keep the connection open (Ctrl-C to stop)")
    p_tcp.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for data (default 3)")
    args = ap.parse_args(argv)

    if args.cmd in (None, "ports"):
        return cmd_ports()
    if args.cmd == "check":
        return cmd_check(args.device)
    if args.cmd == "guide":
        return cmd_guide()
    if args.cmd == "tcp":
        return cmd_tcp(args.host, args.port, args.read, args.hold, args.timeout)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
