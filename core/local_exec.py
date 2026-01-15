from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from utils import log_event


_READ_ONLY_CMDS = {
    "ls",
    "cat",
    "rg",
    "grep",
    "sed",
    "awk",
    "head",
    "tail",
    "stat",
    "wc",
    "find",
    "pwd",
    "whoami",
    "uname",
    "date",
}

_WRITE_CMDS = {"touch", "mkdir", "rm", "mv", "cp", "tee", "truncate"}

_GIT_READ_CMDS = {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files"}
_GIT_WRITE_CMDS = {"add", "checkout", "commit", "reset", "rm", "mv", "clean", "apply", "revert"}

_REDIRECT_RE = re.compile(r"(?:^|\\s)(?:>>|>|2>|&>)\\s*(\\S+)")
_SHELL_COMPLEX_RE = re.compile(r"(?:^|\\s)(?:\\|\\||&&|;|\\|)(?:\\s|$)")


@dataclass
class ExecConfig:
    enabled: bool = False
    workspace_root: str = "."
    require_approval: bool = True
    max_output_chars: int = 4000


class WriteApproval:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.allowed_paths: set[Path] = set()
        self.allowed_dirs: set[Path] = set()

    def is_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        if resolved in self.allowed_paths:
            return True
        for allowed_dir in self.allowed_dirs:
            if _is_relative_to(resolved, allowed_dir):
                return True
        return False

    def request(self, paths: Iterable[Path], *, command: str, is_dir_flags: Iterable[bool]) -> None:
        for path, is_dir in zip(paths, is_dir_flags):
            if self.is_allowed(path):
                continue
            decision = _prompt_write_permission(path, command)
            if decision == "deny":
                raise PermissionError(f"Write access denied for {path}")
            if decision == "always":
                if is_dir:
                    self.allowed_dirs.add(path.resolve())
                else:
                    self.allowed_paths.add(path.resolve())


class LocalExecutor:
    def __init__(self, config: ExecConfig, approval: WriteApproval) -> None:
        self.config = config
        self.enabled = bool(config.enabled)
        self.workspace_root = Path(config.workspace_root).resolve()
        self.require_approval = bool(config.require_approval)
        self.approval = approval

    def execute(self, command: str, *, cwd: str | None = None) -> dict[str, object]:
        if not self.enabled:
            raise PermissionError("Local execution is disabled.")
        if not command or not command.strip():
            raise ValueError("Command is empty.")
        run_cwd = self._resolve_cwd(cwd)
        # Guardrail: reject complex shell pipelines and multi-command chains to avoid hidden writes.
        write_intent, write_paths, is_dir_flags = self._analyze_command(command, run_cwd)
        if write_intent and not write_paths:
            raise PermissionError(
                "Cannot determine write targets. Use explicit file paths or redirection within the workspace."
            )
        if write_intent:
            self._enforce_workspace(write_paths)
            if self.require_approval:
                self.approval.request(write_paths, command=command, is_dir_flags=is_dir_flags)
        log_event("TOOLS", "exec", "local command", data={"command": command, "cwd": str(run_cwd)})
        proc = subprocess.run(
            command,
            shell=True,
            cwd=run_cwd,
            capture_output=True,
            text=True,
        )
        stdout = _clip(proc.stdout, self.config.max_output_chars)
        stderr = _clip(proc.stderr, self.config.max_output_chars)
        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode,
            "cwd": str(run_cwd),
        }

    def _resolve_cwd(self, cwd: str | None) -> Path:
        if not cwd:
            return self.workspace_root
        resolved = Path(cwd).expanduser().resolve()
        if not _is_relative_to(resolved, self.workspace_root):
            raise PermissionError(f"Working directory must be within workspace: {resolved}")
        return resolved

    def _enforce_workspace(self, paths: Iterable[Path]) -> None:
        for path in paths:
            if not _is_relative_to(path.resolve(), self.workspace_root):
                raise PermissionError(f"Write path outside workspace is blocked: {path}")

    def _analyze_command(self, command: str, cwd: Path) -> tuple[bool, list[Path], list[bool]]:
        tokens = _safe_split(command)
        if _SHELL_COMPLEX_RE.search(command):
            return True, [], []
        if not tokens:
            return False, [], []
        cmd = tokens[0]
        subcmd = tokens[1] if len(tokens) > 1 else ""
        redir_paths = _extract_redirections(command, cwd)
        has_redir = bool(redir_paths)
        if cmd in _READ_ONLY_CMDS and not has_redir and not _has_write_flags(cmd, tokens):
            return False, [], []

        write_intent = True
        write_paths: list[Path] = []
        is_dir_flags: list[bool] = []
        unknown_targets = False

        if cmd == "git":
            if subcmd in _GIT_READ_CMDS and not has_redir:
                return False, [], []
            if subcmd in _GIT_WRITE_CMDS:
                write_paths = [self.workspace_root]
                is_dir_flags = [True]
            else:
                unknown_targets = True
        elif cmd in {"touch", "rm"}:
            for path in _non_flag_args(tokens[1:]):
                write_paths.append(_resolve_path(path, cwd))
                is_dir_flags.append(False)
        elif cmd == "mkdir":
            for path in _non_flag_args(tokens[1:]):
                write_paths.append(_resolve_path(path, cwd))
                is_dir_flags.append(True)
        elif cmd in {"cp", "mv"}:
            args = _non_flag_args(tokens[1:])
            if len(args) >= 2:
                dest = _resolve_path(args[-1], cwd)
                write_paths.append(dest)
                is_dir_flags.append(dest.exists() and dest.is_dir())
        elif cmd == "tee":
            args = _non_flag_args(tokens[1:])
            if args:
                dest = _resolve_path(args[-1], cwd)
                write_paths.append(dest)
                is_dir_flags.append(False)
        else:
            unknown_targets = True

        for path in redir_paths:
            write_paths.append(path)
            is_dir_flags.append(False)
            unknown_targets = False

        if unknown_targets and not write_paths:
            return True, [], []
        return write_intent, write_paths, is_dir_flags


