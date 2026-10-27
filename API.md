# 宮今 API 명세 (서버는 아직 구현 전)

관람객 앱(폰 브라우저)이 쓰는 것 전부: **서버 API 4개** + **S3 정적 파일** + **폰에서 돌리는 JS 모듈 2개**.
기계용 같은 내용: [`openapi.yaml`](openapi.yaml) (https://editor.swagger.io 에 붙여 넣으면 화면으로 보임,
`npx @stoplight/prism-cli mock openapi.yaml` 이면 서버 없이 예시 응답을 주는 목 서버 → 앱 화면을 먼저 만들 수 있음).

```
폰 ──(https)── CloudFront ─┬─ /api/*   → EC2 Nginx → Docker: server/ (LLM·DB 가 필요한 일만)
                           └─ 그 밖     → S3: visit/ · servermap/ (지도·판정·길찾기 코드·데이터) + photos/
판정(judge.mjs)·길 계산(route.mjs)은 폰에서 돎. 서버는 위치를 받지 않음.
```

## 0. 지금 상태

| | 상태 |
|---|---|
| 답변 로직 `ask.reply` · 카드 `cards.card` · 코스 `course.Planner.make` | **있음** (`server/`) — HTTP 로 감싸기만 하면 됨 |
| 엔드포인트 4개 | 확인용 임시 서버만 (`server/course.py`, localhost:8001 — 이 문서의 요청·응답·에러 코드 그대로) |
| 정식 서버 (FastAPI 등) · Dockerfile · Nginx | **없음** |
| 관람객 앱 화면 | **없음** (`visit/sim.html` 이 같은 흐름을 개발용으로 보여 줌) |

---

## 1. 공통 규칙

| 항목 | 값 |
|---|---|
| 베이스 URL | 같은 도메인 `/api` (CloudFront). 개발: `http://localhost:8001/api` |
| 형식 | 요청·응답 모두 JSON, UTF-8. `Content-Type: application/json` |
| 인증 | 없음. 비용 보호는 Nginx IP 별 속도 제한 (예: `/api/ask`·`/api/course` 분당 10회) |
| CORS | 같은 도메인이라 운영엔 불필요. 개발 서버는 `*` |
| 길이 제한 | `question`·`request` 300자 (넘으면 잘라서 씀 — `course.py` 와 같음) |
| 소요 시간 | `/api/ask` 3~10초, `/api/course` 10~20초 → 앱은 로딩 표시. 서버 타임아웃 60초 |
| 언어 | 답변 언어는 서버가 질문을 보고 정함 (아래 `language`). 앱이 따로 보내지 않음 |

### `language` 값
`"한국어"` · `"English"` · `"ภาษาไทย"` — `ask.answer_language`: "영어로/in English" 같은 요청이 있으면 그 언어, 아니면 질문에 태국 문자 → 태국어, 한글 → 한국어, 그 밖 → English.

### 에러
모든 에러 응답은 같은 모양: `{"error": "사람이 읽는 메시지"}`

| 코드 | 언제 | 앱이 할 일 |
|---|---|---|
| `400` | 본문이 JSON 이 아님, 필수 필드(`question`·`request`·`id`) 없음·빈 문자열 | 입력 확인 |
| `404` | 없는 카드 id, 없는 경로 | 카드 숨김 |
| `429` | 속도 제한 (Nginx) | "잠시 후 다시" |
| `500` | LLM·DB 오류 | "답변을 만들지 못했습니다" + 다시 시도 버튼 |
| `504` | 60초 넘김 (Nginx) | 위와 같음 |

### 답변 텍스트 (`answer`) — `/api/ask`·`/api/course` 공통
- 챗봇 규칙 그대로(`ask.SYSTEM`): **넣은 DB 문서에 있는 사실만**, `language` 로, 용어집 표기.
- 일반 문단 마크다운 (제목·목록 없음). 전각·용어 이름은 이름마다 **처음 한 번** 링크:
  ```
  [근정전](card:b-160) 앞 [월대](card:t-70) 는 ...
  ```
- 앱: `card:<id>` 링크를 누르면 `GET /api/card?id=<id>` → 카드 시트. (일반 링크로 열지 말 것)
- 링크는 그 답변에 **넣은 문서의 이름에만** 붙음 (`cards.link_names`) → 카드가 있으면 근거 문서도 있음.

---

## 2. 엔드포인트

### `POST /api/ask` — 질문 (설명 · 길 묻기 · 근처 편의시설)

요청
| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `question` | string (1~300자) | ✔ | 관람객이 쓴 문장. 한국어·영어·태국어 |
| `history` | `[{question, answer}]` | | 이전 턴 (최근 3개만 씀). 받은 `answer` 그대로 넣으면 됨. 없으면 단발 질문 |

