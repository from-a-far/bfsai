from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Settings


class ServiceManager:
    def __init__(self, settings: Settings, root_dir: Path):
        self.settings = settings
        self.root_dir = root_dir
        self.run_dir = self.settings.extraction.runtime_dir / "services"
        self.log_dir = self.run_dir / "logs"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        return {
            "api": self._service_status("api"),
            "worker": self._service_status("worker"),
        }

    def start(self, service_name: str) -> dict[str, Any]:
        if service_name == "all":
            return {"api": self.start("api"), "worker": self.start("worker")}
        current = self._service_status(service_name)
        if current["running"]:
            return current | {"action": "already_running"}
        process = self._spawn(service_name)
        return self._service_status(service_name) | {"action": "started", "pid": process.pid}

    def stop(self, service_name: str) -> dict[str, Any]:
        if service_name == "all":
            return {"api": self.stop("api"), "worker": self.stop("worker")}
        pid_file = self._pid_file(service_name)
        pid = self._read_pid(pid_file)
        if not pid or not self._pid_running(pid):
            if pid_file.exists():
                pid_file.unlink()
            return self._service_status(service_name) | {"action": "not_running"}
        os.killpg(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        return self._service_status(service_name) | {"action": "stopped", "pid": pid}

    def restart(self, service_name: str) -> dict[str, Any]:
        stop_result = self.stop(service_name)
        start_result = self.start(service_name)
        return {"stop": stop_result, "start": start_result}

    def _spawn(self, service_name: str) -> subprocess.Popen[str]:
        command = self._command(service_name)
        log_file = self.log_dir / f"{service_name}.log"
        with log_file.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                command,
                cwd=self.root_dir,
                stdout=handle,
                stderr=handle,
                stdin=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        self._pid_file(service_name).write_text(str(process.pid), encoding="utf-8")
        return process

    def _command(self, service_name: str) -> list[str]:
        if service_name == "api":
            return ["uv", "run", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8001"]
        if service_name == "worker":
            return ["uv", "run", "python", "-m", "app.worker"]
        raise ValueError(f"Unknown service: {service_name}")

    def _service_status(self, service_name: str) -> dict[str, Any]:
        pid_file = self._pid_file(service_name)
        pid = self._read_pid(pid_file)
        if not pid or not self._pid_running(pid):
            pid = self._discover_pid(service_name)
        running = bool(pid and self._pid_running(pid))
        log_file = self.log_dir / f"{service_name}.log"
        return {
            "name": service_name,
            "running": running,
            "pid": pid if running else None,
            "pid_file": str(pid_file),
            "log_file": str(log_file),
            "last_log_line": self._last_log_line(log_file),
        }

    def _read_pid(self, pid_file: Path) -> int | None:
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def _pid_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _pid_file(self, service_name: str) -> Path:
        return self.run_dir / f"{service_name}.pid"

    def _last_log_line(self, log_file: Path) -> str:
        if not log_file.exists():
            return ""
        lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-1] if lines else ""

    def _discover_pid(self, service_name: str) -> int | None:
        try:
            if service_name == "api":
                result = subprocess.run(
                    ["lsof", "-t", "-iTCP:8001", "-sTCP:LISTEN"],
                    cwd=self.root_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                pid_text = result.stdout.strip().splitlines()
                return int(pid_text[0]) if pid_text else None
            if service_name == "worker":
                result = subprocess.run(
                    ["pgrep", "-f", "uv run python -m app.worker|python -m app.worker"],
                    cwd=self.root_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                pid_text = result.stdout.strip().splitlines()
                return int(pid_text[0]) if pid_text else None
        except Exception:
            return None
        return None


def main() -> None:
    from .config import load_settings

    settings = load_settings()
    manager = ServiceManager(settings, Path.cwd())
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    service_name = sys.argv[2] if len(sys.argv) > 2 else "all"
    if action == "status":
        payload = manager.status()
    elif action == "start":
        payload = manager.start(service_name)
    elif action == "stop":
        payload = manager.stop(service_name)
    elif action == "restart":
        payload = manager.restart(service_name)
    else:
        raise SystemExit(f"Unsupported action: {action}")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
