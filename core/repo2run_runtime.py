from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from core.contracts import ContainerSpec, ToolCandidate
from core.observability import Observability
from utils import ensure_dir, log_event


@dataclass
class Repo2RunResult:
    repo_dir: str
    dockerfile_path: str
    commit_sha: str | None = None
    used_repo2run: bool = False
    stdout_path: str | None = None
    stderr_path: str | None = None


class Repo2RunRunner:
    def __init__(
        self,
        *,
        repo2run_path: str,
        work_dir: str,
        python_exe: str = "python",
        llm: str | None = None,
        prefer_existing: bool = True,
        observability: Observability | None = None,
    ) -> None:
        self.repo2run_path = os.path.abspath(repo2run_path)
        self.work_dir = os.path.abspath(work_dir)
        self.python_exe = python_exe
        self.llm = llm
        self.prefer_existing = prefer_existing
        self.obs = observability
        ensure_dir(self.work_dir)

    def prepare(self, candidate: ToolCandidate) -> Repo2RunResult | None:
        if candidate.kind != "github_repo" or not candidate.repo_url:
            return None
        if not os.path.isdir(self.repo2run_path):
            log_event("TOOLS", "repo2run", "Repo2Run path missing", level="warn")
            return None
        repo_dir = self._clone_repo(candidate)
        if repo_dir is None:
            return None
        existing = self._find_dockerfile(repo_dir)
        if existing and self.prefer_existing:
            return Repo2RunResult(repo_dir=repo_dir, dockerfile_path=existing, commit_sha=candidate.commit_sha)
        return self._run_repo2run(candidate, repo_dir)

    def to_container_spec(self, result: Repo2RunResult) -> ContainerSpec:
        workdir = self._parse_workdir(result.dockerfile_path)
        return ContainerSpec(
            dockerfile_path=result.dockerfile_path,
            context_dir=result.repo_dir,
            workdir=workdir or "/workspace",
        )

    def _clone_repo(self, candidate: ToolCandidate) -> Optional[str]:
        repo_name = candidate.name or self._extract_full_name(candidate.repo_url or "") or "repo"
        repo_dir = os.path.join(self.work_dir, repo_name.replace("/", "_"))
        if os.path.isdir(repo_dir):
            shutil.rmtree(repo_dir)
        cmd = ["git", "clone", "--depth", "1", candidate.repo_url, repo_dir]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            log_event("TOOLS", "git_clone", "failed to clone repo", level="warn", data={"repo": candidate.repo_url})
            return None
        if candidate.commit_sha:
            subprocess.run(["git", "-C", repo_dir, "fetch", "--depth", "1", "origin", candidate.commit_sha], check=False)
            subprocess.run(["git", "-C", repo_dir, "checkout", candidate.commit_sha], check=False)
        else:
            sha = subprocess.run(
                ["git", "-C", repo_dir, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if sha.returncode == 0:
                candidate.commit_sha = sha.stdout.strip()
        return repo_dir

    def _run_repo2run(self, candidate: ToolCandidate, repo_dir: str) -> Repo2RunResult | None:
        full_name = candidate.name or self._extract_full_name(candidate.repo_url or "")
        if not full_name:
            return None
        cmd = [
            self.python_exe,
            os.path.join(self.repo2run_path, "build_agent", "main.py"),
            "--full_name",
            full_name,
            "--root_path",
            self.work_dir,
        ]
        if candidate.commit_sha:
            cmd.extend(["--sha", candidate.commit_sha])
        if self.llm:
            cmd.extend(["--llm", self.llm])
        log_event("TOOLS", "repo2run", "running Repo2Run", data={"repo": full_name})
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        stdout_path, stderr_path = self._write_logs(repo_dir, proc.stdout, proc.stderr)
        if self.obs:
            self.obs.event(
                "repo2run",
                {"repo": full_name, "returncode": proc.returncode},
            )
        dockerfile_path = self._find_dockerfile(repo_dir)
        if proc.returncode != 0 or not dockerfile_path:
            log_event("TOOLS", "repo2run", "Repo2Run failed or Dockerfile missing", level="warn")
            return None
        return Repo2RunResult(
            repo_dir=repo_dir,
            dockerfile_path=dockerfile_path,
            commit_sha=candidate.commit_sha,
            used_repo2run=True,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    @staticmethod
    def _extract_full_name(repo_url: str) -> str | None:
        if "github.com/" not in repo_url:
            return None
        return repo_url.rstrip("/").split("github.com/")[-1]

    @staticmethod
    def _find_dockerfile(repo_dir: str) -> str | None:
        root_path = os.path.join(repo_dir, "Dockerfile")
        if os.path.isfile(root_path):
            return root_path
        for root, _dirs, files in os.walk(repo_dir):
            for name in files:
                if name.lower().startswith("dockerfile"):
                    return os.path.join(root, name)
        return None

    @staticmethod
    def _parse_workdir(dockerfile_path: str) -> str | None:
        try:
            with open(dockerfile_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.upper().startswith("WORKDIR "):
                        return line.split(" ", 1)[1].strip()
        except OSError:
            return None
        return None

    @staticmethod
    def _write_logs(repo_dir: str, stdout: str, stderr: str) -> tuple[str, str]:
        stdout_path = os.path.join(repo_dir, "repo2run_stdout.txt")
        stderr_path = os.path.join(repo_dir, "repo2run_stderr.txt")
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write(stdout or "")
        with open(stderr_path, "w", encoding="utf-8") as f:
            f.write(stderr or "")
        return stdout_path, stderr_path
