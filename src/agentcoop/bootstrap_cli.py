"""CLI entrypoint for agentcoop-bootstrap-skills."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentcoop.skills.bootstrapper import SkillBootstrapper


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap meta-skills from existing workflow patterns.")
    parser.add_argument(
        "--output-dir",
        default="skills/meta-skills",
        help="Directory to write generated meta-skill SKILL.md files (default: skills/meta-skills)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    bootstrapper = SkillBootstrapper(output_dir=output_dir)
    generated = bootstrapper.bootstrap_all()
    for path in generated:
        print(f"Generated: {path}")
    print(f"\n{len(generated)} meta-skills generated in {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
