"""답변·STT 문장 속 전각·용어 이름 → [이름](card:<id>) 링크, 그리고 카드 한 장 만들기.
RAG 답변(server/ask.py)과 STT 가 같이 씀. 의존성 없음(표준 라이브러리만). DB 는 server/pg.py 의 Collection 을 넘겨받음.

  RAG: link(답변, 답변에 쓴 문서 메타데이터, 언어, gloss_index(전체 메타))  → 이름 매칭 + 외국어는 DB 풀이 매칭, 쓴 문서만 링크
  STT: link_names(문장, col.get()['metadatas'], 언어)        → 전각·용어 전체에서 이름으로 찾아 링크
  카드: card(col, 'b-160')  → {name, name_en, name_th, text, text_en, text_th, photo, ...} (없으면 None)
"""
import re


def card_id(meta):
    """카드 id: 전각은 문단이 아니라 전각 단위(b-<serial>), 용어는 t-<no>"""
    return f"b-{meta['serial']}" if meta['type'] == 'building' else f"t-{meta['no']}"


LINK = re.compile(r'\[([^\[\]]*)\]\(card:([bt]-\d+)\)')


def unique(labels):
    """{이름: 카드 id 집합} → {이름: 카드 id}. 같은 이름의 카드가 여럿이면 전각 우선('꽃담' 전각·용어),
    용어끼리면 어느 쪽인지 몰라 뺌 ('사신' = 四神 수호신·使臣 외교관)"""
    out = {}
    for k, ids in labels.items():
        ids = {c for c in ids if c.startswith('b-')} or ids
        if len(ids) == 1:
            out[k] = ids.pop()
    return out


STOP = {'the', 'of', 'to', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'with', 'by', 'from', 'at', 'as', 'its', 'his', 'her',
        'their', 'king', 'queen', 'royal', 'palace', 'hall', 'building', 'gate'}  # 단독·끝말로는 어느 카드인지 모르는 말
THAI_TAIL = set('ะาำ' + ''.join(chr(c) for c in range(0x0E31, 0x0E3B)) + ''.join(chr(c) for c in range(0x0E47, 0x0E4F)))
THAI_HEAD = set('เแโใไ')  # 앞에 붙는 모음: 이걸로 끝나면 음절이 잘림
# 태국어 기능어 (~의, ~에, ~와 …): 풀이 안에서 낱말 경계로 씀
# 'ที่·ให้·แบบ'은 낱말 안에도 흔해서(เจ้าหน้าที่ 직원) 경계로 안 씀
THAI_JOIN = ('ของ', 'แห่ง', 'และ', 'ใน', 'บน', 'ใต้', 'สำหรับ', 'เพื่อ', 'จาก', 'กับ', 'หรือ', 'ด้วย', 'โดย')
ADJ = re.compile(r'(ed|al|ly|ive|ous|ful|ing|ic|ese|ish|an)$')


def stem(w):
    """영어 낱말 → 비교용 줄기 (소문자, 소유격·복수 뗌): Enthronements → enthronement, Ceremonies → ceremony"""
    w = re.sub(r"['’]s$", '', w.lower())
    if len(w) > 4 and w.endswith('ies'):
        return w[:-3] + 'y'
    if len(w) > 4 and re.search(r'(ch|sh|x|ss)es$', w):
        return w[:-2]
    return w[:-1] if len(w) > 3 and w.endswith('s') and not w.endswith('ss') else w


def surface(stems):
    """줄기들 → 답변 속 낱말을 찾는 정규식 (복수·소유격 허용, 사이는 띄어쓰기·하이픈)"""
    word = lambda s: (re.escape(s[:-1]) + '(?:y|ies)' if s.endswith('y') else re.escape(s) + '(?:e?s)?') + "(?:['’]s)?"
    return re.compile(r'(?<![A-Za-z])' + r'[\s\-]+'.join(map(word, stems)) + r'(?![A-Za-z])', re.I)


