# deploy — 배포

이 폴더가 올라가는 전부. API 명세: [`API.md`](API.md) · [`openapi.yaml`](openapi.yaml).

```
deploy/
├─ .env.example                키 양식 → .env 로 복사해 채움 (.env 는 git 제외)
├─ docker-compose.yml          EC2 컨테이너 (지금은 db 만)
├─ db/          → EC2 Docker   Postgres(pgvector) 초기 데이터: 00-vector.sql · docs.sql.gz (전각·용어 문서 585 + 임베딩)
├─ server/      → EC2 Docker   질문·길 묻기(ask) · 카드(cards) · 코스(course)
├─ visit/       → S3           폰에서 도는 판정(judge.mjs) · 길(route.mjs) · data/ (+ 개발용 sim.html·logger.html)
└─ servermap/   → S3           지도 (스타일·타일·길·핀·라벨)
```

| 파일 | 내용 |
|---|---|
| `server/ask.py` | 질문 답변 `reply()` = `/api/ask` — 의도(일반 질문·길 묻기·편의시설) → 검색(pgvector)·답변 |
| `server/cards.py` | 답변 속 이름 → `[근정전](card:b-160)` 링크, 카드 `card()` = `/api/card` |
| `server/course.py` | 코스 `Planner.make()` = `/api/course` (① 경로 ② DB 문서로 설명 + 카드). `python course.py` = 확인용 임시 서버 localhost:8001 |
| `server/course-input.json` | 코스 후보·걷기 분 (생성물 — 손으로 고치지 말 것) |
| `server/pg.py` · `bedrock.py` | Postgres 문서 저장소 · Titan 임베딩 |
| `server/config.py` · `requirements.txt` | `.env` 로드 (개발용) · 패키지 |
| `visit/judge.mjs` | 방문 판정 — 위치 샘플 → `enter`·`audio`·`stamp`·`exit` |
| `visit/route.mjs` | 길찾기 `routeTo` · 근처 편의시설 `nearestAmenities` · 코스 맞추기 `fitCourse` · 다음 곳 `nextStop` |
| `visit/data/` | `pois.json`(전각·문 71곳) · `zones.geojson`(판정 존) · `walls.geojson`(담장, 시뮬레이터 표시용) |
| `visit/sim.html` · `logger.html` | 개발용 시뮬레이터 · 현장 GPS 로거 (폰 GPS 는 HTTPS 필요라 같이 올림) |
| `servermap/` | `style.json` · `tiles/palace.pmtiles` · `data/*.geojson` · `manifest.json` (생성물 — 손으로 고치지 말 것) |

## 구성

```
폰 ── CloudFront ─┬─ /api/*  → EC2 Nginx → Docker (server/)
                  └─ 그 밖   → S3 (visit/ · servermap/ · photos/)
```
- CloudFront: pmtiles 때문에 Range 요청 허용.
- Nginx: `/api/ask`·`/api/course` IP 별 속도 제한 (넘으면 429), 타임아웃 60초.

## 키 — `.env`

`.env.example` 을 `.env` 로 복사해 채움. `.env` 는 git 에 안 올라감 (`.gitignore`) — 키를 git·메신저로 보내지 말 것.
`server/config.py` 와 `docker compose`(DB_PASSWORD) 가 둘 다 이 파일을 읽음.

| 키 | 용도 |
|---|---|
| `OPENAI_API_KEY` (+ 선택 `LUNA_EFFORT`) | 답변·의도·코스 LLM (GPT-6 Luna) |
| `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` · `AWS_REGION` | Titan 임베딩 (Bedrock). EC2 에선 Bedrock 권한 IAM 역할을 붙이면 키 두 개는 비움 |
| `DB_PASSWORD` · `DATABASE_URL` | DB 비밀번호 · 접속 주소 (둘을 같이 바꿀 것) |

팀원은 각자 키를 발급받아 자기 `.env` 에 넣음 (OpenAI 는 같은 조직 안 개인 키, AWS 는 Bedrock 호출 권한만 준 IAM 사용자).

## EC2 — `docker-compose.yml` · `db/` · `server/`

EC2 한 대에서 Docker Compose. DB 도 같은 EC2 안의 컨테이너 (RDS 아님).

```bash
docker compose -f deploy/docker-compose.yml up -d db     # 처음 뜰 때 db/ 를 읽어 docs 테이블을 채움
```
- `db/` 는 볼륨이 비어 있을 때 한 번만 읽힘. `docs.sql.gz` 가 바뀌면 `docker compose -f deploy/docker-compose.yml down -v` 후 다시 `up -d db`.
- DB 포트는 `127.0.0.1:5433` (서버 밖에선 안 보임). 비밀번호 `DB_PASSWORD` (기본 `gbg`).

| server/ | |
|---|---|
| 넣을 것 | `server/` 폴더만 (`pip install -r requirements.txt`) |
| 키 | 위 `.env` (또는 같은 이름의 환경변수) |
| 엔드포인트 | `ask.reply` → `POST /api/ask` · `cards.card` → `GET /api/card` · `course.Planner.make` → `POST /api/course` · `GET /api/health` |

## S3 — `visit/` · `servermap/` · `photos/`

폴더 구조 그대로 올림 (html 이 `../servermap/` 상대 경로로 읽음).
```bash
aws s3 sync deploy/visit/     s3://<버킷>/visit/     --exclude "*.md" --delete
aws s3 sync deploy/servermap/ s3://<버킷>/servermap/ --exclude "*.md" --delete
```
`photos/<serial>.jpg` — 카드 `photo` 경로 (`/photos/<serial>.jpg`). 버킷에 따로 있어야 함.

## 아직 없는 것
compose 의 api·nginx 컨테이너 (Dockerfile · Nginx 설정) · 정식 API 서버 (`server/course.py` 의 localhost:8001 은 엔드포인트 4개를 띄우는 확인용).
로직은 위 표의 함수에 다 있음 — HTTP 로 감싸기만 하면 됨.
