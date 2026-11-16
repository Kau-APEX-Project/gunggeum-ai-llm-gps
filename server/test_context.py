"""맥락 반영 확인 (키·DB 없이): python server/test_context.py — 후속 질문 규칙은 LLM 을 부르지 않음"""
import os

os.environ.setdefault('OPENAI_API_KEY', 'test')
import ask  # noqa: E402

GYO = {'type': 'building', 'serial': 164, 'name': '교태전', 'name_en': 'Gyotaejeon (Queen’s Bedchamber)',
       'name_th': 'คโยแทจอน (พระตำหนักบรรทมราชินี)'}


GEUN = {'type': 'building', 'serial': 160, 'name': '근정전', 'name_en': 'Geunjeongjeon (Throne Hall)', 'nameId': 'place:geunjeongjeon-hall'}


class Col:  # pg.Collection 흉내: 전각 serial·이름으로만 찾음 (type 만 주면 전각 전부 = places)
    name = 'test'

    def get(self, where=None):
        if where == {'type': 'building'}:
            return {'metadatas': [GYO, GEUN]}
        hit = str(where.get('serial')) == '164' or where.get('name') == '교태전'
        return {'metadatas': [GYO] if hit else []}


names = {'교태전': ['교태전', 'gyotaejeon', 'คโยแทจอน'], '근정전': ['근정전', 'geunjeongjeon']}
h = [{'question': '교태전이 뭐야?', 'answer': '[교태전](card:b-164)은 왕비의 침전입니다.'}]
ask.llm = lambda *a, **k: (_ for _ in ()).throw(AssertionError('LLM 을 부르면 안 됨'))
col = Col()
assert ask.prepare(col, names, '거기는 누가 살았어?', h) == '거기는 누가 살았어? (교태전)'
assert ask.prepare(col, names, 'How do I get there?', h) == 'How do I get there? (Gyotaejeon)'  # 질문 언어로
assert ask.prepare(col, names, 'ไปที่นั่นยังไง', h) == 'ไปที่นั่นยังไง (คโยแทจอน)'
assert ask.prepare(col, names, '처음 지은 건 언제야?', h) == '처음 지은 건 언제야? (교태전)'  # 주어를 뺀 질문
assert ask.prepare(col, names, '그럼 왕비는 어디서 지냈어?', h) == '그럼 왕비는 어디서 지냈어?'  # 스스로 주어가 있음
assert ask.prepare(col, names, '근정전은 뭐야?', h) == '근정전은 뭐야?'  # 다른 이름을 물음
assert ask.prepare(col, names, '화장실 어디야?', h) == '화장실 어디야?'
assert ask.prepare(col, names, '거기는 누가 살았어?', []) == '거기는 누가 살았어?'  # 기록 없음
assert ask.prepare(col, names, 'Who lived there?', [{'question': '교태전이 뭐야?', 'answer': '왕비의 침전입니다.'}]) \
    == 'Who lived there? (Gyotaejeon)'  # 답변에 카드가 없으면 앞 질문의 이름
G = 'place:geunjeongjeon-hall'  # 지금 있는 곳 (폰이 보낸 here)
assert ask.prepare(col, names, '현재 내 위치를 설명해줘', h, G) == '현재 내 위치를 설명해줘 (근정전)'  # 앞 대화(교태전)보다 위치
assert ask.prepare(col, names, '지금위치 설명해', [], G) == '지금위치 설명해 (근정전)'  # 기록 없어도
assert ask.prepare(col, names, 'Where am I?', [], G) == 'Where am I? (Geunjeongjeon)'
assert ask.prepare(col, names, '거기는 누가 살았어?', h, G) == '거기는 누가 살았어? (교태전)'  # 거기 = 앞 대화
assert ask.prepare(col, names, '여기가 교태전이야?', [], G) == '여기가 교태전이야?'  # 이름을 물으면 위치 안 붙임
assert ask.prepare(col, names, '현재 내 위치를 설명해줘', h, 'place:none') == '현재 내 위치를 설명해줘 (교태전)'  # 모르는 id
assert ask.NOISE.sub('', 'ประทับของพระมเหสี เครดิตฟรี') == 'ประทับของพระมเหสี'  # 태국어 답변 끝 잡음
assert ask.NOISE.sub('', 'ชั้นบนกับชั้นล่าง ทดลองใช้ฟรี') == 'ชั้นบนกับชั้นล่าง'
assert ask.NOISE.sub('', 'ฟรีแลนซ์ เป็นอาคาร') == 'ฟรีแลนซ์ เป็นอาคาร'  # 끝 어절만
print('ok')
