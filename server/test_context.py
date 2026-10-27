"""맥락 반영 확인 (키·DB 없이): python server/test_context.py — 후속 질문 규칙은 LLM 을 부르지 않음"""
import os

os.environ.setdefault('OPENAI_API_KEY', 'test')
import ask  # noqa: E402

GYO = {'type': 'building', 'serial': 164, 'name': '교태전', 'name_en': 'Gyotaejeon (Queen’s Bedchamber)',
       'name_th': 'คโยแทจอน (พระตำหนักบรรทมราชินี)'}


class Col:  # pg.Collection 흉내: 전각 serial·이름으로만 찾음
    def get(self, where=None):
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
assert ask.NOISE.sub('', 'ประทับของพระมเหสี เครดิตฟรี') == 'ประทับของพระมเหสี'  # 태국어 답변 끝 잡음
assert ask.NOISE.sub('', 'ชั้นบนกับชั้นล่าง ทดลองใช้ฟรี') == 'ชั้นบนกับชั้นล่าง'
assert ask.NOISE.sub('', 'ฟรีแลนซ์ เป็นอาคาร') == 'ฟรีแลนซ์ เป็นอาคาร'  # 끝 어절만
print('ok')
