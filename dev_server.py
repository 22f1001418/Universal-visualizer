"""Dev-server manager — runs `npm install`, `npm audit fix`, `npm run dev`
for each generated viz, on a unique port, in the background.

Lifecycle of one viz preview:
  1. Caller asks `start_dev_server(project_dir)`
  2. We pick a free port (auto-incremented, starting at 5180)
  3. Run `npm install` (idempotent — fixes deps if first run failed)
  4. Run `npm audit fix --force` (silently ignored if it errors)
  5. Spawn `npm run dev -- --port PORT --host` as a detached child
  6. Wait for the port to start listening (up to PREVIEW_BOOT_WAIT seconds)
  7. Return DevServerInfo so the UI can show http://127.0.0.1:PORT

We track all running servers in `_servers` and kill them on shutdown.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hackmd-orch.devserver")

# ── Static build ─────────────────────────────────────────────────────────────

@dataclass
class StaticBuildInfo:
    project_dir: str
    dist_dir: str
    status: str   # "ok" | "failed"
    error: str = ""


def _patch_vite_base(project_dir: Path) -> None:
    """Insert base: "./" into both vite.config.ts and vite.config.js.

    Both files must be patched because Vite may load either one depending on
    version — vite.config.js takes precedence in some Vite versions.
    """
    for fname in ("vite.config.ts", "vite.config.js"):
        cfg = project_dir / fname
        if not cfg.exists():
            continue
        text = cfg.read_text(encoding="utf-8")
        if "base:" in text:
            continue  # already has a base setting, skip this file
        patched = re.sub(r'(defineConfig\s*\(\s*\{)', r'\1' + '\n  base: "./",' , text, count=1)
        if patched != text:
            cfg.write_text(patched, encoding="utf-8")
            logger.info("[StaticBuild] patched %s with base: \"./\"", fname)


def build_static_viz(project_dir: str) -> StaticBuildInfo:
    """Compile the generated Vite project to a static dist/ bundle.

    Strategy (avoids a second npm install which fails on Railway):
      1. Use node_modules/.bin/vite directly — fixed_main_v6.py already ran
         npm install, so the binary is there. Pass --base ./ via CLI so
         assets use relative paths and work from any URL sub-path.
      2. Only fall back to npm install + npm run build if the vite binary
         is missing for some reason.
    Idempotent: skips if dist/index.html already exists.
    """
    import shutil as _shutil

    pdir = Path(project_dir).resolve()
    dist = pdir / "dist"

    if not pdir.exists() or not (pdir / "package.json").exists():
        return StaticBuildInfo(
            project_dir=str(pdir), dist_dir="", status="failed",
            error=f"Not a valid Vite project: {pdir}",
        )

    if dist.exists() and (dist / "index.html").exists():
        logger.info("[StaticBuild] dist/ already present for %s — skipping", pdir.name)
        return StaticBuildInfo(project_dir=str(pdir), dist_dir=str(dist), status="ok")

    # Remove any stale / partial dist/
    if dist.exists():
        _shutil.rmtree(dist)

    vite_bin = pdir / "node_modules" / ".bin" / "vite"

    if vite_bin.exists():
        # Happy path: use the already-installed Vite binary, no npm install needed.
        # --base ./ makes all asset paths relative so the bundle works from /viz/<slug>/dist/.
        ok, tail = _run_npm_step(
            [str(vite_bin), "build", "--base", "./"],
            pdir,
            NPM_INSTALL_TIMEOUT,
            "vite build --base ./",
        )
    else:
        # Fallback: vite binary missing, run a full npm install first.
        logger.warning("[StaticBuild] vite binary not found for %s — running npm install", pdir.name)
        ok, tail = _npm_install(pdir)
        if not ok:
            return StaticBuildInfo(
                project_dir=str(pdir), dist_dir="", status="failed",
                error=f"npm install failed: {tail[-300:]}",
            )
        _ensure_boilerplate_deps(pdir)
        _patch_vite_base(pdir)
        ok, tail = _run_npm_step(
            ["npm", "run", "build"],
            pdir,
            NPM_INSTALL_TIMEOUT,
            "npm run build",
        )

    if not ok:
        return StaticBuildInfo(
            project_dir=str(pdir), dist_dir="", status="failed",
            error=f"vite build failed: {tail[-300:]}",
        )

    if not (dist / "index.html").exists():
        return StaticBuildInfo(
            project_dir=str(pdir), dist_dir="", status="failed",
            error="build reported success but dist/index.html not found",
        )

    logger.info("[StaticBuild] built dist/ for %s", pdir.name)
    return StaticBuildInfo(project_dir=str(pdir), dist_dir=str(dist), status="ok")



# ── Config ──────────────────────────────────────────────
PORT_RANGE_START = int(os.getenv("DEV_SERVER_PORT_START", "5180"))
PORT_RANGE_END   = int(os.getenv("DEV_SERVER_PORT_END",   "5230"))
PREVIEW_BOOT_WAIT = int(os.getenv("PREVIEW_BOOT_WAIT", "45"))   # seconds — vite cold start
NPM_INSTALL_TIMEOUT = int(os.getenv("NPM_INSTALL_TIMEOUT", "300"))
AUDIT_FIX_ENABLED = os.getenv("AUDIT_FIX_ENABLED", "false").lower() in ("1", "true", "yes")
DEV_SERVER_LOG_NAME = ".dev-server.log"


@dataclass
class DevServerInfo:
    project_dir: str
    port: int
    pid: int
    url: str
    started_at: float = field(default_factory=time.time)
    status: str = "starting"   # starting | running | failed | stopped
    error: str = ""


# ── Internal state — keyed by project_dir ────────────────
_servers: dict[str, DevServerInfo] = {}
_processes: dict[str, subprocess.Popen] = {}
_used_ports: set[int] = set()
_lock = threading.Lock()


# ── Helpers ──────────────────────────────────────────────

def _is_port_free(port: int) -> bool:
    """Try binding to localhost:port; if it works, the port is free."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.bind(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        try: s.close()
        except Exception: pass
        return False


