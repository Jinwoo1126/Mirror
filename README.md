# Mirror — 개인 이해 에이전트 (P1–P4 전체 구현)

평소 쓰는 AI 에이전트 옆에 붙는 이해 모듈. 대화·선택·정정을 받아
"나"에 대한 시간축 지식 그래프를 갱신하고, 요청이 오면 관련 이해와
사용 제한을 돌려준다. 전체 설계는 구축 계획 문서
(`Mirror 구축 계획 — 동적 그래프·온톨로지 기반 개인 이해 에이전트.md`) 참조.

구현 범위: P1 그래프 MVP(수집·추출·이중 시간 저장·MCP·채점),
P2 가설 엔진(승격·주간점검·야간통합·라우터), P3 비교 실험(리플레이·A/M1/M2),
P4 제안·UI(시뮬레이터·개입 정책·주간 리포트·이해 보기 화면),
실사용 지능(BM25+임베딩 하이브리드 검색, MiroFish식 동적 관계·엔티티 요약,
personal.ai식 채팅 우선 로컬 웹앱).

## 구성

```
mirror/
  schema.py store.py          온톨로지 스키마 + Kuzu 이중 시간 저장소
  llm.py extract.py           LLM 호출 헬퍼 + 추출기(spark/Muse/ollama)
  observe.py control.py       관찰 수집 + 조회·정정·중지·삭제·재허용
  retrieve.py                 관련 이해 조회 (+options decision 모드)
  hypotheses.py               승격·주간 점검·야간 통합
  router.py assistant.py      의도·스타일 라우터 + 어시스턴트 배선
  simulate.py                 관점 시뮬레이터 (경쟁 가설 3~5개)
  agents.py replay.py         A/M1/M2 에이전트 + 리플레이 하네스
  policy.py report.py         개입 정책 0~3단계 + 주간 변경 리포트
  ingest.py eval_precision.py 일괄 수집 + 추출 정밀도 채점
  ui_export.py server.py      UI 내보내기 + MCP 서버
  vstore.py bm25.py search.py 로컬 임베딩 + BM25 + 하이브리드 검색(RRF)
  app.py static/app.html      로컬 웹앱 (채팅·기억·그래프·가설·관계·리포트)
data/sample_observations.jsonl   샘플 관찰 8건
eval/gold_observations.jsonl     손작성 정답 트리플 4장면
eval/dj_episodes.jsonl           DJ 선택 에피소드 10건 (리플레이용)
ui/index.html                    이해 보기 화면 (정적 페이지)
tests/                           pytest 커밋 테스트 (단위+통합)
```

> 개인 일기 성격의 샘플(`data/sample_observations.jsonl`, `eval/gold_observations.jsonl`,
> `eval/dj_episodes.jsonl`)과 생성된 `mirror-results.html`은 레포에 포함하지 않는다.
> 같은 형식의 JSONL을 직접 만들어 쓰면 된다. 채점 형식은 `eval/GUIDELINE.md`,
> 측정 기록은 `eval/RESULTS.md` 참조.

> 추출은 LLM 추출기만 쓴다. 규칙 기반 `MockExtractor`는 테스트 전용이고, LLM을 못 쓰면
> 규칙으로 대신하지 않고 실패를 알린다. spark는 `muse` 로그인 또는 `META_API_KEY`가 필요하다.

## 설치

```sh
uv venv --python 3.11
uv pip install -e '.[test]'
```

Claude/Ollama 추출기를 쓰려면 `.[Muse]` / `.[ollama]` 추가 설치.
Spark 추출기는 로그인된 `muse` CLI를 사용 (`muse-spark-1.3` 기본).

## 사용

```sh
source .venv/bin/activate

# 1. 일괄 수집 (샘플 8건)
python -m mirror.ingest data/sample_observations.jsonl --db data/local/mirror.kuzu

# 추출만 확인 (기록 없음)
python -m mirror.ingest data/sample_observations.jsonl --dry-run

# 2. 추출 정밀도 채점 (정답셋 기준, 건수 그대로 보고)
python -m mirror.eval_precision eval/gold_observations.jsonl --extractor spark

# 3. MCP 서버 (Claude 등 호스트에 연결)
MIRROR_DB=data/local/mirror.kuzu python -m mirror.server

# 4. 비교 실험 (A vs M1 vs M2, 시간순 리플레이)
python -m mirror.replay eval/dj_episodes.jsonl

# 5. 주기 작업 (야간 통합·주간 점검은 아래 함수로 스케줄러에 연결)
python -c "from mirror.store import MirrorStore; from mirror import hypotheses as H
st = MirrorStore('data/local/mirror.kuzu'); print(H.nightly_maintenance(st)); print(H.weekly_review(st))"

# 6. 이해 보기 화면 내보내기 → ui/index.html을 브라우저로 열기
python -m mirror.ui_export --db data/local/mirror.kuzu --out ui/mirror-graph.json

# 7. 테스트 (단위+통합)
python -m pytest

# 8. 로컬 웹앱 실행 → 브라우저에서 http://127.0.0.1:8000 열기
python -m mirror.app
# 다른 DB/포트: python -m mirror.app --db data/local/mirror.kuzu --port 8000
```

## 실사용 지능

- 하이브리드 검색: BM25 + 다국어 MiniLM 임베딩(384차원, 로컬 ONNX) +
  RRF 융합. 모델은 첫 실행 시 `data/models/`에 내려받는다(약 470MB,
  `MIRROR_MODEL_DIR`로 변경 가능). 모델이 없으면 BM25 전용으로 동작한다.
- 동적 온톨로지(MiroFish식): 고정 구조 뼈대 위에 `RELATED_TO` 관계
  서술이 어휘로 축적되고(`/api/relations`), 엔티티 요약이 언급과 함께
  자란다. 관계 추출은 규칙(Mock 최소) + LLM 프롬프트가 담당한다.
- 웹앱(personal.ai식): 채팅 우선. 저장은 기본 수동(각 메시지 '기억에
  저장', 자동 토글 지원). 생성 답변은 spark → Claude → ollama 순으로
  자동 감지하고, 없으면 검색 기반 로컬 답변 + 근거 인용으로 답한다.

주요 환경 변수: `MIRROR_DB`, `MIRROR_EXTRACTOR`(spark/Muse/ollama, 기본 spark),
`MIRROR_ROUTER`(mock/llm), `MIRROR_NO_INDEX=1`(레거시 검색),
`MIRROR_MODEL_DIR`, `ANTHROPIC_API_KEY`.

MCP 클라이언트 설정 예 (Claude Code):

```json
{
  "mcpServers": {
    "mirror": {
      "command": "/path/to/mirror/.venv/bin/python",
      "args": ["-m", "mirror.server"],
      "cwd": "/path/to/mirror",
      "env": {"MIRROR_DB": "data/local/mirror.kuzu", "MIRROR_EXTRACTOR": "spark", "META_API_KEY": "..."}
    }
  }
}
```

## 원칙 (구축 계획에서 발췌)

- 단정 노드 금지: 해석은 조건·반례·대안을 가진 가설 노드로만 둔다.
- 근거 없는 엣지 거부: `USED_IN` 제외 모든 엣지에 근거 관찰 ID 필수.
- 이중 시간: `valid_*`(현실 유효 구간)와 `recorded_at`(안 시점) 분리,
  `as_of` 조회로 미래 정보 차단.
- 실패 시 상태 불변: observe/control/retrieve 모두 실패하면 그래프를
  바꾸지 않고 사유를 반환한다.
- 삭제·사용 중지 즉시 적용 + 차단 목록으로 재출현 방지.
