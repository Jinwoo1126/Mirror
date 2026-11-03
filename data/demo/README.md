# Demo persona (fictional)

`persona_observations.jsonl` holds 40 short first-person entries from a **fictional person**
over six weeks. They are not anyone's real data. The cases cover DJ sets, blog writing,
meals, tiredness and rest, code review, deadlines, learning, shopping, travel and how the
person likes AI assistants to answer, so that every scenario has something to draw on.

```sh
META_API_KEY=... python -m mirror.ingest data/demo/persona_observations.jsonl \
    --db data/demo/mirror.kuzu --extractor spark
```
