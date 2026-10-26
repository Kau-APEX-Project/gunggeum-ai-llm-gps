"""답변·STT 문장 속 전각·용어 이름 → [이름](card:<id>) 링크, 그리고 카드 한 장 만들기.
RAG 답변(server/ask.py)과 STT 가 같이 씀. 의존성 없음(표준 라이브러리만). DB 는 server/pg.py 의 Collection 을 넘겨받음.

  RAG: link_names(답변, 답변에 쓴 문서 메타데이터, 언어)      → 쓴 문서만 링크
  STT: link_names(문장, col.get()['metadatas'], 언어)        → 전각·용어 전체에서 찾아 링크
  카드: card(col, 'b-160')  → {name, name_en, name_th, text, text_en, text_th, photo, ...} (없으면 None)
"""
import re


def card_id(meta):
    """카드 id: 전각은 문단이 아니라 전각 단위(b-<serial>), 용어는 t-<no>"""
    return f"b-{meta['serial']}" if meta['type'] == 'building' else f"t-{meta['no']}"


def link_names(text, metas, language):
    """답변에서 사용한 문서의 이름을 처음 나올 때만 [이름](card:<id>) 로 감쌈. 답변 언어의 표기로 찾음
    ('삼도 / 어도' → 삼도·어도, 'Geunjeongjeon (Throne Hall)' → Geunjeongjeon). 긴 이름부터 맞춰서
    '근정전' 안의 용어 '정전'이나 'Geunjeongjeon' 안의 'Jeongjeon' 은 링크하지 않음"""
    field = {'English': 'name_en', 'ภาษาไทย': 'name_th'}.get(language, 'name')
    labels = {}  # 소문자 이름 → 카드 id (영어 답변이 'ondol'처럼 소문자로 쓰기도 함)
    for m in metas:
        # 한국어 이름도 봄: 외국어 답변이 '답도'처럼 한글로 남긴 이름. 카드마다 한 번만 거니 'Gyeonghoeru (경회루)'는 앞만
        for p in re.sub(r'\(.*?\)', '', f"{m.get(field) or ''}/{m.get('name') or ''}").split('/'):  # 괄호 안에도 / 가 있음
            p = p.strip()
            if len(p) > 1:
                labels.setdefault(p.lower(), set()).add(card_id(m))
    # 같은 이름의 카드가 여럿: 전각이 있으면 전각('꽃담' 전각·용어), 용어끼리면 어느 쪽인지 몰라 링크 안 함
    # ('사신' = 四神 수호신·使臣 외교관. 괄호를 떼면 둘 다 '사신'이라 외국 사신이 수호신 카드로 걸렸음)
    for k, ids in list(labels.items()):
        ids = {c for c in ids if c.startswith('b-')} or ids
        if len(ids) == 1:
            labels[k] = ids.pop()
        else:
            del labels[k]
    if not labels:
        return text
    seen = set()

    def link(m):
        cid = labels[m[1].lower()]
        if cid in seen:
            return m[0]
        seen.add(cid)
        return f'[{m[1]}](card:{cid})'
    names = '|'.join(map(re.escape, sorted(labels, key=len, reverse=True)))
    return re.sub(f'(?<![A-Za-z])({names})(?![A-Za-z])', link, text, flags=re.I)


assert link_names('근정전과 정전, 근정전', [{'type': 'building', 'serial': 1, 'name': '근정전'},
                                      {'type': 'term', 'no': 9, 'name': '정전(正殿)'}], '한국어') \
    == '[근정전](card:b-1)과 [정전](card:t-9), 근정전'
assert link_names('Geunjeongjeon (근정전) and Four', [{'type': 'term', 'no': 9, 'name_en': 'Jeongjeon (main / Four)'}],
                  'English') == 'Geunjeongjeon (근정전) and Four'
assert link_names('Gyeonghoeru (경회루)', [{'type': 'building', 'serial': 3, 'name': '경회루', 'name_en': 'Gyeonghoeru'}],
                  'English') == '[Gyeonghoeru](card:b-3) (경회루)'
assert link_names('the ondol (온돌)', [{'type': 'term', 'no': 5, 'name': '온돌', 'name_en': 'Ondol (Heating)'}],
                  'English') == 'the [ondol](card:t-5) (온돌)'
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