def _is_port_listening(port: int) -> bool:
    """True if SOMETHING is listening on the port (i.e. dev server came up)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except (OSError, ConnectionRefusedError):
        try: s.close()
        except Exception: pass
        return False


def _allocate_port() -> Optional[int]:
    """Pick a free port from the configured range."""
    with _lock:
        for p in range(PORT_RANGE_START, PORT_RANGE_END + 1):
            if p in _used_ports:
                continue
            if _is_port_free(p):
                _used_ports.add(p)
                return p
    return None


def _release_port(port: int) -> None:
    with _lock:
        _used_ports.discard(port)


# ── npm helpers ──────────────────────────────────────────

def _run_npm_step(
    cmd: list[str],
    cwd: Path,
    timeout: int,
    label: str,
) -> tuple[bool, str]:
    """Run an npm command, return (ok, last_lines)."""
    logger.info("[DevServer] %s in %s ...", label, cwd.name)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label} timed out after {timeout}s"
    except FileNotFoundError:
        return False, "npm executable not found — is Node.js installed and in PATH?"

    out = (result.stdout or "") + "\n" + (result.stderr or "")
    tail = "\n".join(out.splitlines()[-15:]) if out else ""
    if result.returncode != 0:
        logger.warning("[DevServer] %s exited %d. tail: %s",
                       label, result.returncode, tail[:300])
        return False, tail
    return True, tail


def _npm_install(project_dir: Path) -> tuple[bool, str]:
    return _run_npm_step(
        ["npm", "install", "--no-fund", "--no-audit", "--loglevel=error"],
        project_dir, NPM_INSTALL_TIMEOUT, "npm install",
    )


# Boilerplate deps the generator template promises — pinned to match the
# versions in fixed_main_v6.py's prompt. The LLM occasionally forgets to put
# one of these in package.json even after importing it; we self-heal here.
_BOILERPLATE_DEPS: dict[str, str] = {
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "framer-motion": "11.15.0",
    "zustand": "5.0.3",
    "lucide-react": "0.468.0",
    "prism-react-renderer": "2.4.0",
}

_IMPORT_RE = re.compile(r"""(?:from|import)\s*['"]([^'".][^'"]*)['"]""")


def _scan_imports(project_dir: Path) -> set[str]:
    """Return the set of bare-package imports found under src/."""
    src = project_dir / "src"
    if not src.exists():
        return set()
    found: set[str] = set()
    for path in src.rglob("*"):
        if path.suffix not in (".ts", ".tsx", ".js", ".jsx"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _IMPORT_RE.finditer(text):
            spec = m.group(1)
            # Bare package: doesn't start with . or /, take "name" or "@scope/name"
            if spec.startswith(("@",)):
                parts = spec.split("/", 2)
                if len(parts) >= 2:
                    found.add("/".join(parts[:2]))
            else:
                found.add(spec.split("/", 1)[0])
    return found


def _ensure_boilerplate_deps(project_dir: Path) -> None:
    """If src/ imports a known boilerplate dep that isn't installed, install it.

    Self-heals against the LLM-generated package.json missing a dep that the
    code actually imports — the most common cause of `vite:import-analysis`
    failures right after generation.
    """
    imports = _scan_imports(project_dir)
    node_modules = project_dir / "node_modules"
    missing: list[str] = []
    for pkg, version in _BOILERPLATE_DEPS.items():
        if pkg not in imports:
            continue
        if (node_modules / pkg).exists():
            continue
        missing.append(f"{pkg}@{version}")
    if not missing:
        return
    logger.warning(
        "[DevServer] %s: package.json missing %d imported dep(s) — installing: %s",
        project_dir.name, len(missing), ", ".join(missing),
    )
    _run_npm_step(
        ["npm", "install", "--save", "--no-fund", "--no-audit", "--loglevel=error", *missing],
        project_dir, NPM_INSTALL_TIMEOUT, f"npm install {' '.join(missing)}",
    )


def _npm_audit_fix(project_dir: Path) -> tuple[bool, str]:
    """Run audit fix --force. Failures are tolerated."""
    if not AUDIT_FIX_ENABLED:
        return True, "(skipped — AUDIT_FIX_ENABLED=false)"
    ok, tail = _run_npm_step(
        ["npm", "audit", "fix", "--force", "--loglevel=error"],
        project_dir, NPM_INSTALL_TIMEOUT, "npm audit fix --force",
    )
    if not ok:
        # Audit fix sometimes "fails" but actually fixes things — log and keep going.
        logger.info("[DevServer] audit fix returned non-zero, continuing anyway")
    return True, tail


# ── Public API ──────────────────────────────────────────

def start_dev_server(project_dir: str) -> DevServerInfo:
    """Start npm install → audit fix → npm run dev for the given project dir.

    Idempotent: if a server is already running for this dir, returns its info.
    """
    pdir = Path(project_dir).resolve()
    if not pdir.exists() or not (pdir / "package.json").exists():
        info = DevServerInfo(
            project_dir=str(pdir), port=0, pid=0, url="",
            status="failed", error=f"Not a valid Vite project: {pdir}",
        )
        return info

    # Already running?
    with _lock:
        if str(pdir) in _servers:
            existing = _servers[str(pdir)]
            if existing.status == "running" and _is_port_listening(existing.port):
                logger.info("[DevServer] reusing existing server for %s on port %d",
                            pdir.name, existing.port)
                return existing
            # Stale — clean up
            _stop_unlocked(str(pdir))

    port = _allocate_port()
    if port is None:
        return DevServerInfo(
            project_dir=str(pdir), port=0, pid=0, url="",
            status="failed",
            error=f"No free port available in range {PORT_RANGE_START}-{PORT_RANGE_END}",
        )

    info = DevServerInfo(
        project_dir=str(pdir), port=port, pid=0,
        url=f"http://127.0.0.1:{port}",
        status="starting",
    )
    with _lock:
        _servers[str(pdir)] = info

    # ── Step 1: npm install (skip if node_modules already present) ──
    if (pdir / "node_modules").exists():
        logger.info("[DevServer] node_modules present in %s — skipping npm install", pdir.name)
    else:
        ok, tail = _npm_install(pdir)
        if not ok:
            info.status = "failed"
            info.error = f"npm install failed: {tail[-300:]}"
            _release_port(port)
            return info

    # ── Step 1b: self-heal — install any boilerplate dep the LLM imported
    # but forgot to declare in package.json.
    _ensure_boilerplate_deps(pdir)

    # ── Step 2: npm audit fix --force (opt-in via AUDIT_FIX_ENABLED, off by default) ──
    # `--force` can install incompatible major-version upgrades and break the
    # generated project right before `npm run dev`. Disabled by default.
    _npm_audit_fix(pdir)

    # ── Step 3: npm run dev as a detached background process ──
    # We pass --port and --host so vite uses our chosen port.
    # The "--" separator tells npm to forward args to the underlying script.
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--host", "127.0.0.1"]
    log_path = pdir / DEV_SERVER_LOG_NAME
    logger.info("[DevServer] spawning: %s in %s (log: %s)",
                " ".join(cmd), pdir.name, log_path)

    try:
        # Stream stdout+stderr into a per-project log file so failures are
        # diagnosable. start_new_session detaches the child so parent SIGINT
        # doesn't take it down — we kill explicitly on shutdown.
        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            cmd,
            cwd=str(pdir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        log_file.close()  # child inherits the fd; parent doesn't need it
    except FileNotFoundError:
        info.status = "failed"
        info.error = "npm not found — is Node.js installed and in PATH?"
        _release_port(port)
        return info

    info.pid = proc.pid
    with _lock:
        _processes[str(pdir)] = proc

    # ── Step 4: wait for the port to come up ──
    deadline = time.time() + PREVIEW_BOOT_WAIT
    while time.time() < deadline:
        if proc.poll() is not None:
            info.status = "failed"
            info.error = (
                f"dev server exited with code {proc.returncode} during boot. "
                f"See {log_path} for details."
            )
            _release_port(port)
            with _lock:
                _processes.pop(str(pdir), None)
            return info

        if _is_port_listening(port):
            info.status = "running"
            logger.info("[DevServer] running pid=%d port=%d url=%s",
                        proc.pid, port, info.url)
            return info

        time.sleep(0.5)

    # Timed out — port never bound. Don't lie about being "running"; kill the
    # orphan so we don't leak the port, and point the operator at the log.
    logger.warning(
        "[DevServer] port %d not listening after %ds — killing pid %d. See %s",
        port, PREVIEW_BOOT_WAIT, proc.pid, log_path,
    )
    try:
        os.killpg(os.getpgid(proc.pid), 15)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), 9)
    except (ProcessLookupError, PermissionError):
        pass
    info.status = "failed"
    info.error = (
        f"dev server didn't bind port {port} within {PREVIEW_BOOT_WAIT}s. "
        f"See {log_path} for the npm output."
    )
    _release_port(port)
    with _lock:
        _processes.pop(str(pdir), None)
    return info


def stop_dev_server(project_dir: str) -> bool:
    """Stop the dev server for project_dir. Returns True if one was running."""
    with _lock:
        return _stop_unlocked(project_dir)


def _stop_unlocked(project_dir: str) -> bool:
    """Caller MUST hold _lock."""
    proc = _processes.pop(project_dir, None)
    info = _servers.get(project_dir)

    if proc is not None:
        try:
            # Negative pid kills the whole process group (we used start_new_session=True)
            os.killpg(os.getpgid(proc.pid), 15)   # SIGTERM
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), 9)   # SIGKILL
                proc.wait(timeout=2)
        except (ProcessLookupError, PermissionError):
            pass
        except Exception as e:
            logger.warning("[DevServer] error stopping %s: %s", project_dir, e)

    if info is not None:
        info.status = "stopped"
        _used_ports.discard(info.port)

    return proc is not None


def list_dev_servers() -> list[DevServerInfo]:
    with _lock:
        # Refresh status: anything we think is running but isn't listening → stale
        for pdir, info in list(_servers.items()):
            if info.status == "running" and not _is_port_listening(info.port):
                info.status = "stopped"
                info.error = info.error or "port no longer listening"
                _used_ports.discard(info.port)
                _processes.pop(pdir, None)
        return list(_servers.values())


def shutdown_all() -> int:
    """Stop every tracked dev server. Called on app shutdown."""
    n = 0
    with _lock:
        for pdir in list(_processes.keys()):
            if _stop_unlocked(pdir):
                n += 1
    if n:
        logger.info("[DevServer] shutdown — stopped %d dev server(s)", n)
    return n
