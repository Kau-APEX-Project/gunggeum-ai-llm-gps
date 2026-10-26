"""LLM 코스: 관람객 요청 문장 → {answer, language, course} (deploy/API.md 의 POST /api/course)

1. 경로 (LLM 1번): 재료 = course-input.json (후보 = 권역 대표 전각 + 지도 자연 지점, 후보 사이 걷기 분)
   + 권역 해설(DB 전각 문서, group 으로 묶음). LLM 은 후보 id(enum) 중 들를 곳·순서·체류·넣은 이유(why)만 정함.
2. 설명 (LLM 1번): 고른 곳의 DB 문서(b-<serial>-*)를 고정으로 넣고 챗봇 답변 규칙(ask.SYSTEM)으로 "코스를 왜 이렇게 짰는지 + 곳마다 볼 것"
   → 카드 링크(cards.link_names). why 는 2단계 재료로만 쓰고 응답엔 안 나감.
실제 길·시간은 폰의 visit/route.mjs fitCourse 가 다시 계산하고 넘치는 곳은 뺌.
실행: 루트에서 .venv\Scripts\python deploy/server/course.py → localhost:8001 (OPENAI_API_KEY·DATABASE_URL 필요). 화면은 루트 sim.bat
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ask import add_fixed, answer_docs, answer_language, llm, name_keys, reply
from cards import card, link_names
from pg import Collection

INPUT = Path(__file__).parent / 'course-input.json'  # servermap 이 바뀌면 build.cmd 가 다시 만듦
DOC_CHARS = 600  # 경로 단계: 건물 하나당 해설 앞부분만 (권역 안 소속 건물도 고루 들어가게)
NATURE = ('water', 'wood', 'stream', 'spring')

SYSTEM = """너는 경복궁 해설사다. 관람객 요청에 맞는 테마 코스의 경로를 정한다. 설명 글은 따로 쓰므로 여기서는 쓰지 않는다.

규칙
1. 관람객 요청에서 시간(minutes)과 원하는 것(테마·관심사·동행 등)을 읽는다. 시간이 없으면 60분으로 한다.
   요청이 자연·역사가 아니어도(예: 왕비, 사진 찍기 좋은 곳, 조용한 곳) <documents> 에 근거해 맞는 곳을 고른다.
2. 후보(candidates)의 id 만 쓴다. 출발과 도착은 광화문(start)이다. 광화문을 보고 싶으면 stops 첫 번째에 넣는다.
3. 시간: walkMin[출발][첫 곳] + … + walkMin[마지막 곳][광화문] 걷기 합 + 모든 stayMin 합이 제한 시간 이하여야 한다. 계산해서 지킨다.
4. stayMin: 권역은 소속 건물(members)이 많을수록 길게(대략 3~15분), 자연 지점(kind water·wood·stream·spring)은 1~5분.
5. 같은 곳을 두 번 넣지 않는다. 걷기 분을 보고 되돌아가는 동선을 피한다.
6. why = 이 요청에 이곳을 이 순서로 넣은 이유 한 문장. <documents> 에 적힌 사실만 근거로 쓴다.
   자연 지점은 문서가 없으니 이름과 가까운 권역(natureNear) 문서에 나온 내용만 쓴다.
"""

DESCRIBE = """관람객 요청: {request}
아래 순서로 코스를 짰다 (괄호 = 넣은 이유, 참고용).
{stops}

