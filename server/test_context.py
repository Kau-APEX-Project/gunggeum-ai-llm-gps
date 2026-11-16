"""맥락 반영 확인 (키·DB 없이): python server/test_context.py
- 지금 위치(here) 규칙은 LLM 을 부르지 않음
- 후속 질문은 intent 호출이 같이 다시 씀 → 가짜 LLM 으로 배관만 확인 (history 가 들어가는지, 다시 쓴 질문이 검색에 쓰이는지, 답변 언어는 원문 기준인지)"""
import json
import os

os.environ.setdefault('OPENAI_API_KEY', 'test')
import ask  # noqa: E402

GYO = {'type': 'building', 'serial': 164, 'name': '교태전', 'name_en': 'Gyotaejeon (Queen’s Bedchamber)',
       'name_th': 'คโยแทจอน (พระตำหนักบรรทมราชินี)', 'nameId': 'place:gyotaejeon'}
GEUN = {'type': 'building', 'serial': 160, 'name': '근정전', 'name_en': 'Geunjeongjeon (Throne Hall)', 'nameId': 'place:geunjeongjeon-hall'}


class Col:  # pg.Collection 흉내: type 만 주면 전각 전부 = places
    name = 'test'

    def get(self, where=None, ids=None):
        return {'metadatas': [GYO, GEUN] if where == {'type': 'building'} else []}


names = {'교태전': ['교태전', 'gyotaejeon', 'คโยแทจอน'], '근정전': ['근정전', 'geunjeongjeon']}
ask.SHOW_STAGES = False
ask.llm = lambda *a, **k: (_ for _ in ()).throw(AssertionError('LLM 을 부르면 안 됨'))
col = Col()
G = 'place:geunjeongjeon-hall'  # 지금 있는 곳 (폰이 보낸 here)
assert ask.prepare(col, names, '현재 내 위치를 설명해줘', G) == '현재 내 위치를 설명해줘 (근정전)'
assert ask.prepare(col, names, '지금위치 설명해', G) == '지금위치 설명해 (근정전)'
assert ask.prepare(col, names, 'Where am I?', G) == 'Where am I? (Geunjeongjeon)'
assert ask.prepare(col, names, '거기는 누가 살았어?', G) == '거기는 누가 살았어?'  # 거기 = 앞 대화 (intent 가 처리)
assert ask.prepare(col, names, '여기가 교태전이야?', G) == '여기가 교태전이야?'  # 이름을 물으면 위치 안 붙임
assert ask.prepare(col, names, '현재 내 위치를 설명해줘', 'place:none') == '현재 내 위치를 설명해줘'  # 모르는 id
assert ask.prepare(col, names, '현재 내 위치를 설명해줘') == '현재 내 위치를 설명해줘'  # here 없음

# 후속 질문: intent 한 번에 kind + standalone. 가짜 LLM 이 받은 입력을 기록
seen = []


def fake(system, user, max_tokens, schema=None, effort=None):
    seen.append(user)
    if schema:  # intent
        return json.dumps({'kind': 'question', 'to': None, 'amenity': None,
                           'standalone': 'Who lived in Gyotaejeon?' if '<history>' in user else 'IGNORED'})
    return '답변'


ask.llm = fake
h = [{'question': 'What is Gyotaejeon?', 'answer': '[Gyotaejeon](card:b-164) is the queen’s quarters.'}]
it = ask.intent(col, 'Who lived there?', h)
assert it['standalone'] == 'Who lived in Gyotaejeon?'
assert '<history>' in seen[-1] and 'card:' not in seen[-1] and 'Gyotaejeon is the queen' in seen[-1]  # 카드 링크는 지우고 넣음
assert ask.intent(col, '근정전이 뭐야?')['standalone'] == '근정전이 뭐야?'  # 앞 대화 없으면 원문 그대로 (LLM 이 바꿔도)
assert '<history>' not in seen[-1]

got = {}
ask.retrieve = lambda col, names, q, s: (got.setdefault('q', q), s.update(fixed=[], picked=[]), [])[2]
ask.link = lambda text, *a: text
ask.glosses = lambda col: None
r, _ = ask.reply(col, names, 'Who lived there?', h)
assert got['q'] == 'Who lived in Gyotaejeon?' and r['language'] == 'English'  # 검색은 다시 쓴 질문, 언어는 원문

# 코스 시간 맞춤(course.fit)·출발 권역(start_of): LLM·DB 없이
import course  # noqa: E402