def gloss_keys(m):
    """카드 하나의 풀이 표현: name_en·name_th 괄호 안 ('Jeugwisik (Enthronement Ceremony)' → enthronement ceremony).
    영어는 연속 낱말 묶음(앞뒤가 흔한 말이 아닌 것), 태국어는 띄어쓰기가 없어 6자 이상 조각(음절이 안 잘리는 자리)"""
    keys = set()
    en = m.get('name_en') or ''
    # 괄호가 없으면 이름 자체가 영어 풀이 ('Reconstruction of Gyeongbokgung Palace', 'The Great Fire of ...')
    for g in re.findall(r'\(([^)]*)\)', en) or [en]:
        for part in re.split(r'[/,;]', g):
            ws = [stem(w) for w in re.findall(r"[A-Za-z][A-Za-z'’\-]*", part)]  # 하이픈 말은 한 낱말 (Multi-story → multi·story 로 안 쪼갬)
            content = [w for w in ws if w not in STOP]
            for i in range(len(ws)):
                for j in range(i + 1, len(ws) + 1):
                    k = tuple(ws[i:j])
                    if k[0] in STOP or k[-1] in STOP or len(''.join(k)) < 5:
                        continue
                    # 한 낱말은 그 풀이의 유일한 내용어일 때만: royal bedchamber hall → bedchamber, King's Mother → mother.
                    # 'Enthronement Ceremony'의 ceremony, 'Three Armies Headquarters'의 three 같은 낱말 하나로는 안 걺
                    if len(k) == 1 and (len(content) != 1 or ADJ.search(k[0])):
                        continue
                    keys.add(('en', k))
    for g in re.findall(r'\(([^)]*)\)', m.get('name_th') or ''):
        for part in g.split('/'):
            t = re.sub(r'\s', '', part)
            # 띄어쓰기가 없어 낱말 경계(풀이 앞뒤·띄어쓰기·기능어 앞뒤) 사이 조각만, 8자 이상. ตำหนักบรรทม|ของ|กษัตริย์ → ตำหนักบรรทม·กษัตริย์…
            # 아무 데서나 자르면 'นเหลี่ยม'(음절 중간), 'ตำหนักบรรทมขอ'(낱말 중간)처럼 잘림
            heads, tails, pos = {0}, {len(t)}, 0
            for w in part.split():
                heads.add(pos)
                tails.add(pos)
                pos += len(w)
            for jw in THAI_JOIN:
                for x in re.finditer(jw, t):
                    heads.add(x.end())
                    tails.add(x.start())
            for i in heads:
                for j in tails:
                    if j - i >= 8:
                        keys.add(('th', t[i:j]))
    return keys


def alias_key(lang, a):
    """별칭 → 매칭 키 (gloss_keys 와 같은 모양): 영어는 낱말 줄기 묶음, 태국어는 띄어쓰기 뺀 글자 (4자 이상)"""
    if lang == 'en':
        ws = tuple(stem(w) for w in re.findall(r"[A-Za-z][A-Za-z'’\-]*", a))
        return ('en', ws) if ws and len(''.join(ws)) >= 4 else None
    t = re.sub(r'\s', '', a)
    return ('th', t) if len(t) >= 4 else None


def gloss_index(all_metas, aliases=None):
    """카드 링크 색인 (서버 시작 때 한 번, ask.glosses). {'keys': {표현: 카드 id 집합}, 'own': {카드 id: {표현: 'safe'|'context'}},
    'alias': 별칭에서 온 표현, 'names': {카드 id: 한국어 이름}, 'homonyms': {이름: 한자 없이 쓰면 이 카드}}.
    표현 = DB 풀이(gloss_keys) + 검수한 별칭(aliases.json). 풀이 중 흔한 것은 버림:
    - 카드 5장 넘게 나오는 표현 (palace gate, ตำหนัก). 5장 이하는 답변 후보 안에서 하나로 좁혀질 때만 걺
    - 태국어 조각이 DB 태국어 번역(text_th) 15개 문서 넘게 나오면 일반 낱말 (สัญลักษณ์ 상징 17, ในช่วง ~동안 96)"""
    idx, own, names = {}, {}, {}
    for m in all_metas:
        names.setdefault(card_id(m), m['name'])
        for k in gloss_keys(m):
            idx.setdefault(k, set()).add(card_id(m))
    th = [re.sub(r'\s', '', m.get('text_th') or '') for m in all_metas]
    idx = {k: ids for k, ids in idx.items() if len(ids) <= 5 and not (k[0] == 'th' and sum(k[1] in d for d in th) > 15)}
    for k, ids in idx.items():
        for c in ids:
            own.setdefault(c, {})[k] = 'safe'
    alias = set()
    for cid, d in (aliases or {}).get('cards', {}).items():
        for kind, items in d.items():
            lang, tier = kind.split('_')
            for x in items:
                if k := alias_key(lang, x):
                    idx.setdefault(k, set()).add(cid)
                    own.setdefault(cid, {})
                    own[cid][k] = 'safe' if 'safe' in (tier, own[cid].get(k)) else tier
                    alias.add(k)
    return {'keys': idx, 'own': own, 'alias': alias, 'names': names, 'homonyms': (aliases or {}).get('homonyms', {})}