응답 `200`
| 필드 | 타입 | 설명 |
|---|---|---|
| `answer` | string | 답변 (위 규칙, `card:` 링크 포함) |
| `language` | string | 답변 언어 |
| `action` | object \| null | 폰이 할 일. 일반 질문이면 `null` |
| `action.type` | `"route"` \| `"amenity"` | |
| `action.to` | string | (`route`) 목적지 POI id = `pois.json` 의 `id` (nameId) |
| `action.category` | `"restroom"` \| `"bench"` \| `"water"` | (`amenity`) 편의시설 종류 = `pins.geojson` 의 `category` |

서버 안 흐름 (`ask.reply`):
```
⓪ 맥락 반영 (LLM 없음, 규칙): 지시어(거기·there·ที่นั่น)가 있거나 질문에 이름이 없으면, 앞 답변에 처음 걸린 전각 카드 이름을
   질문 언어로 붙임. "Who lived there?" → "Who lived there? (Gyotaejeon)". 답변 언어는 원래 질문 기준
① 의도 (LLM 1번, 짧음): {kind: route|amenity|question, to, amenity}
   to 는 DB 전각 문서의 nameId 목록(enum) 안에서만 고름 → 목록 밖 id 는 나올 수 없음
② kind 별
   question → 지금 챗봇 그대로: 번역(외국어만) → 검색(벡터+키워드) → 문서 고르기 → 답변      LLM 총 3~4번
   route    → 검색 없이 목적지 문서 b-<serial>-* 전부 + 그 전각 용어를 고정으로 → 짧은 소개 2~3문장  LLM 총 2번
   amenity  → DB 에 문서 없음 → 언어별 한 문장                                               LLM 총 1번
```

| `kind` | 답변 근거 | 카드 근거 | 길 근거 |
|---|---|---|---|
| question | 검색해서 고른 문서 | 그 문서들 메타데이터 | — |
| route | 목적지 전각 문서 (DB `nameId` == `to`) | 목적지 전각·용어 | 폰 `routeTo` (편집기 길 `paths.geojson`) |
| amenity | (템플릿) | 없음 | 폰 `nearestAmenities` (`pins.geojson`) |

예시
```jsonc
// 일반 질문
→ {"question": "강녕전이 뭐야?"}
← {"answer": "[강녕전](card:b-163)은 왕이 일상을 보내던 침전입니다. ...", "language": "한국어", "action": null}

// 길 묻기 — answer 는 목적지 소개, 거리·시간은 폰이 routeTo 결과로 붙임 ("320m · 5분")
→ {"question": "교태전 어떻게 가?"}
← {"answer": "[교태전](card:b-164)은 왕비의 침전으로, 뒤뜰에 [아미산](card:b-165) 굴뚝이 있습니다. ...",
   "language": "한국어", "action": {"type": "route", "to": "place:gyotaejeon"}}

// 근처 편의시설
→ {"question": "Where is the nearest toilet?"}
← {"answer": "Showing the nearest restrooms on the map.", "language": "English",
   "action": {"type": "amenity", "category": "restroom"}}
```
- 섞인 질문("교태전 가는 길이랑 뭐하는 곳인지")은 route 로 보고 짧은 소개만. 길게 원하면 다시 일반 질문.
- 대화 기록은 서버에 저장 안 함 — 폰이 `history` 로 보냄. 안 보내면 질문마다 독립 ("거기는?" 못 알아들음).
- 자연 지점(연못·숲)은 DB 문서가 없어 길 묻기 목적지가 안 됨.

### `GET /api/card?id=<id>` — 카드 한 장

| 쿼리 | 형식 | 설명 |
|---|---|---|
| `id` | `^(b-\d+\|t-\d+)$` | `b-<serial>` 전각 (serial = `pois.json` 의 `serial`, 151~227) · `t-<no>` 용어 |

응답 `200` (`cards.card`) · 없으면 `404`
```json
{"id": "b-160", "type": "building", "name": "근정전", "name_en": "Geunjeongjeon (Throne Hall)", "name_th": "...",
 "text": "설명 (전각은 문단을 합친 전체, 용어는 용어집 한 항목)", "text_en": "...", "text_th": "...", "category": null,
 "photo": "/photos/160.jpg", "photo_license": "공공누리 제1유형", "photo_source": "..."}
```
| 필드 | 설명 |
|---|---|
| `type` | `"building"` \| `"term"` |
| `text` | 한국어 원문 |
| `text_en` · `text_th` | 같은 설명의 영어·태국어 (생성 번역, 원어민 검수 전). 화면 언어에 맞춰 쓰고, `null` 이면 `text` |
| `category` | 용어 분류 (전각은 `null`) |
| `photo` | CloudFront 기준 경로 = S3 `photos/<serial>.jpg`. 사진 없거나 용어면 `null`. **사진 쓰면 `photo_license`·`photo_source` 를 같이 표시** |

