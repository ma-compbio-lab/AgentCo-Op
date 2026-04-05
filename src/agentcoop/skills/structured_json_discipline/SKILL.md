---
name: structured-json-discipline
description: Keep reasoning compact, preserve required keys, and return schema-aligned JSON objects.
user-invocable: true
---

Follow the output contract exactly.

1. Return a JSON object, not prose.
2. Preserve required keys even when confidence is low.
3. Keep the summary short and factual.
4. If a tool is available, use it only when it materially improves correctness.
