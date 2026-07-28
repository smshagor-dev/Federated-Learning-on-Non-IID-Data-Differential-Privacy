from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .output import Console


@dataclass(slots=True)
class ManagedProcess:
    process: subprocess.Popen[str]
    threads: list[threading.Thread] = field(default_factory=list)


def start_web_process(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    log_file: Path,
    console: Console,
) -> ManagedProcess:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=creationflags,
    )
    handle = log_file.open("a", encoding="utf-8")
    threads = [
        threading.Thread(
            target=_pump_stream,
            args=(process.stdout, handle, console, "WEB"),
            daemon=True,
        ),
        threading.Thread(
            target=_pump_stream,
            args=(process.stderr, handle, console, "WEB"),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    return ManagedProcess(process=process, threads=threads)


def _pump_stream(
    stream: TextIO | None, handle: TextIO, console: Console, component: str
) -> None:
    if stream is None:
        return
    try:
        for line in stream:
            stripped = line.rstrip()
            if not stripped:
                continue
            handle.write(stripped + "\n")
            handle.flush()
            console.plain(f"[{component}] {stripped}")
    finally:
        stream.close()


def stop_process(
    process: subprocess.Popen[str],
    console: Console,
    component: str,
    timeout_s: int = 15,
) -> None:
    if process.poll() is not None:
        return
    console.info(component, "Stopping process")
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError):
        console.warning(component, "Graceful stop timed out, terminating")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            console.warning(component, "Terminate timed out, killing")
            process.kill()
            process.wait(timeout=5)