def present(cid, index, docs):
    """그 카드의 한국어 이름이 넣은 문서 본문(자기 용어집 문서는 빼고)에 실제로 나오는가 — context 별칭을 켤 조건.
    같은 이름이 여러 카드면('사신') 한자째 이름(사신(四神))이 나올 때만, 단 homonyms 에 적힌 카드는 한자 없는 이름도 셈"""
    name = index['names'][cid]
    root = re.sub(r'\(.*?\)', '', name.split('/')[0]).strip()
    for d in docs:
        if re.match(re.escape(root) + r'\s*\(', d) and '): ' in d[:300]:  # 자기 용어집 문서 ('침전(寢殿, …): …')
            continue
        if name in d:
            return True
        for p in (re.sub(r'\(.*?\)', '', x).strip() for x in name.split('/')):
            if len(p) > 1 and re.search(re.escape(p) + r'(?!\()', d) and index['homonyms'].get(p, cid) == cid:
                return True
    return False


def link_glosses(text, metas, language, index, context=None):
    """외국어 답변은 용어를 이름 없이 뜻으로 씀('held enthronements' = 즉위식). 이름 매칭이 못 건 카드를 풀이·별칭으로 걺.
    후보 = 넣은 문서의 카드 중 아직 안 건 것. safe 표현은 바로, context 표현은 그 카드의 한국어 이름이 문서 본문에 나올 때만.
    한 표현이 후보 둘 이상을 가리키면 안 걺. 긴 표현 먼저, 카드당 한 번"""
    lang = {'English': 'en', 'ภาษาไทย': 'th'}.get(language)
    if not lang or not index:
        return text
    seen = set(c for _, c in LINK.findall(text))
    docs = [d for d in (context or '').split('\n\n') if d.strip()]
    keys = {}
    for c in {card_id(m) for m in metas} - seen:
        on = None
        for k, tier in index['own'].get(c, {}).items():
            if k[0] != lang:
                continue
            if tier == 'context':
                on = present(c, index, docs) if on is None else on
                if not on:
                    continue
            keys.setdefault(k, set()).add(c)
    taken = [(x.start(), x.end()) for x in LINK.finditer(text)]  # 이미 건 링크 자리
    hits = []
    for k, ids in keys.items():
        pat = surface(k[1]) if lang == 'en' else re.compile(re.escape(k[1]))
        # 링크 안쪽 X. DB 풀이에서 온 대문자 한 낱말은 고유명사라 X (검수한 별칭은 됨: Coronation, Cheonrok)
        ok = lambda x: not any(a < x.end() and x.start() < b for a, b in taken) and \
            not (lang == 'en' and len(k[1]) == 1 and x[0][0].isupper() and k not in index['alias'])
        x = pat.search(text)
        while x and not ok(x):
            x = pat.search(text, x.end())
        if x:
            hits.append((x.start(), x.end(), ids))
    picked = []
    for a, b, ids in sorted(hits, key=lambda h: (h[0] - h[1], h[0])):  # 긴 표현 먼저, 같으면 앞자리 먼저
        left = ids - seen
        if len(left) == 1 and not any(p < b and a < q for p, q, _ in picked):
            seen |= left
            picked.append((a, b, left.pop()))
    for a, b, cid in sorted(picked, reverse=True):
        text = f'{text[:a]}[{text[a:b]}]({"card:" + cid}){text[b:]}'
    return text


def link(text, metas, language, index=None, context=None):
    """RAG 답변의 카드 링크 (LLM 없음): 이름 매칭 먼저, 외국어는 이름 없이 뜻으로 쓴 용어를 DB 풀이·검수 별칭으로.
    context = 답변에 넣은 문서 글 (context 별칭을 켤지 볼 때)"""
    return link_glosses(link_names(text, metas, language), metas, language, index, context)


