#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> None:
    dest_dir = Path("data/med_qa_repo")
    dest_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="bigbio/med_qa",
        repo_type="dataset",
        local_dir=str(dest_dir),
        local_dir_use_symlinks=False,
    )
    print(f"Downloaded to: {dest_dir}")


if __name__ == "__main__":
    main()
