# MedQA Benchmark Assets

This directory stores local MedQA benchmark artifacts created by the repository helpers.

- `medqa_preview.json`: a tiny preview sample fetched from Hugging Face.
- `medqa_dataset_info.json`: dataset version, config, split, and sample metadata.

To refresh these files:

```bash
cd /home/shuaikes/projects/agent/Agent-Cop
.venv/bin/python scripts/prepare_medqa.py
```

The repository expects `datasets<4` and uses `trust_remote_code=True` because `bigbio/med_qa` is implemented as a dataset script.
