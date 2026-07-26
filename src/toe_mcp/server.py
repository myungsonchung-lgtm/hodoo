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

import os
import platform
import shutil
import subprocess
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


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