### `POST /api/course` — 테마 코스

요청
| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `request` | string (1~300자) | ✔ | 예: "왕비 이야기 위주로 40분", "a quiet 30-minute walk". 시간이 없으면 60분 |

응답 `200`
```json
{
  "answer": "왕비의 생활 공간을 따라가도록 짰습니다. 먼저 [교태전](card:b-164)에서 ... 이어서 향원지 ...",
  "language": "한국어",
  "course": {
    "minutes": 40,
    "stops": [{"id": "place:gyotaejeon", "stayMin": 8}, {"id": "nature:향원지", "stayMin": 3}]
  }
}
```
| 필드 | 설명 |
|---|---|
| `answer` | **코스를 왜 이렇게 짰는지 + 순서대로 곳마다 볼 것** (카드 링크). 시간·거리 숫자는 안 씀 |
| `course.minutes` | 요청에서 읽은 제한 시간(분) |
| `course.stops[].id` | 전각 = `pois.json` 의 `id` · 자연 지점 = `nature:<이름>` (`servermap/data/places.geojson` 의 `name`) |
| `course.stops[].stayMin` | 머무는 분 |
| (순서) | LLM 이 정한 순서 그대로. 출발·도착 = 광화문 기준으로 짬. **실제 길·시간은 서버가 주지 않음** → 폰 `fitCourse` |

서버 안 두 단계 (`course.Planner`), LLM 총 2번:
1. **경로** — 근거: 권역 해설(DB 전각 문서, `group` 으로 묶음) + 후보·걷기 분 행렬(`course-input.json`). LLM 은 후보 id(enum) 중 들를 곳·순서·`stayMin`·넣은 이유(`why`, 내부용)만 정함.
2. **설명** — 근거: 고른 곳의 DB 문서(`b-<serial>-*` + 그 전각 용어)를 고정으로 넣고 챗봇 규칙(`ask.SYSTEM`)으로 답변. `why` 는 참고로만 넘김 → `link_names` 로 카드 링크.

### `GET /api/health`
`200 {"ok": true}` — 로드밸런서·배포 확인용. DB·LLM 은 부르지 않음.

---

## 3. S3 정적 파일 (앱이 `fetch` 하는 것)

`visit/`·`servermap/` 를 폴더 구조 그대로 올림 + 사진 `photos/`.

| 경로 | 내용 | 모양 |
|---|---|---|
| `servermap/style.json` | MapLibre 스타일 (배경·길·핀·라벨). 상대 URL 은 `servermap/` 기준 | MapLibre style |
| `servermap/tiles/palace.pmtiles` | 배경 타일. **HTTP Range 요청** 필요 (S3·CloudFront 는 됨) | PMTiles v3 |
| `servermap/data/paths.geojson` | 보행 길 → `buildGraph` 재료 | LineString, `properties.nodes`(노드 id 배열)·`access`·`foot` |
| `servermap/data/pins.geojson` | 핀 (전각·문·화장실·벤치·식수대·행사) | Point, `category`: `gate` `hall` `event` `water` `restroom` `bench`, `name:ko/en/th` |
| `servermap/data/places.geojson` | 지도 라벨 (연못·숲 등 자연 지점 포함) | Point, `kind`, `name`, `name:ko/en/th` |
| `servermap/data/boundary.geojson`·`mask.geojson` | 궁 경계·궁 밖 가림 | Polygon |
| `servermap/manifest.json` | 내보낸 시각·범위·언어·개수 | `{center, bbox, minZoom, maxZoom, languages, counts}` |
| `visit/data/pois.json` | 판정·길찾기 대상 71곳 | 아래 |
| `visit/data/zones.geojson` | 판정 존 | Polygon, `properties.id`, `kind`: `yard` `band` `gate` |
| `visit/judge.mjs`·`visit/route.mjs` | 폰에서 돌리는 모듈 (아래 4) | ES module |
| `photos/<serial>.jpg` | 전각 사진 | JPEG |

`pois.json` 한 항목:
```json
{"id": "place:gyotaejeon", "name": "교태전", "category": "hall", "pin": [126.977058, 37.579916],
 "zoneIds": ["yard:gyotaejeon", "yard:p-21", "yard:heumgyeonggak"], "approachNodes": ["4440001251"],
 "serial": 164, "group": "교태전"}
```
`id` = nameId (편집기 핀·`action.to`·`course.stops[].id`), `serial` → 카드 `b-<serial>`·사진 `photos/<serial>.jpg`, `group` = 소속 권역 대표 전각. 좌표는 모두 `[lng, lat]`.

