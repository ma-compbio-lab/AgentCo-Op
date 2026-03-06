# SpatialBench Benchmark Assets

This directory stores local SpatialBench validation outputs created by the repository helpers.

- `validation_summary.json`: output from running the canonical `spatialbench validate` smoke test.

To (re)create the local SpatialBench repo clone, virtualenv, package install, and validation summary:

```bash
cd /home/shuaikes/projects/agent/Agent-Cop
.venv/bin/python scripts/setup_spatialbench.py
```

The default config clones `https://github.com/latchbio/spatialbench` into `external/spatialbench` and creates a dedicated virtual environment at `external/spatialbench/.venv`.