def link_names(text, metas, language):
    """답변에서 사용한 문서의 이름을 처음 나올 때만 [이름](card:<id>) 로 감쌈. 답변 언어의 표기로 찾음
    ('삼도 / 어도' → 삼도·어도, 'Geunjeongjeon (Throne Hall)' → Geunjeongjeon). 긴 이름부터 맞춰서
    '근정전' 안의 용어 '정전'이나 'Geunjeongjeon' 안의 'Jeongjeon' 은 링크하지 않음.
    이미 걸린 [..](card:..) 는 건드리지 않고, 그 카드는 다시 걸지 않음"""
    field = {'English': 'name_en', 'ภาษาไทย': 'name_th'}.get(language, 'name')
    labels = {}  # 소문자 이름 → 카드 id (영어 답변이 'ondol'처럼 소문자로 쓰기도 함)
    for m in metas:
        # 한국어 이름도 봄: 외국어 답변이 '답도'처럼 한글로 남긴 이름. 카드마다 한 번만 거니 'Gyeonghoeru (경회루)'는 앞만
        for p in re.sub(r'\(.*?\)', '', f"{m.get(field) or ''}/{m.get('name') or ''}").split('/'):  # 괄호 안에도 / 가 있음
            p = p.strip()
            if len(p) > 1:
                labels.setdefault(p.lower(), set()).add(card_id(m))
        # 외국어 답변은 이름 대신 풀이만 쓰기도 함 ('Obok (Five Blessings)' → 'the Five Blessings'). 짧은 풀이(Zone·Hall)는 오탐이라 뺌
        if field != 'name':
            for g in re.findall(r'\(([^)]*)\)', m.get(field) or ''):
                for p in g.split('/'):
                    if len(p := p.strip()) >= 6:
                        labels.setdefault(p.lower(), set()).add(card_id(m))
    labels = unique(labels)  # 괄호를 떼면 '사신' 둘이 같아져 외국 사신이 수호신 카드로 걸렸었음
    if not labels:
        return text
    seen = set(c for _, c in LINK.findall(text))

    def one(m):
        cid = labels[m[1].lower()]
        if cid in seen:
            return m[0]
        seen.add(cid)
        return f'[{m[1]}](card:{cid})'
    names = re.compile(f"(?<![A-Za-z])({'|'.join(map(re.escape, sorted(labels, key=len, reverse=True)))})(?![A-Za-z])", re.I)
    # 이미 걸린 링크 조각은 그대로, 나머지에서만 찾음 (split 의 홀수 칸이 링크)
    parts = re.split(r'(\[[^\[\]]*\]\(card:[bt]-\d+\))', text)
    return ''.join(p if i % 2 else names.sub(one, p) for i, p in enumerate(parts))


assert link_names('근정전과 정전, 근정전', [{'type': 'building', 'serial': 1, 'name': '근정전'},
                                      {'type': 'term', 'no': 9, 'name': '정전(正殿)'}], '한국어') \
    == '[근정전](card:b-1)과 [정전](card:t-9), 근정전'
assert link_names('Geunjeongjeon (근정전) and Four', [{'type': 'term', 'no': 9, 'name_en': 'Jeongjeon (main / Four)'}],
                  'English') == 'Geunjeongjeon (근정전) and Four'
assert link_names('Gyeonghoeru (경회루)', [{'type': 'building', 'serial': 3, 'name': '경회루', 'name_en': 'Gyeonghoeru'}],
                  'English') == '[Gyeonghoeru](card:b-3) (경회루)'
assert link_names('the ondol (온돌)', [{'type': 'term', 'no': 5, 'name': '온돌', 'name_en': 'Ondol (Heating)'}],
                  'English') == 'the [ondol](card:t-5) (온돌)'
assert link_names('one of the Five Blessings, a zone', [{'type': 'term', 'no': 90, 'name': '오복', 'name_en': 'Obok (Five Blessings)'},
                                                      {'type': 'term', 'no': 8, 'name': '권역', 'name_en': 'Gwonyeok (Zone)'}],
                  'English') == 'one of the [Five Blessings](card:t-90), a zone'
_M = [{'type': 'term', 'no': 152, 'name': '즉위식', 'name_en': 'Jeugwisik (Enthronement Ceremony)'},
      {'type': 'building', 'serial': 160, 'name': '근정전', 'name_en': 'Geunjeongjeon (Throne Hall)'},
      {'type': 'term', 'no': 70, 'name': '사신(四神)'}, {'type': 'term', 'no': 122, 'name': '사신(使臣)'}]