- **화면 이름**: `pois.json` 의 `name` 은 한국어뿐. 영어·태국어는 `pins.geojson` 에서 `properties.nameId == id` 인 핀의 `name:en`·`name:th` (71곳 모두 있음). 자연 지점(`nature:<이름>`)은 `places.geojson` 의 `name == <이름>` 인 곳의 `name:*`·좌표.
- **행사 핀** (`category: "event"`, 3곳): `properties.description` 에 시간·위치·관람 참고 (한국어만, 줄바꿈 `\n`). 판정·길찾기 대상 아님.

---

## 4. 폰 JS 모듈 (`visit/judge.mjs`·`visit/route.mjs`)

외부 의존성 없음. 좌표 `[lng, lat]`, 거리 m, 시간 분(`route`)·ms(`judge`).

### `judge.mjs` — 방문 판정
```js
import { createJudge, DEFAULTS } from './judge.mjs'
const judge = createJudge(zones, pois, opts?, saved?)   // zones.geojson, pois.json, DEFAULTS 일부 덮어쓰기, judge.saved() 저장값
judge.update({ lat, lng, accuracy, t })  // geolocation 샘플마다 → [{ type: 'enter'|'audio'|'stamp'|'exit', poiId, t }]
judge.saved()                            // { visited: [poiId], lastAudio: {poiId: t} } → localStorage 에 저장해 두고 다음에 복원
```
`audio` = 존 안 7초 → 해설 재생 (`serial` 로 카드·사진), `stamp` = 25초 → 스탬프. 임계값은 `DEFAULTS`.

### `route.mjs` — 길
| 함수 | 반환 | 쓰는 곳 |
|---|---|---|
| `buildGraph(paths)` | 그래프 `g` (앱 시작 때 한 번) | |
| `routeTo(g, here, poi)` | `{meters, minutes, coords}` \| `null` | `action.type == "route"` → `poi = pois.find(p => p.id == action.to)` |
| `nearestAmenities(g, here, pins, category, k=3)` | `[{id, pin, meters, minutes, coords}]` 가까운 순 | `action.type == "amenity"` |
| `fitCourse(g, here, stops, minutes, end=here)` | `{stops: [{id, walkMin, stayMin, coords}], back: {walkMin, coords}, minutes, meters, dropped: [{id, reason: 'time'\|'path'\|'unknown'}]}` | 코스 [안내하기] |
| `nextStop(g, here, pois, visitedSet)` | `{poiId, meters, coords}` \| `null` | "다음 갈 곳" |

`coords` = 지도에 그릴 LineString 좌표. `fitCourse` 의 `back` 은 `end = null`(끝점 없음)이면 `null`. `stops` 는 `{id, stayMin, pin, approachNodes?}` 로 채워서 넘김 (전각 = `pois.json`, 자연 지점 = `places.geojson` 좌표).

---

## 5. 화면 흐름

```
[질문] "교태전 어떻게 가?"
  → POST /api/ask                                    (로딩)
  ← answer + action {type: route, to}
  화면: 말풍선 answer (card: 링크 → 카드 시트) · routeTo(g, 내 위치, pois[to]) 선 그리기 · "320m · 5분"

[질문] "근처 화장실"
  ← action {type: amenity, category: restroom}
  화면: nearestAmenities 3곳 핀 + 1등까지 선

[코스] "왕비 이야기 위주로 40분"
  → POST /api/course                                 (10~20초, 로딩)
  ← answer + course.stops
  화면: 말풍선 answer · 지도에 stops 핀·순서 번호 · [안내하기] 버튼 (아직 길은 안 그림)
  [안내하기]
    stops 를 pin·approachNodes 로 채움 → fitCourse(g, 내 위치, stops, course.minutes)
    → 실제 걷기 시간으로 다시 계산, 넘치는 곳은 빼고 "n곳 뺌" 표시
    안내: 다음 곳까지 coords 표시 → judge 의 stamp.poiId == 다음 곳이면 다음으로 (자연 지점은 근처 도착으로 넘김)
    마지막 곳 → back 경로 (끝점 = 출발점)

[판정] 항상 켜 둠: watchPosition → judge.update → audio = 카드·해설, stamp = 스탬프 모음
```

## 6. 이번 범위 밖
- `/ws` 실시간 통역 (별도 컨테이너·Redis).
- 세션·QR 토큰, 스탬프 서버 저장 (지금은 폰 `judge.saved()`).
- 응답 스트리밍 — 필요해지면 `/api/ask` 를 SSE 로.
- 대화 기록(후속 질문), 자연 지점 길 묻기.
- 해설 소리: judge `audio` 때 무엇을 재생할지 미정 (브라우저 TTS 로 카드 `text_*` 읽기 / 미리 만든 음성 파일).