M = {'start': 'A', 'walkMin': {'A': {'B': 5, 'C': 10}, 'B': {'A': 5, 'C': 5}, 'C': {'A': 10, 'B': 5}}}
S = [{'id': 'B', 'stayMin': 10}, {'id': 'C', 'stayMin': 10}]  # A→B→C→A = 5+5+10 + 20 = 40분
assert course.total(M, S) == 40 and course.fit(M, S, 45) == (S, [])  # 45 × 0.9 = 40.5 안
assert course.fit(M, S, 40) == (S[:1], S[1:])  # 36 밖 → 끝(C)부터 뺌 → A→B→A 20분
assert course.total(M, S[:1], 'C') == 5 + 5 + 10  # 출발 C: C→B→C
p = object.__new__(course.Planner)
p.col, p.m = col, {'start': 'place:gwanghwamun-gate'}
p.name = {'place:gwanghwamun-gate': '광화문', 'place:gyotaejeon': '교태전'}
p.ids = list(p.name)
GYO['group'] = '교태전'
assert p.start_of('place:gyotaejeon') == 'place:gyotaejeon'  # here 의 권역 = 후보
assert p.start_of('place:none') == p.start_of(None) == p.start_of(['x']) == 'place:gwanghwamun-gate'  # 모르면·형식 틀리면 광화문

# 들를 곳이 0곳이면 설명 LLM 을 안 부르고 고정 문장
p.route = lambda request, start: {'minutes': 5, 'stops': [{'id': 'B', 'stayMin': 10, 'why': ''}]}
p.m = {**M, 'start': 'A'}
p.name, p.ids = {'A': '가', 'B': '나'}, ['A', 'B']
p.describe = lambda *a: (_ for _ in ()).throw(AssertionError('0곳이면 describe 를 부르면 안 됨'))
r = p.make('5분 코스')
assert r['course'] == {'minutes': 5, 'start': 'A', 'stops': []} and r['answer'] == course.EMPTY_KO
assert p.make('5 minute tour')['answer'] == course.EMPTY['English']

# 모델이 JSON 을 두 번 이어 붙여 줘도 첫 객체만 읽음
ask.llm = lambda *a, **k: '{"kind": "question"}\n{"kind": "question"}'
assert ask.llm_json('s', 'u', 10) == {'kind': 'question'}

# 요청 본문 검사: 형식 틀린 history 항목은 버림, 최근 3개
assert ask.clean_history('x') == ask.clean_history(None) == []
assert ask.clean_history([{'question': 'a' * 400, 'answer': 'b'}, {'question': 1, 'answer': 'b'}, 'x', {'question': 'q'}]) \
    == [{'question': 'a' * 300, 'answer': 'b'}]
assert len(ask.clean_history([{'question': 'q', 'answer': 'a'}] * 5)) == 3

# 코스 요청: planner 가 있으면 코스 action (출발 = nearest, 없으면 here), 없으면 일반 질문
ask.llm = lambda system, user, max_tokens, schema=None, effort=None: json.dumps(
    {'kind': 'course', 'to': None, 'amenity': None, 'standalone': 'x'}) if schema else '답변'


class FakePlanner:
    def make(self, request, language=None, here=None):
        return {'answer': '코스 소개', 'language': language, 'course': {'minutes': 40, 'start': here, 'stops': []}}


r, _ = ask.reply(col, names, '왕비 위주로 40분 코스 짜줘', here='H', planner=FakePlanner(), nearest='N')
assert r['action'] == {'type': 'course', 'course': {'minutes': 40, 'start': 'N', 'stops': []}} and r['language'] == '한국어'
assert ask.reply(col, names, '코스 짜줘', here='H', planner=FakePlanner())[0]['action']['course']['start'] == 'H'  # nearest 없으면 here
assert ask.reply(col, names, '코스 짜줘', history='bad', here=['x'], planner=FakePlanner())[0]['action']['course']['start'] is None
assert ask.reply(col, names, '왕비 위주로 40분 코스 짜줘')[0]['action'] is None  # planner 없으면 일반 질문

assert ask.NOISE.sub('', 'ประทับของพระมเหสี เครดิตฟรี') == 'ประทับของพระมเหสี'  # 태국어 답변 끝 잡음
assert ask.NOISE.sub('', 'ชั้นบนกับชั้นล่าง ทดลองใช้ฟรี') == 'ชั้นบนกับชั้นล่าง'
assert ask.NOISE.sub('', 'ฟรีแลนซ์ เป็นอาคาร') == 'ฟรีแลนซ์ เป็นอาคาร'  # 끝 어절만
print('ok')
