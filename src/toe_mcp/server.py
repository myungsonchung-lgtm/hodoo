"""MCP server for reading and writing TouchDesigner ``.toe`` files.

A ``.toe`` file is a binary container. The only reliable, officially supported
way to read/write its contents directly is TouchDesigner's own command line
utilities ``toeexpand`` and ``toecollapse`` (they ship inside every
TouchDesigner install). ``toeexpand`` disassembles a ``.toe`` into a tree of
ASCII text files; ``toecollapse`` rebuilds the ``.toe`` from that tree.

This server wraps that round-trip so an assistant can:

    expand  ->  read / search / edit the ASCII files  ->  collapse

All tools operate on files on the local machine where this server runs.
"""

from __future__ import annotations

import ast
import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("toe-mcp")

# --- how long we let a toeexpand/toecollapse run before giving up (seconds) ---
_RUN_TIMEOUT = 600


# ---------------------------------------------------------------------------
# Locating the TouchDesigner command line tools
# ---------------------------------------------------------------------------
def _candidate_bin_dirs() -> list[Path]:
    """Common locations of the TouchDesigner ``bin`` folder, best guess first."""
    dirs: list[Path] = []

    # 1. Explicit override: TD_BIN points at the TouchDesigner bin directory.
    env_bin = os.environ.get("TD_BIN")
    if env_bin:
        dirs.append(Path(env_bin))

    system = platform.system()
    if system == "Windows":
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Derivative",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Derivative",
        ]
        for root in roots:
            if root.is_dir():
                # Newest install last-modified first.
                installs = sorted(
                    (p for p in root.glob("TouchDesigner*") if p.is_dir()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                dirs.extend(p / "bin" for p in installs)
    elif system == "Darwin":
        for app in sorted(Path("/Applications").glob("TouchDesigner*.app"), reverse=True):
            dirs.append(app / "Contents" / "MacOS")
        dirs.append(Path("/Applications/TouchDesigner.app/Contents/MacOS"))
    else:  # Linux (community builds)
        for base in (Path.home() / "Derivative", Path("/opt/Derivative")):
            if base.is_dir():
                for p in sorted(base.glob("TouchDesigner*"), reverse=True):
                    dirs.append(p / "bin")

    return dirs


def _find_tool(name: str) -> str | None:
    """Return the full path to a TouchDesigner tool (``toeexpand``/``toecollapse``)."""
    exe = f"{name}.exe" if platform.system() == "Windows" else name

    # 1. Direct override of the exact binary path.
    override = os.environ.get(name.upper())  # TOEEXPAND / TOECOLLAPSE
    if override and Path(override).is_file():
        return str(Path(override))

    # 2. On PATH.
    found = shutil.which(exe) or shutil.which(name)
    if found:
        return found

    # 3. Scan known install directories.
    for bin_dir in _candidate_bin_dirs():
        cand = bin_dir / exe
        if cand.is_file():
            return str(cand)

    return None


def _require_tool(name: str) -> str:
    path = _find_tool(name)
    if not path:
        raise RuntimeError(
            f"Could not find '{name}'. It ships with TouchDesigner in the "
            f"install 'bin' folder. Set the TD_BIN environment variable to that "
            f"folder (e.g. C:\\Program Files\\Derivative\\TouchDesigner\\bin), or "
            f"set {name.upper()} to the tool's full path."
        )
    return path


def _run(cmd: list[str], cwd: Path) -> str:
    """Run a command, returning combined stdout/stderr; raise on failure."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_RUN_TIMEOUT,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\n{out.strip()}"
        )
    return out.strip()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def _resolve_toe(toe_path: str) -> Path:
    p = Path(toe_path).expanduser().resolve()
    if p.suffix.lower() != ".toe":
        raise ValueError(f"Not a .toe file: {toe_path}")
    return p


def _expanded_dir_for(toe: Path) -> Path | None:
    """Locate the expanded directory produced by toeexpand for ``toe``."""
    # toeexpand naming has varied across versions; accept both forms.
    for cand in (toe.parent / f"{toe.name}.dir", toe.parent / f"{toe.stem}.dir"):
        if cand.is_dir():
            return cand
    return None


def _require_expanded_dir(toe: Path) -> Path:
    d = _expanded_dir_for(toe)
    if d is None:
        raise RuntimeError(
            f"No expanded directory found for {toe.name}. "
            f"Run expand_toe() on it first."
        )
    return d


def _safe_inner(expanded: Path, inner_path: str) -> Path:
    """Resolve ``inner_path`` inside ``expanded`` and block path traversal."""
    target = (expanded / inner_path).resolve()
    if expanded not in target.parents and target != expanded:
        raise ValueError(f"Path escapes the expanded directory: {inner_path}")
    return target


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def check_environment() -> str:
    """Report whether the TouchDesigner toeexpand/toecollapse tools were found.

    Call this first if anything fails. It shows the detected tool paths and the
    directories that were searched, so you can fix TD_BIN if needed.
    """
    lines = [f"Platform: {platform.system()} ({platform.machine()})"]
    for name in ("toeexpand", "toecollapse"):
        path = _find_tool(name)
        lines.append(f"{name}: {path or 'NOT FOUND'}")
    tdbin = os.environ.get("TD_BIN")
    lines.append(f"TD_BIN env: {tdbin or '(unset)'}")
    lines.append("Searched bin dirs:")
    cands = _candidate_bin_dirs()
    if cands:
        lines.extend(f"  - {d}  ({'exists' if d.is_dir() else 'missing'})" for d in cands)
    else:
        lines.append("  (none — set TD_BIN to your TouchDesigner install's bin folder)")
    return "\n".join(lines)


@mcp.tool()
def expand_toe(toe_path: str) -> str:
    """Expand a ``.toe`` file into a tree of editable ASCII text files.

    Runs TouchDesigner's ``toeexpand``. Produces a directory next to the .toe
    (named ``<file>.toe.dir``) containing the ASCII representation of every
    operator, parameter and network. This must be done before reading, editing
    or searching a .toe's contents.

    Args:
        toe_path: Absolute path to the .toe file on this machine.

    Returns a summary including the path of the expanded directory.
    """
    toe = _resolve_toe(toe_path)
    if not toe.is_file():
        raise FileNotFoundError(f"File not found: {toe}")
    tool = _require_tool("toeexpand")
    output = _run([tool, toe.name], cwd=toe.parent)
    expanded = _expanded_dir_for(toe)
    if expanded is None:
        raise RuntimeError(
            f"toeexpand ran but no expanded directory was found.\nOutput:\n{output}"
        )
    n_files = sum(1 for _ in expanded.rglob("*") if _.is_file())
    return (
        f"Expanded {toe.name} -> {expanded}\n"
        f"{n_files} text file(s). Use list_expanded / read_toe_text / search_toe "
        f"to inspect, then collapse_toe to rebuild the .toe.\n"
        f"{('toeexpand output: ' + output) if output else ''}".strip()
    )


@mcp.tool()
def collapse_toe(toe_path: str, backup: bool = True) -> str:
    """Rebuild a ``.toe`` file from its expanded ASCII directory.

    Runs TouchDesigner's ``toecollapse``, reading ``<file>.toe.dir`` and
    overwriting the ``.toe``. Call this after editing the expanded files to
    write your changes back into the binary .toe.

    Args:
        toe_path: Absolute path to the .toe file to rebuild.
        backup: If true (default), copy the existing .toe to ``<file>.toe.bak``
            before overwriting it.

    Returns a summary of the result.
    """
    toe = _resolve_toe(toe_path)
    _require_expanded_dir(toe)
    tool = _require_tool("toecollapse")

    backup_note = ""
    if backup and toe.is_file():
        bak = toe.with_suffix(toe.suffix + ".bak")
        shutil.copy2(toe, bak)
        backup_note = f"Backup saved: {bak}\n"

    output = _run([tool, toe.name], cwd=toe.parent)
    if not toe.is_file():
        raise RuntimeError(f"toecollapse ran but {toe} was not produced.\n{output}")
    size = toe.stat().st_size
    return (
        f"{backup_note}Rebuilt {toe.name} ({size:,} bytes).\n"
        f"{('toecollapse output: ' + output) if output else ''}".strip()
    )


@mcp.tool()
def list_expanded(toe_path: str, subdir: str = "") -> str:
    """List the ASCII files/folders inside a .toe's expanded directory.

    Args:
        toe_path: Absolute path to the .toe file (must already be expanded).
        subdir: Optional inner path to list a specific subfolder of the tree.

    Returns a newline-separated listing with '/' suffix marking directories.
    """
    toe = _resolve_toe(toe_path)
    expanded = _require_expanded_dir(toe)
    base = _safe_inner(expanded, subdir) if subdir else expanded
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {subdir or '.'}")
    entries = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(expanded).as_posix()
        entries.append(f"{rel}/" if p.is_dir() else rel)
    header = f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} under {expanded.name}/"
    return header + "\n" + "\n".join(entries) if entries else header + "\n(empty)"


@mcp.tool()
def read_toe_text(toe_path: str, inner_path: str, max_bytes: int = 200_000) -> str:
    """Read one ASCII file from a .toe's expanded directory.

    Args:
        toe_path: Absolute path to the .toe file (must already be expanded).
        inner_path: Path of the file relative to the expanded directory
            (as shown by list_expanded), e.g. ``project1/text1.dat``.
        max_bytes: Truncate output beyond this many bytes (default 200000).

    Returns the file contents (truncated if larger than max_bytes).
    """
    toe = _resolve_toe(toe_path)
    expanded = _require_expanded_dir(toe)
    target = _safe_inner(expanded, inner_path)
    if not target.is_file():
        raise FileNotFoundError(f"No such file in expanded tree: {inner_path}")
    data = target.read_bytes()
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[... truncated, {len(data):,} bytes total ...]"
    return text


@mcp.tool()
def write_toe_text(toe_path: str, inner_path: str, content: str) -> str:
    """Overwrite one ASCII file in a .toe's expanded directory.

    Writes ``content`` to the file (creating parent folders if needed). After
    writing, call collapse_toe to fold the change back into the .toe.

    Args:
        toe_path: Absolute path to the .toe file (must already be expanded).
        inner_path: Path of the file relative to the expanded directory.
        content: The full new contents of the file.
    """
    toe = _resolve_toe(toe_path)
    expanded = _require_expanded_dir(toe)
    target = _safe_inner(expanded, inner_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.is_file()
    target.write_text(content, encoding="utf-8")
    verb = "Updated" if existed else "Created"
    return (
        f"{verb} {inner_path} ({len(content):,} chars). "
        f"Run collapse_toe to write it back into {toe.name}."
    )


@mcp.tool()
def edit_toe_text(toe_path: str, inner_path: str, old: str, new: str, count: int = 0) -> str:
    """Find-and-replace inside one ASCII file of a .toe's expanded directory.

    Args:
        toe_path: Absolute path to the .toe file (must already be expanded).
        inner_path: Path of the file relative to the expanded directory.
        old: Exact substring to search for.
        new: Replacement string.
        count: Max replacements (0 = replace all occurrences).

    Fails if ``old`` is not found. Run collapse_toe afterwards to apply.
    """
    toe = _resolve_toe(toe_path)
    expanded = _require_expanded_dir(toe)
    target = _safe_inner(expanded, inner_path)
    if not target.is_file():
        raise FileNotFoundError(f"No such file in expanded tree: {inner_path}")
    text = target.read_text(encoding="utf-8", errors="replace")
    occurrences = text.count(old)
    if occurrences == 0:
        raise ValueError(f"Substring not found in {inner_path}: {old!r}")
    replaced = text.replace(old, new) if count <= 0 else text.replace(old, new, count)
    n = occurrences if count <= 0 else min(count, occurrences)
    target.write_text(replaced, encoding="utf-8")
    return (
        f"Replaced {n} occurrence(s) in {inner_path}. "
        f"Run collapse_toe to write it back into {toe.name}."
    )


@mcp.tool()
def search_toe(toe_path: str, pattern: str, max_results: int = 100) -> str:
    """Search the expanded ASCII files of a .toe for a text substring.

    Case-insensitive plain-text search across every file in the expanded tree.
    Useful for locating a node, parameter value, DAT text, or path.

    Args:
        toe_path: Absolute path to the .toe file (must already be expanded).
        pattern: Text to search for.
        max_results: Cap on number of matching lines returned (default 100).

    Returns matches as ``<inner_path>:<line_no>: <line>``.
    """
    toe = _resolve_toe(toe_path)
    expanded = _require_expanded_dir(toe)
    needle = pattern.lower()
    results: list[str] = []
    for f in sorted(expanded.rglob("*")):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(expanded).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                results.append(f"{rel}:{i}: {line.strip()}")
                if len(results) >= max_results:
                    return (
                        f"{len(results)} match(es) (capped at {max_results}):\n"
                        + "\n".join(results)
                    )
    if not results:
        return f"No matches for {pattern!r}."
    return f"{len(results)} match(es):\n" + "\n".join(results)


# ---------------------------------------------------------------------------
# Live control of a RUNNING TouchDesigner (via the bridge/td_setup.py bridge)
# ---------------------------------------------------------------------------
# These tools talk to the Web Server DAT installed by bridge/td_setup.py, so
# they control the project that is *currently open* in TouchDesigner in real
# time. This is different from the .toe file tools above, which edit a saved
# file on disk. Requires TouchDesigner to be open with the bridge running.
_BRIDGE_TIMEOUT = 30.0


def _bridge_url() -> str:
    return os.environ.get("TD_BRIDGE_URL", "http://127.0.0.1:9980").rstrip("/")


def _bridge_token() -> str:
    return os.environ.get("TD_BRIDGE_TOKEN", "")


def _bridge_post(path: str, payload: dict | None) -> dict:
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        _bridge_url() + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_BRIDGE_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach the TouchDesigner bridge at {_bridge_url()} ({e}). "
            f"Open TouchDesigner and run bridge/td_setup.py in it first "
            f"(or set TD_BRIDGE_URL)."
        )


def _fmt_live(res: dict) -> str:
    parts: list[str] = []
    out = (res.get("stdout") or "").rstrip("\n")
    if out:
        parts.append(out)
    if not res.get("ok", False):
        parts.append("ERROR:\n" + (res.get("error") or "unknown error").rstrip("\n"))
    else:
        val = res.get("value")
        if val is not None:  # None == statement / no return value
            parts.append(val)
    return "\n".join(parts) if parts else "(ok, no output)"


@mcp.tool()
def td_ping() -> str:
    """Check whether a live TouchDesigner project is reachable.

    Returns 'connected' if the running TouchDesigner has the bridge active,
    otherwise an error explaining how to start it. Call this before using the
    other td_* live tools if you are unsure.
    """
    try:
        res = _bridge_post("/ping", None)
    except RuntimeError as e:
        return str(e)
    return f"connected to {_bridge_url()}" if res.get("ok") else f"unexpected reply: {res}"


@mcp.tool()
def td_exec(code: str) -> str:
    """Run Python inside the CURRENTLY OPEN TouchDesigner project (real time).

    Use this to control the live project: create/delete operators, change
    parameters, read state, run scripts. Changes take effect immediately in the
    open project. This does NOT touch any .toe file on disk (use the expand/
    collapse tools for saved files). Full TouchDesigner Python is available
    (``op``, ``root``, ``ops``, ``ui``, ``project``, operator classes, etc.).

    A single expression returns its value; multiple statements run as a block
    and any ``print`` output is captured.

    Examples:
        op('/project1').children              -> list child operators
        op('/project1/moviefilein1').par.file = 'D:/clips/a.mov'
        op('/project1').create(boxSOP, 'box1')

    Args:
        code: Python source to execute in TouchDesigner.
    """
    return _fmt_live(_bridge_post("/exec", {"code": code, "token": _bridge_token()}))


@mcp.tool()
def td_ls(path: str = "/") -> str:
    """List the child operators of an operator in the running TouchDesigner.

    Args:
        path: Operator path to list children of (default '/', the root).
    """
    code = (
        "(lambda _o: sorted(c.path for c in _o.children) if _o "
        "else 'no such op: %s')(op(%r))" % (path, path)
    )
    return _fmt_live(_bridge_post("/exec", {"code": code, "token": _bridge_token()}))


@mcp.tool()
def td_pars(path: str) -> str:
    """List an operator's parameters and their current values (running TD).

    Args:
        path: Operator path whose parameters to read.
    """
    code = (
        "(lambda _o: {p.name: p.val for p in _o.pars()} if _o "
        "else 'no such op: %s')(op(%r))" % (path, path)
    )
    return _fmt_live(_bridge_post("/exec", {"code": code, "token": _bridge_token()}))


# Profiler code that runs INSIDE TouchDesigner. Walks every operator, reads its
# last cook time, and prints a JSON report to stdout. Shared with bridge/toe_ctl.py
# (keep the two copies in sync). {topn} is filled in before sending.
PERF_PROBE = r'''
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
    _cook = _num(getattr(_o, 'cookTime', 0.0))
    _cpu = _num(getattr(_o, 'cpuCookTime', 0.0))
    try:
        _cooks = int(getattr(_o, 'totalCooks', 0) or 0)
    except Exception:
        _cooks = 0
    _row = {'path': _o.path, 'type': getattr(_o, 'type', ''),
            'family': getattr(_o, 'family', ''),
            'cook': round(_cook, 3), 'cpu': round(_cpu, 3), 'cooks': _cooks}
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
_summary = {'target_fps': _fps, 'op_count': len(_rows),
            'total_last_cook_ms': round(sum(r['cook'] for r in _rows), 3)}
print(_json.dumps({'summary': _summary, 'top': _rows[:_TOPN]}))
'''


def _perf_hints(summary: dict, rows: list[dict]) -> list[str]:
    hints: list[str] = []
    fps = summary.get("target_fps") or 0
    budget = (1000.0 / fps) if fps else 0.0
    if budget and summary.get("total_last_cook_ms", 0) > budget:
        hints.append(
            "Last-cook total %.1fms exceeds the ~%.1fms/frame budget at %g fps — "
            "the ops below are the ones to trim." % (summary["total_last_cook_ms"], budget, fps)
        )
    for r in rows[:5]:
        res = r.get("res")
        if res and res[0] * res[1] > 1920 * 1080:
            hints.append(
                "%s is %dx%d — big TOPs are costly; lower the resolution or add a "
                "Resolution/Null downstream, and set 'Cook Type' to Selective." % (r["path"], res[0], res[1])
            )
        if r["family"] in ("DAT", "CHOP") and r["cook"] > budget * 0.25 and budget:
            hints.append(
                "%s (%s) cooks %.1fms — if it runs Python every frame, cache it or "
                "drive it from an event instead of cooking continuously." % (r["path"], r["type"], r["cook"])
            )
    if not hints:
        hints.append(
            "No single obvious hotspot from last-cook times. Let it run a few seconds "
            "under load, then re-run so cook times reflect the slow moment."
        )
    return hints


@mcp.tool()
def td_perf(top: int = 20) -> str:
    """Profile the running TouchDesigner project and rank the slowest operators.

    Runs a profiler inside the currently-open project that reads every
    operator's last cook time and returns them ranked slowest-first, plus a
    summary (target fps, operator count, total last-cook ms) and optimization
    hints. Use this to find WHAT is slow before changing anything.

    Tip: let the project run under its slow condition for a few seconds first,
    so the cook times reflect the slow moment.

    Args:
        top: How many of the slowest operators to return (default 20).
    """
    code = PERF_PROBE % int(top)
    res = _bridge_post("/exec", {"code": code, "token": _bridge_token()})
    if not res.get("ok", False):
        return "ERROR:\n" + (res.get("error") or "unknown error")
    raw = (res.get("stdout") or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "Unexpected profiler output:\n" + raw
    summary = data.get("summary", {})
    rows = data.get("top", [])
    lines = [
        "Target fps: %g | ops: %d | last-cook total: %.1f ms"
        % (summary.get("target_fps", 0), summary.get("op_count", 0),
           summary.get("total_last_cook_ms", 0.0)),
        "",
        "Slowest operators (last cook, ms):",
    ]
    for r in rows:
        res_str = (" res %dx%d" % tuple(r["res"])) if r.get("res") else ""
        lines.append(
            "  %8.3f  %-40s %-5s cooks=%d%s"
            % (r["cook"], r["path"], r["family"], r["cooks"], res_str)
        )
    lines.append("")
    lines.append("Hints:")
    lines.extend("  - " + h for h in _perf_hints(summary, rows))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live optimization via the touchdesigner-mcp WebServer (port 9981)
# ---------------------------------------------------------------------------
# The td_* tools above talk to the td_setup.py bridge (port 9980). If instead
# you run the third-party touchdesigner-mcp ``.tox`` (mcp_webserver_base), it
# exposes a different HTTP contract on port 9981. td_optimize / td_optimize_undo
# below drive THAT server: they profile the currently-open project and apply
# safe, fully reversible speedups. Configure the endpoint with TD_MCP_URL
# (default http://127.0.0.1:9981).
#
# The TD-side OPTIMIZE/UNDO scripts are the same contract as bridge/td9981.py —
# keep the two copies in sync.


def _tdmcp_url() -> str:
    return os.environ.get("TD_MCP_URL", "http://127.0.0.1:9981").rstrip("/")


def _tdmcp_exec(script: str) -> dict:
    """POST a Python script to the touchdesigner-mcp exec endpoint."""
    data = json.dumps({"script": script}).encode("utf-8")
    req = urllib.request.Request(
        _tdmcp_url() + "/api/td/server/exec",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_BRIDGE_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach the touchdesigner-mcp server at {_tdmcp_url()} ({e}). "
            f"Open TouchDesigner with the mcp_webserver_base .tox running (check "
            f"{_tdmcp_url()}/health in a browser), or set TD_MCP_URL."
        )


def _coerce(x):
    """Some touchdesigner-mcp builds return dicts/lists as JSON or repr strings."""
    if isinstance(x, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                return parse(x)
            except Exception:
                pass
    return x


def _tdmcp_result(res: dict):
    """Return the script's ``result``, tolerant of response nesting differences."""
    d = res.get("data")
    if isinstance(d, dict):
        r = d.get("result")
        if isinstance(r, dict) and "value" in r:
            return _coerce(r["value"])
        if r is not None:
            return _coerce(r)
    return _coerce(d)


# Diagnose + apply safe, fully reversible optimizations (TOP node viewers off,
# oversized self-sized TOPs shrunk to a cap), recording every change to a
# ``td9981_backup`` store so td_optimize_undo can restore it exactly.
_OPTIMIZE_SCRIPT = r"""
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
_down = []
_skip = []
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
            if _o.viewer:
                _o.viewer = False
                _bk['viewer'].append(_o.path)
                _voff += 1
            _w = int(_o.width); _h = int(_o.height)
            _ck = _num(getattr(_o, 'cookTime', 0.0))
            if _w * _h <= CAPW * CAPH:
                continue
            _mp = getattr(_o.par, 'resolution', None)
            _rw = getattr(_o.par, 'resolutionw', None)
            _rh = getattr(_o.par, 'resolutionh', None)
            if _mp is None or _rw is None or _rh is None:
                _skip.append({'path': _o.path, 'why': 'no resolution params',
                              'res': [_w, _h], 'cook': round(_ck, 3)})
                continue
            _mode = str(_mp.eval()).lower()
            if ('custom' not in _mode) and ('fixed' not in _mode):
                _skip.append({'path': _o.path,
                              'why': 'input-sized (' + _mode + ') - left alone',
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

_UNDO_SCRIPT = r"""
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


@mcp.tool()
def td_optimize(apply: bool = False, top: int = 15, cap: str = "1920x1080") -> str:
    """Profile the open TouchDesigner project and (optionally) apply safe speedups.

    Talks to the touchdesigner-mcp WebServer (port 9981 by default; set the
    TD_MCP_URL env var to change it). This is the live-optimization path for
    projects run with the ``mcp_webserver_base`` .tox.

    With ``apply=False`` (default) it only diagnoses: it ranks the slowest
    operators and previews what would change. With ``apply=True`` it applies two
    safe, fully reversible optimizations and records them so td_optimize_undo can
    restore them exactly:

      1. Turn off TOP node-viewer thumbnails — an editor-only cost; the rendered
         / performed output is unaffected.
      2. Shrink oversized TOPs to ``cap`` — but ONLY TOPs that own their
         resolution (mode custom/fixed). Input-sized TOPs are never touched and
         are reported for manual review, so the network's sizing logic is kept.

    Tip: let the project run under its slow condition for a few seconds first so
    cook times reflect the slow moment.

    Args:
        apply: If true, apply the safe optimizations (default false = diagnose only).
        top: How many of the slowest operators to list (default 15).
        cap: Max resolution ``WxH`` for oversized self-sized TOPs (default 1920x1080).
    """
    try:
        cw, ch = (int(x) for x in str(cap).lower().split("x", 1))
    except Exception:
        return "cap must be WxH, e.g. 1920x1080."
    script = ("TOPN = %d\nAPPLY_SAFE = %s\nCAPW = %d\nCAPH = %d\n"
              % (int(top), bool(apply), cw, ch)) + _OPTIMIZE_SCRIPT
    res = _tdmcp_exec(script)
    if not res.get("success", False):
        err = res.get("error") or (res.get("data") or {}).get("stderr") or "unknown error"
        return "ERROR:\n" + str(err)
    v = _tdmcp_result(res)
    if not isinstance(v, dict):
        return "Unexpected optimizer output:\n" + str(v)[:2000]

    fps = v.get("fps", 0) or 0
    budget = (1000.0 / fps) if fps else 0.0
    lines = [
        "fps=%g | ops=%d | last-cook total=%.1fms%s"
        % (fps, v.get("count", 0), v.get("total", 0.0),
           ("  (budget ~%.1fms/frame)" % budget) if budget else ""),
    ]
    if budget and v.get("total", 0) > budget:
        lines.append(">>> Over frame budget — the operators below are the cause.")
    lines.append("")
    lines.append("Slowest operators (last cook, ms):")
    for r in v.get("top", []):
        res_s = (" %dx%d" % tuple(r["res"])) if r.get("res") else ""
        lines.append("  %8.3f  %-40s %-5s cooks=%d%s"
                     % (r["cook"], r["path"], r["family"], r["cooks"], res_s))
    lines.append("")
    if apply:
        down = v.get("downsized") or []
        lines.append("Applied (safe, reversible):")
        lines.append("  - TOP node viewers off: %d (render output unaffected)"
                     % v.get("viewers_off", 0))
        lines.append("  - Oversized TOPs shrunk: %d (cap %dx%d, self-sized only)"
                     % (len(down), cw, ch))
        for d in down:
            lines.append("      %-40s %dx%d -> %dx%d  (%.1fms)"
                         % (d["path"], d["frm"][0], d["frm"][1],
                            d["to"][0], d["to"][1], d.get("cook", 0.0)))
        skip = v.get("skipped") or []
        if skip:
            lines.append("  Heavy TOPs left alone (manual review):")
            for s in skip[:10]:
                lines.append("      %-40s %dx%d  %.1fms  [%s]"
                             % (s["path"], s["res"][0], s["res"][1],
                                s.get("cook", 0.0), s.get("why", "")))
        lines.append("  Revert everything with the td_optimize_undo tool.")
    else:
        lines.append("Diagnose only — call again with apply=True to turn off TOP "
                     "viewers and shrink oversized TOPs (both reversible via "
                     "td_optimize_undo).")
    return "\n".join(lines)


@mcp.tool()
def td_optimize_undo() -> str:
    """Revert everything a previous ``td_optimize(apply=True)`` changed.

    Restores TOP node viewers and the original resolutions from the
    ``td9981_backup`` store in the open project, then clears the store. Talks to
    the touchdesigner-mcp WebServer (port 9981 by default; set TD_MCP_URL).
    """
    res = _tdmcp_exec(_UNDO_SCRIPT)
    if not res.get("success", False):
        err = res.get("error") or (res.get("data") or {}).get("stderr") or "unknown error"
        return "ERROR:\n" + str(err)
    v = _tdmcp_result(res)
    if not isinstance(v, dict):
        return "Unexpected undo output:\n" + str(v)[:2000]
    return ("Reverted: %d TOP viewer(s) turned back on, %d resolution(s) restored."
            % (v.get("viewers_on", 0), v.get("res_restored", 0)))


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