def _clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _safe_split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _non_flag_args(args: list[str]) -> list[str]:
    return [arg for arg in args if not arg.startswith("-")]


def _resolve_path(path_str: str, cwd: Path) -> Path:
    path = Path(os.path.expanduser(path_str))
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _extract_redirections(command: str, cwd: Path) -> list[Path]:
    matches = _REDIRECT_RE.findall(command)
    return [_resolve_path(match, cwd) for match in matches]


def _has_write_flags(cmd: str, tokens: list[str]) -> bool:
    if cmd == "sed":
        return any(token.startswith("-i") or token == "--in-place" for token in tokens[1:])
    if cmd == "find":
        return any(token in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for token in tokens[1:])
    return False


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        return path.is_relative_to(base)
    except AttributeError:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


def _prompt_write_permission(path: Path, command: str) -> str:
    _animate_prompt()
    message = f"Write access requested for:\\n{path}\\n\\nCommand:\\n{command}"
    if _use_prompt_toolkit():
        return _prompt_toolkit_choice(message)
    return _prompt_text_choice(message)


def _use_prompt_toolkit() -> bool:
    return sys.stdin.isatty()


def _prompt_toolkit_choice(message: str) -> str:
    try:
        from prompt_toolkit.shortcuts import radiolist_dialog
    except Exception:
        return _prompt_text_choice(message)
    choice = radiolist_dialog(
        title="Write Permission Required",
        text=message,
        values=[
            ("allow", "Allow once"),
            ("always", "Allow always for this file/dir"),
            ("deny", "Deny"),
        ],
    ).run()
    return choice or "deny"


def _prompt_text_choice(message: str) -> str:
    print(message)
    print("1) Allow once")
    print("2) Allow always for this file/dir")
    print("3) Deny")
    selection = input("Select [1-3]: ").strip()
    if selection == "2":
        return "always"
    if selection == "1":
        return "allow"
    return "deny"


def _animate_prompt() -> None:
    frames = ["|", "/", "-", "\\"]
    for idx in range(8):
        frame = frames[idx % len(frames)]
        sys.stdout.write(f"\r{frame} Requesting write approval...")
        sys.stdout.flush()
        time.sleep(0.08)
    sys.stdout.write("\r" + " " * 40 + "\r")
    sys.stdout.flush()