_T = [{'type': 'term', 'no': 3, 'name': '침전', 'name_en': 'Chimjeon (royal bedchamber hall)', 'name_th': 'ชิมจอน (ตำหนักบรรทมของกษัตริย์)'},
      {'type': 'term', 'no': 17, 'name': '조정', 'name_en': 'Jojeong (Royal Court / Main Courtyard)'}]
_IDX = gloss_index(_M + _T)
assert link('Geunjeongjeon held enthronement ceremonies in the hall', _M, 'English', _IDX) \
    == '[Geunjeongjeon](card:b-160) held [enthronement ceremonies](card:t-152) in the hall'  # 이름 + 풀이, 흔한 말(hall)은 안 걺
assert link('held enthronements', _M, 'English', _IDX) == 'held enthronements'  # 내용어가 둘인 풀이는 한 낱말만으로 안 걺
assert link("the king's bedchambers", _T, 'English', _IDX) == "the king's [bedchambers](card:t-3)"  # 복수형도 찾음
assert link('เป็นตำหนักบรรทม', _T, 'ภาษาไทย', _IDX) == 'เป็น[ตำหนักบรรทม](card:t-3)'  # 태국어는 풀이 조각으로
assert link('enthronements', _M, 'English') == 'enthronements'  # 색인 없이 부르면(STT 등) 이름 매칭만
_A = {'cards': {'t-152': {'en_safe': ['coronation']}, 't-3': {'en_context': ['sleeping quarters']},
                't-70': {'en_context': ['guardian deities']}, 't-122': {'en_context': ['envoy']}}, 'homonyms': {'사신': 't-122'}}
_IA = gloss_index(_M + _T, _A)
assert link('the Coronation was held', _M, 'English', _IA) == 'the [Coronation](card:t-152) was held'  # 별칭(safe), 대문자도 됨
assert link('his sleeping quarters', _T, 'English', _IA, '강녕전은 국왕의 침전이다.') == 'his [sleeping quarters](card:t-3)'
assert link('his sleeping quarters', _T, 'English', _IA, '침전(寢殿, …): 왕이 자는 곳') == 'his sleeping quarters'  # 자기 용어집 문서만 있으면 X
assert link('guardian deities and an envoy', _M, 'English', _IA, '외국 사신 접견') == 'guardian deities and an [envoy](card:t-122)'
assert link('guardian deities and an envoy', _M, 'English', _IA, '사신(四神)을 새김') == '[guardian deities](card:t-70) and an envoy'
assert link_names('외국 사신과 꽃담', [{'type': 'term', 'no': 70, 'name': '사신(四神)'},
                                {'type': 'term', 'no': 122, 'name': '사신(使臣)'},
                                {'type': 'term', 'no': 67, 'name': '꽃담'},
                                {'type': 'building', 'serial': 171, 'name': '꽃담'}], '한국어') \
    == '외국 사신과 [꽃담](card:b-171)'


def card(col, cid):
    """카드 한 장 (col: server/pg.py 의 Collection): 전각(b-<serial>)은 문단을 모두 합친 설명·사진, 용어(t-<no>)는 용어집 문서. 없으면 None"""
    if not re.fullmatch(r'[bt]-\d+', cid or ''):  # 'x-1' 이 용어 카드로 가지 않게
        return None
    kind, _, num = cid.partition('-')
    if kind == 'b':
        got = col.get(where={'type': 'building', 'serial': num})
        text = '\n\n'.join(re.sub(r'^\[[^\]]*\] ', '', d) for d in got['documents'])  # 앞의 [이름 | 영문 | 태국어] 뺌
    else:
        got = col.get(where={'type': 'term', 'no': num})
        text = got['documents'][0] if got['ids'] else ''
    if not got['ids']:
        return None
    m = got['metadatas'][0]
    # 영어·태국어 설명 (local/rag/build/translate.py). 문단 하나라도 번역이 없으면 None → 앱은 text(한국어)를 씀
    tr = {f'text_{l}': '\n\n'.join(x[f'text_{l}'] for x in got['metadatas'])
          if all(x.get(f'text_{l}') for x in got['metadatas']) else None for l in ('en', 'th')}
    return {'id': cid, 'type': m['type'], 'name': m['name'], 'name_en': m.get('name_en'), 'name_th': m.get('name_th'),
            'text': text, **tr, 'category': m.get('category'),
            'photo': f'/photos/{num}.jpg' if m.get('photo_license') else None,  # 파일: data/경복궁_전각_사진/<serial>.jpg
            'photo_license': m.get('photo_license'), 'photo_source': m.get('photo_source')}
