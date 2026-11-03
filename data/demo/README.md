# 데모 인물 기록 (가상)

`persona_observations.jsonl`은 시나리오와 지식 베이스 채우기를 보여주기 위해 만든 **가상 인물**의 4주치 기록 29건이다.
실제 사용자의 일기가 아니다. DJ 작업, 블로그 글쓰기, 식사 선택, 업무 메일 같은 일상 사례로 구성했다.

```sh
META_API_KEY=... python -m mirror.ingest data/demo/persona_observations.jsonl --db data/demo/mirror.kuzu --extractor spark
```