이 코스를 관람객에게 소개하라. 먼저 1~2문장으로 이 요청에 맞춰 코스를 왜 이렇게 짰는지 말하고, 이어서 순서대로 곳마다 무엇을 볼 수 있는지 1~2문장씩 쓴다.
걷는 시간·거리·분은 화면이 실제 길로 다시 계산하므로 쓰지 않는다. 자연 지점(연못·숲 등)은 문서가 없으니 이름만 짧게 언급한다."""


def materials():
    return json.loads(INPUT.read_text(encoding='utf-8'))


def documents(col, cands):
    """권역 해설: DB 전각 문서를 권역(group)별로 — 건물마다 문단을 합쳐 앞부분만"""
    got = col.get(where={'type': 'building'})
    paras = {}  # 건물 이름 → (group, [문단])
    for d, m in zip(got['documents'], got['metadatas']):
        paras.setdefault(m['name'], (m['group'], []))[1].append(re.sub(r'^\[[^\]]*\] ', '', d))  # 앞의 [이름 | 영문 | 태국어] 뺌
    by_group = {}
    for name, (group, ps) in paras.items():
        by_group.setdefault(group, []).append(f"[{name}] {chr(10).join(ps)[:DOC_CHARS]}")
    parts = []
    for c in cands:
        if c['kind'] in NATURE:
            continue
        text = '\n'.join(by_group.get(c['name'], []))
        parts.append(f'<document id="{c["id"]}" name="{c["name"]}">\n{text}\n</document>')
    return '\n'.join(parts)


def schema(ids):
    stop = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'stayMin', 'why'],
            'properties': {'id': {'type': 'string', 'enum': ids}, 'stayMin': {'type': 'integer'}, 'why': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['minutes', 'stops'],
            'properties': {'minutes': {'type': 'integer'}, 'stops': {'type': 'array', 'items': stop}}}


def total(m, stops):
    """LLM 이 지켰는지 확인용 — 후보 걷기 분 행렬로 다시 셈 (실제 길은 fitCourse 가 계산)"""
    seq = [m['start'], *[s['id'] for s in stops], m['start']]
    return sum(m['walkMin'][a].get(b, 0) for a, b in zip(seq, seq[1:]) if a != b) + sum(s['stayMin'] for s in stops)


class Planner:
    """재료(후보·걷기 시간표·해설)와 DB 를 한 번 열어 두고 요청마다 LLM 두 번 (경로 → 설명)"""

    def __init__(self, col):
        self.col = col
        self.m = materials()
        self.cands = self.m['candidates']
        self.ids = [x['id'] for x in self.cands]
        self.name = {x['id']: x['name'] for x in self.cands}
        self.base = (f'<documents>\n{documents(col, self.cands)}\n</documents>\n\n<data>\n'
                     + json.dumps({k: self.m[k] for k in ('start', 'candidates', 'walkMin')}, ensure_ascii=False)
                     + '\n</data>\n\n')

    def route(self, request):
        """1단계: 요청 문장 → {minutes, stops:[{id, stayMin, why}]}. 시간이 없으면 60분"""
        c = json.loads(llm(SYSTEM, self.base + f'관람객 요청: {request}\n이 요청에 맞는 코스를 짜라.',
                           4000, schema=schema(self.ids), effort='medium'))
        print(f'[{request}] {c["minutes"]}분: {len(c["stops"])}곳 · 행렬 기준 {total(self.m, c["stops"]):.0f}분 — '
              + ' → '.join(self.name[s['id']] for s in c['stops']), flush=True)
        return c

    def describe(self, request, stops, language):
        """2단계: 고른 곳의 DB 문서를 고정으로 넣고 챗봇 규칙으로 코스 소개 → 카드 링크 붙은 답변"""
        fixed = {}
        for s in stops:  # 자연 지점(nature:)은 DB 문서가 없어 안 들어감
            add_fixed(self.col, {'type': 'building', 'nameId': s['id']}, fixed)
        # ponytail: 대표 전각 문서만 (권역 소속 건물 전부 넣으면 수만 자). 소속 건물 얘기가 빠지면 group 으로 넓힐 것
        lines = '\n'.join(f"{i}. {self.name[s['id']]}{' (자연 지점)' if s['id'].startswith('nature:') else ''} ({s['why']})"
                          for i, s in enumerate(stops, 1))
        text, _ = answer_docs(list(fixed.values()), DESCRIBE.format(request=request, stops=lines), language)
        return link_names(text, self.col.get(ids=list(fixed))['metadatas'], language)

    def make(self, request):
        """POST /api/course 응답: {answer, language, course: {minutes, stops: [{id, stayMin}]}}"""
        c = self.route(request)
        language = answer_language(request)
        return {'answer': self.describe(request, c['stops'], language), 'language': language,
                'course': {'minutes': c['minutes'], 'stops': [{'id': s['id'], 'stayMin': s['stayMin']} for s in c['stops']]}}


def serve(port=8001):
    """deploy/API.md 의 4개(/api/ask·/api/course·/api/card·/api/health)를 띄우는 확인용 임시 서버 (sim.html 의 코스·질문 칸이 부름).
    정식 API 서버는 아직 없음. 화면(정적 파일)은 sim.bat 의 http-server 가 띄움 — 지도 타일(pmtiles)이 Range 요청을 써서 파이썬 기본 서버로는 안 됨"""
    col = Collection()
    p, names = Planner(col), name_keys(col)
    routes = {'/api/course': ('request', lambda s: p.make(s)),
              '/api/ask': ('question', lambda s: reply(col, names, s)[0])}

    class Handler(BaseHTTPRequestHandler):
        def cors(self, code, body=None):
            self.send_response(code)
            self.send_header('Access-Control-Allow-Origin', '*')  # localhost:8080 의 sim.html 에서 부름
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            if body is not None:
                self.wfile.write(json.dumps(body, ensure_ascii=False).encode())

        def do_OPTIONS(self):
            self.cors(204)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == '/api/health':
                return self.cors(200, {'ok': True})
            if url.path == '/api/card':
                cid = parse_qs(url.query).get('id', [''])[0]
                if not cid:
                    return self.cors(400, {'error': '쿼리에 id 필요'})
                c = card(col, cid)
                return self.cors(200, c) if c else self.cors(404, {'error': '없는 카드'})
            self.send_response(200)  # 브라우저로 이 주소를 열었을 때 안내
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write('<meta charset="utf-8"><p>코스·질문 API 가 켜져 있음 (화면 아님).</p><p>화면: sim.bat 실행 → '
                             '<a href="http://localhost:8080/deploy/visit/sim.html">http://localhost:8080/deploy/visit/sim.html</a>'
                             ' 의 코스·질문 칸</p>'.encode())

        def do_POST(self):
            if self.path not in routes:
                return self.cors(404, {'error': '없는 경로'})
            field, run = routes[self.path]
            try:
                text = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)))[field].strip()[:300]
                assert text
            except Exception:
                return self.cors(400, {'error': f'JSON 본문에 {field} (빈 문자열 아님) 필요'})
            try:
                self.cors(200, run(text))
            except Exception as e:  # LLM·DB 실패는 화면 로그에 보이게
                self.cors(500, {'error': str(e)})

        def log_message(self, *a):
            pass

    print(f'API http://localhost:{port}/api/ (ask·course·card·health) — 화면은 sim.bat (끝내려면 Ctrl+C)', flush=True)
    ThreadingHTTPServer(('localhost', port), Handler).serve_forever()


if __name__ == '__main__':
    serve()
