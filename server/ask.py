"""경복궁 해설 챗봇: Postgres(pgvector)에서 검색한 문서로 GPT-6 Luna(OpenAI API)가 답한다.
질문당 LLM 호출: 의도(길·편의시설·질문) → [질문] 번역(영어·태국어만) → 문서 고르기 → 답변 = 최대 4번,
[길 묻기] 의도 → 답변(목적지 문서 고정) = 2번, [편의시설] 의도 1번. 답변 언어: 한국어·English·ภาษาไทย(태국어).

사용: python deploy/server/ask.py                  대화 모드 (빈 줄 / exit 로 종료)
      python deploy/server/ask.py "강녕전이 뭐야?"   한 번만 묻기  [--debug: 검색된 청크 표시]
      python deploy/server/ask.py "교태전 어떻게 가?"  → 짧은 소개 + [action] {"type": "route", "to": ...}
"""
import json
import math
import os
import re
import sys
import time
from collections import Counter

from korean_romanizer.romanizer import Romanizer
from openai import OpenAI

import config  # noqa: F401  deploy/.env 로드 (OPENAI_API_KEY)
from cards import link_names
from pg import Collection

THAI = re.compile('[฀-๿]')  # 태국 문자

SHOW_STAGES = True # True면 질문마다 0~3단계에서 뽑은 문서를 터미널에 바로 보여줌

luna = OpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=10)  # deploy/.env (config.py가 로드)
usage = Counter()  # 호출별 토큰 합계 (eval이 비용 계산에 씀)


def llm(system, user, max_tokens, schema=None, effort=None):
    """텍스트 입력 → 텍스트 출력. GPT-6 Luna (추론 effort 또는 LUNA_EFFORT, 기본 none).
    schema(JSON Schema)를 주면 그 모양의 JSON 문자열로만 답함 (course.py)"""
    t = time.time()
    fmt = {'text': {'format': {'type': 'json_schema', 'name': 'out', 'schema': schema, 'strict': True}}} if schema else {}
    res = luna.responses.create(model='gpt-6-luna', reasoning={'effort': effort or os.environ.get('LUNA_EFFORT') or 'none'},
                                max_output_tokens=max_tokens + 2000,  # 추론 토큰도 여기 포함
                                instructions=system, input=user, **fmt)
    usage.update(calls=1, input=res.usage.input_tokens, output=res.usage.output_tokens, seconds=time.time() - t)
    return res.output_text


# 해설사처럼 덧붙여 설명하게 함 (좁은 질문도 1~2문장으로 끝내지 않음). 용어 표기는 나중에 용어집 링크로 씀
SYSTEM = """너는 경복궁 해설사다. 관람객에게 궁궐의 건물과 문화유산을 설명한다.

<documents>
{context}
</documents>

<answer_language>{language}</answer_language>

관람객의 질문에 답할 때 아래 규칙을 따른다.

1. 언어
- 답변 전체를 {language}로 쓴다. 질문이나 문서가 다른 언어여도 마찬가지다.
- {language}가 English나 ภาษาไทย(태국어)이면 문서의 사실을 그 언어로 자연스럽게 옮긴다. 한국어 문장을 번역하지 않고 남겨 두지 않는다. 한국어는 6번 규칙의 괄호 안에만 쓸 수 있다.
- 뜻을 정확히 옮긴다. 일어난 일을 바꾸지 않는다. 폐지된 관청은 "abolished"이지 "dismantled"가 아니고, 무쇠는 "iron"이지 "stone"이 아니다.

2. 문서만 사용
- <documents>에 적힌 사실만 쓴다. 외부 지식, 추측, 문서에 없는 날짜·숫자·이름을 더하지 않는다.
- 확실히 아는 사실이라도 더하지 않는다. 문서에 "임진왜란 때 소실"만 있으면 "1592년"이나 "(1592–1598)"을 붙이지 않는다.
- "안내소에 문의하세요", "공식 홈페이지를 확인하세요" 같은 조언을 넣지 않는다.

3. 문서 고르기
- 먼저 질문이 어느 건물이나 대상에 관한 것인지 정한다.
- 그 대상에 관한 문서를 모두 써서 하나의 답으로 합친다.
- 다른 건물이나 관계없는 주제의 문서는 <documents>에 있어도 무시한다. 예: 근정전 질문에는 경회루 문서를 쓰지 않는다.

4. 정보가 없을 때
- 문서가 질문의 일부만 다루면 그 부분만 답하고, 빠진 부분을 한 문장으로 밝힌다.
  - English: "The documents do not cover [missing part]."
  - ภาษาไทย: "เอกสารไม่มีข้อมูลเกี่ยวกับ [빠진 부분]"
  - 한국어: "[빠진 부분]에 대한 내용은 자료에 없습니다."
- 관련 문서가 하나도 없으면 그 한 문장만 답한다.
- 사과, 추측, 정보가 없는 이유 설명을 하지 않는다.

5. 구성과 길이
- 해설사가 관람객에게 설명하듯 답한다. 묻는 것만 답하고 끝내지 않는다.
- 사실 하나를 묻는 좁은 질문: 첫 문장에서 바로 답한다. 이어서 문서에 있는 관련 내용(그 건물의 용도, 관련 건물·인물, 그 일이 일어난 배경)을 2~4문장 덧붙인다. 덧붙일 내용이 문서에 없으면 덧붙이지 않는다.
- 덧붙인 내용에 대해 "The documents do not cover ...", "자료에 없습니다"를 쓰지 않는다. 이 문장은 질문이 물은 것이 문서에 없을 때만 쓴다. 질문에 답했으면 "자세한 내용은 자료에 없다"를 덧붙이지 않는다.
- 질문이 물은 것이 문서에 없으면 4번 규칙의 문장을 글자 그대로 쓴다. "do not provide", "do not identify"처럼 바꾸지 않는다.
- 문서에 없는 연도를 계산해서 쓰지 않는다. 문서에 "1553년 소실, 이듬해 복구"만 있으면 "1554년"이라고 쓰지 않고 "이듬해"라고 쓴다. 질문이 '언제'를 물으면 문서에 있는 가장 최근 일을 먼저 답한다.
- "X에 대해 알려줘", "X가 뭐야?", "What is X?"처럼 대상 하나를 묻는 질문은 넓은 질문이다. 문서에 있는 내용을 빠짐없이 써서 2~4문단으로 답한다.
- 넓은 질문은 정의 → 창건 → 건축 → 연혁 → 복원 순서로 쓴다. 문서에 없는 단계는 건너뛴다.
- 일반 문단으로 쓴다. 제목이나 글머리 기호를 쓰지 않는다.
- 인사, 질문 칭찬, 맺음말, 요약을 넣지 않는다.

6. 이름과 용어
- 옛 이름: 문서가 그 시기의 이름을 따로 적었으면 그 시기에는 그 이름을 쓴다. 예: 1426년에는 홍례문(弘禮門)으로 이름 지어졌다. "흥례문이 그때 명명되었다"고 쓰지 않는다.
- 영어 답변:
  - 고유명사는 로마자 뒤에 괄호로 한글을 쓴다. 예: Geunjeongjeon (근정전). 한글은 처음 나올 때만 붙이고, 그 뒤로는 로마자만 쓴다.  - 문서에 공식 영문·로마자 표기가 있으면 그대로 쓴다. 없으면 국어의 로마자 표기법을 따른다. 예: 사신 → Sasin, 답도 → Dapdo. "SaChin", "Tapdao" 같은 표기는 쓰지 않는다.
  - 건축 용어는 문서 괄호 안에 영문 용어가 있으면 그대로 쓴다.
  - 영문 용어가 없으면 글자 그대로 직역하지 않는다. 로마자로 쓰고 쉬운 말로 풀어 쓴다. 예: "dapdo (답도), a carved stone slab set in the center of the stairway."
  - 한 글자씩 직역하지 않는다. 예: 팔작지붕은 "octagonal roof"가 아니다.
- 태국어 답변:
  - 고유명사는 태국 문자 음역 뒤에 괄호로 한글을 쓴다. 예: คึนจองจอน (근정전). 한글은 처음 나올 때만 붙이고, 그 뒤로는 태국 문자만 쓴다.
  - 건물·용어 문서 머리의 태국어 명칭(태국 문자)을 그대로 쓴다. 없으면 한국어 발음을 태국 문자로 옮긴다(영어 철자를 옮기지 않는다).
  - 그 밖의 규칙은 영어 답변과 같다(직역 금지, 쉬운 말 풀이).
- 한국어 답변:
  - 이름과 용어는 문서에 적힌 그대로 쓴다.
  - 괄호는 문서에 있는 그 단어의 한자를 붙일 때만 쓴다. 예: 집현전(集賢殿). 다른 한글 이름, 추측한 이름, 같은 단어를 괄호에 넣지 않는다. 틀린 예: 수정전(수종전), 답도(답도).

7. 용어집 용어와 고유명사
- 건물·용어·인물 이름은 문서에 적힌 표기를 한 글자도 바꾸지 않고 쓴다.
- 태국어 답변에서는 문서 머리·괄호 안의 태국어 명칭을 한 글자도 바꾸지 않고 쓴다.
- 영어 답변에서는 문서 괄호 안의 공식 영문·로마자 표기를 그대로 쓴다. 예: 답도(踏道, Dapdo ...) → Dapdo. 하이픈, 띄어쓰기, 철자를 바꾸지 않는다. Geoncheonggung을 Geon-cheong-gung이나 Geoncheongung으로 쓰지 않는다.
- 이름을 영어 설명으로만 바꾸지 않는다. 답도를 "stone pathway"라고만 쓰지 않고 "Dapdo, a carved stone pathway"처럼 이름을 함께 쓴다.
- 영어 답변에서 용어집 문서로 받은 용어는 처음 나올 때 반드시 "로마자 (한글), 풀이" 순서로 쓴다. 예: "Seokchuk (석축), stone retaining walls", "Sipjangsaeng (십장생), the ten symbols of longevity". 틀린 예: "stone retaining walls", "symbols of longevity"처럼 풀이만 쓰기.
- 답에 필요한 관련 건물·용어는 "the hall", "the gate", "그 건물"로 뭉뚱그리지 않고 이름으로 쓴다.
- 건물 이름은 문서 머리 [한글 이름 | 영문 명칭 | 태국어 명칭]의 표기를 쓴다. 영어 답변은 영문 명칭의 괄호 앞 로마자, 태국어 답변은 태국어 명칭의 괄호 앞 음역을 쓴다. 예: [흥례문 | Heungnyemun (Palace Gate) | ฮึงนเยมุน (ประตูพระราชวัง)] → Heungnyemun, ฮึงนเยมุน.
- 관직명은 비슷한 서양 직함으로 옮기지 않는다. 판삼사사를 "Vice President"로 쓰지 않고 "Pansamsasa, head of the Three Offices"처럼 로마자와 풀이로 쓴다.
- 용어집 문서 끝의 "관련 전각:" 목록은 그 용어가 나오는 건물 목록일 뿐이다. 이것을 "~와 관련된 제도", "협길당·경성전에 있는 장식"처럼 문장으로 옮기지 않는다.
- 문서에서 무엇이 기록이고 무엇이 잘못 알려진 것인지 바꾸지 않는다. 예: "사자로 잘못 알려졌으나 기록으로 천록임이 확인되었다"를 "옛 기록에 사자로 나온다"로 쓰지 않는다.

답하기 전에 여섯 가지를 확인한다. 모든 문장이 {language}인가? 모든 사실·날짜·이름이 <documents>에 있는가? 모든 이름이 6번 규칙을 따르는가? (영어 답변) 쓴 용어집 용어마다 로마자 이름이 있는가? 괄호가 맞는가? 인사와 조언이 없는가?"""


def key(text):
    return re.sub(r'\s', '', text).lower()


def name_keys(col):
    """{전각·용어 이름: 비교용 키들} — 질문에 이름이 있는지 볼 때 씀.
    키: 한글, 로마자, 영문·태국어 명칭의 괄호 앞(Jiphyeonjeon). 1글자 한글(궐, 칸)·4글자 이하 로마자는 오탐이 많아 뺌"""
    got = col.get(include=['metadatas', 'documents'])
    names = {}
    for d, m in zip(got['documents'], got['metadatas']):
        parts = [re.sub(r'\(.*?\)', '', p).strip() for p in m['name'].split('/')]  # '삼도 / 어도', '사신(四神)'
        keys = names.setdefault(m['name'], [key(parts[0])])
        keys += [key(p) for p in parts if len(p) > 1]
        keys += [key(Romanizer(p).romanize()) for p in parts]
        for col in ('name_en', 'name_th'):  # 'Hyeonpan / Pyeonaek (plaque)' → hyeonpan, pyeonaek
            keys += [key(re.sub(r'\s*\(.*', '', x)) for x in m.get(col, '').split(' / ') if x.strip()]
    return {n: [k for i, k in enumerate(ks) if i == 0 or len(k) >= 5 or re.search('[가-힣]', k) and len(k) > 1]
            for n, ks in names.items()}


def named(names, question):
    q = key(question)
    found = [n for n, keys in names.items() if len(keys[0]) > 1 and any(k in q for k in keys)]
    # '근정전 조정'을 물었으면 '근정전'은, '근정전'을 물었으면 용어 '정전'은 빼기 (더 긴 이름에 포함된 이름 제외)
    return [n for n in found if not any(n != o and names[n][0] in names[o][0] for o in found)]


def to_korean(question):
    """영어 질문 → 한국어 검색어 (벡터 검색용. 답변 언어는 그대로)"""
    return llm('경복궁 안내 질문을 한국어로 번역하라. 궁궐 건물·인물 이름은 한국어 고유명사로 바꿔라 '
               '(Gyotaejeon → 교태전, Empress Myeongseong → 명성황후). 번역문만 출력하라.',
               question, 200).strip()


def bigrams(text):
    t = re.sub(r'[^0-9a-z가-힣]', '', text.lower())
    return [t[i:i + 2] for i in range(len(t) - 1)]


def keyword_index(col, _cache={}):
    if col.name in _cache:
        return _cache[col.name]
    got = col.get(include=['documents', 'metadatas'])
    rows = list(zip(got['ids'], got['documents'], got['metadatas']))
    tfs = [Counter(bigrams(d)) for _, d, _ in rows]
    df = Counter(g for tf in tfs for g in tf)
    _cache[col.name] = rows, tfs, df, sum(sum(tf.values()) for tf in tfs) / len(tfs)
    return _cache[col.name]


def keyword_scores(col, query, k1=1.5, b=0.75):
    """문서마다 BM25 점수 (keyword_index 의 rows 순서)"""
    rows, tfs, df, avg = keyword_index(col)
    q = set(bigrams(query))

    def score(tf):
        size = sum(tf.values())
        return sum(math.log(1 + (len(tfs) - df[g] + 0.5) / (df[g] + 0.5)) * tf[g] * (k1 + 1)
                   / (tf[g] + k1 * (1 - b + b * size / avg)) for g in q if g in tf)
    return [score(tf) for tf in tfs]


def keyword_search(col, query, n):
    """BM25(두 글자 단위). 벡터 검색이 놓치는 글자 그대로의 일치를 잡음
    ('관을 모신 건물' → 태원전 '관을 모시는 빈전'은 벡터 검색 40위 밖)"""
    rows, scores = keyword_index(col)[0], keyword_scores(col, query)
    ranked = sorted(range(len(rows)), key=scores.__getitem__, reverse=True)
    return [rows[i] for i in ranked[:n]]


SELECT = """아래 <documents>는 경복궁 안내 질문에 답하려고 모은 후보 문서다. 문서마다 <doc n="번호">로 감싸 있다.
질문에 답하는 데 필요한 문서의 n 번호를 관련 높은 순서로 최대 {n}개 골라라.
- 문서 머리의 [건물 이름]이 질문 대상과 달라도 본문이 질문 대상을 다루면 고른다. 예: 집현전 질문에는 '수정전 자리에 집현전이 있었다'는 수정전 문단을 고른다.
- 본문이 질문 대상을 다루지 않는 문서는 고르지 않는다.
- 반드시 1개 이상 고른다. 질문과 가장 가까운 문서라도 고른다.
n 번호만 쉼표로 구분해 출력하라. 문서 본문의 숫자(연도, 칸 수)를 쓰지 않는다."""


def label(doc):
    """문서 이름: 전각 '[근정전 조정 Geunjeongjeon Jojeong] ...' → '근정전 조정', 용어 '답도(踏道, ...): ...' → '답도'"""
    if m := re.match(r'\[([^\]]*)\]', doc):
        return ' '.join(w for w in m[1].split() if re.search('[가-힣]', w))
    return re.split(r'[(:]', doc, maxsplit=1)[0]


def show(title, items=()):
    """SHOW_STAGES일 때 단계 결과를 바로 출력. items = (문서 id, 본문)"""
    if SHOW_STAGES:
        print(title + ''.join(f'\n    {i} {label(d)}' for i, d in items), flush=True)


def select(question, docs, n=8):
    """후보 중 답하는 데 필요한 문서 최대 n개 (LLM이 고름)"""
    if not docs:
        return []
    listing = '\n'.join(f'<doc n="{i}">{d}</doc>' for i, d in enumerate(docs, 1))  # 0번부터 매기면 0번을 안 고름
    text = llm(SELECT.format(n=n), f'<documents>\n{listing}\n</documents>\n\n질문: {question}', 50)
    show(f'[3] AI 응답 원문: {text.strip()!r}')
    picked = dict.fromkeys(int(x) - 1 for x in re.findall(r'\d+', text) if 0 < int(x) <= len(docs))
    return [docs[i] for i in list(picked)[:n]]


def add_fixed(col, where, fixed):
    """where 에 맞는 문서를 문단 순서대로 fixed({id: 문서})에 넣음. 전각이면 그 전각에 나오는 용어도
    (용어집의 영문 표기를 쓰게 하려고 — 근정전 22개. 후보로 돌리면 답도 등이 빠짐). 길 묻기·코스도 이걸로 문서를 고정함"""
    got, terms = col.get(where=where), col.get(where={'type': 'term'})
    for i, d, m in sorted(zip(got['ids'], got['documents'], got['metadatas']),
                          key=lambda x: (x[0][0], int(x[0].rsplit('-', 1)[1]))):  # 전각 문단 순서대로
        fixed[i] = d
        if m['type'] == 'building':
            tag = f"{m['serial']} {m['name']}"
            for ti, td, tm in zip(terms['ids'], terms['documents'], terms['metadatas']):
                if tag in tm.get('buildings', '').split(', '):
                    fixed[ti] = td
    return got


def retrieve(col, names, question, stages=None):
    """고정: 질문에 나온 전각의 해설 문단 전부, 질문에 나온 용어, 그 전각의 용어(영문 표기용).
    후보: 용어를 언급한 전각 문단 용어당 5개, 벡터 검색 25개, 키워드 검색 25개 → LLM이 최대 8개 고름.
    stages에 dict를 넘기면 단계별 문서 id를 채움 (eval_retrieval.py가 씀)"""
    fixed, cand = {}, {}
    s = stages if stages is not None else {}
    search = question if re.search('[가-힣]', question) else to_korean(question)  # 문서가 한국어라 영어 질문은 벡터 검색이 약함
    show(f'[0] 검색어: {search}' + ('' if search == question else ' (외국어 질문 번역)'))
    # 태국어 질문은 사용자가 쓴 음역이 명칭과 다를 수 있어 번역문에서도 이름을 찾음
    s['term'], s['names'] = [], named(names, question + ' ' + search if THAI.search(question) else question)
    for n in s['names']:
        got = add_fixed(col, {'name': n}, fixed)
        if got['metadatas'] and got['metadatas'][0]['type'] == 'term':
            # 용어를 언급한 전각 문단 중 질문과 가까운 5개 (영어 질문은 벡터 검색만으로는 '수정전 자리의 집현전' 문단을 못 찾음)
            rel = col.query(query_texts=[search], n_results=5, where={'type': 'building'},
                            where_document={'$contains': re.sub(r'\(.*?\)', '', n.split('/')[0]).strip()})
            cand.update(zip(rel['ids'][0], rel['documents'][0]))
            s['term'] += rel['ids'][0]
    show(f"[1] 고정 {len(fixed)}개 ({', '.join(s['names']) or '이름 없음'})", fixed.items())
    # 후보는 넓게 (잡상은 벡터 9위·키워드 7위라 상위 5개로는 못 찾음)
    hits = col.query(query_texts=[search], n_results=25)
    show(f"[2-1] 임베딩 {len(hits['ids'][0])}개", zip(hits['ids'][0], hits['documents'][0]))
    kw = keyword_search(col, search, 25)
    show(f'[2-2] 키워드 {len(kw)}개', [(i, d) for i, d, _ in kw])
    show(f"[2-3] 용어 문단 {len(s['term'])}개", [(i, cand[i]) for i in s['term']])
    cand.update(zip(hits['ids'][0], hits['documents'][0]))
    cand.update((i, d) for i, d, _ in kw)
    show(f'[2] 후보 {sum(i not in fixed for i in cand)}개 → AI에게 넘김')
    # 영어 질문은 원문과 번역을 함께 줌 (원문만 주면 '없음'이 잦고, 번역만 주면 뜻이 틀어진 번역에 끌려감)
    picked = select(question if search == question else f'{question}\n({search})',
                    [d for i, d in cand.items() if i not in fixed])
    by_doc = {d: i for i, d in cand.items()}
    s.update(search=search, fixed=list(fixed), vector=hits['ids'][0], keyword=[i for i, _, _ in kw],
             picked=[by_doc[d] for d in picked])
    show(f'[3] AI 고름 {len(picked)}개', [(by_doc[d], d) for d in picked])
    return list(fixed.values()) + picked


def drop_bad_parens(answer, context, language):
    """프롬프트로 막아도 남는 괄호 오류를 지움: '수정전(수정전)' 반복, '수정전(수종전)'처럼 문서에 없는 한글.
    '오른쪽(동쪽)'처럼 문서에 있는 말은 둠. 영어 답변의 '(集賢殿)'은 문서의 '집현전(集賢殿)'을 보고 '(집현전)'으로, 못 찾으면 지움"""
    if language != '한국어':
        hangul = {h: k for k, h in re.findall(r'([가-힣]+)\(([一-鿿]+)', context)}
        answer = re.sub(r' ?\(([一-鿿]+)\)', lambda m: f' ({hangul[m[1]]})' if m[1] in hangul else '', answer)
    answer = re.sub(r'([가-힣]+)\(\1\)', r'\1', answer)
    return re.sub(r'\(([가-힣 ]+)\)', lambda m: m[0] if m[1] in context else '', answer)


def answer_language(question):
    """답변 언어. 언어를 직접 요청하면 그대로('수정전을 영어로 설명해' → English), 아니면 질문 언어를 따름.
    한글 유무만 보면 '영어로' 질문이 한국어 괄호 규칙(한자)으로 답해짐"""
    if re.search(r'영어로|영문으로|\bin English\b', question, re.I):
        return 'English'
    if re.search(r'한국어로|한글로|\bin Korean\b', question, re.I):
        return '한국어'
    if re.search(r'태국어로|in Thai', question, re.I) or THAI.search(question):
        return 'ภาษาไทย'
    return '한국어' if re.search('[가-힣]', question) else 'English'


def answer(col, names, question, stages=None):
    """(답변, context, 사용한 문서 메타데이터). 메타데이터는 link_names·카드에 씀. stages는 retrieve와 같음"""
    s = stages if stages is not None else {}
    text, context = answer_docs(retrieve(col, names, question, s), question, answer_language(question))
    return text, context, col.get(ids=s['fixed'] + s['picked'])['metadatas']


def answer_docs(docs, question, language):
    """정해진 문서로만 답변 (검색 없음) → (답변, context). 일반 질문·길 묻기·코스 설명이 같은 규칙(SYSTEM)을 씀"""
    context = '\n\n'.join(docs)
    text = llm(SYSTEM.format(context=context, language=language), question, 2048)
    return drop_bad_parens(text, context, language), context


INTENT = """경복궁 관람객의 말을 분류하라.
- route: 어떤 건물·문의 위치나 가는 길을 묻는다. 예: "교태전 어떻게 가?", "경회루 어디야?", "Where is Gyeonghoeru?", "근정전까지 길 알려줘". to = <places> 의 id.
- amenity: 가까운 화장실(restroom), 벤치·앉을 곳(bench), 식수대·마실 물(water)을 찾는다. amenity = 그 종류.
- question: 그 밖의 모든 것 (건물 설명·역사·용어 등). 예: "근정전이 뭐야?", "교태전에는 누가 살았어?"
route 가 아니면 to 는 null, amenity 가 아니면 amenity 는 null.

<places>
{places}
</places>"""
AMENITY = {'restroom': ('화장실', 'restrooms', 'ห้องน้ำ'), 'bench': ('벤치', 'benches', 'ม้านั่ง'), 'water': ('식수대', 'drinking fountains', 'จุดน้ำดื่ม')}


def places(col, _cache={}):
    """{nameId: 메타데이터} — 길 묻기 목적지 후보 (DB 전각 문서의 nameId)"""
    if col.name not in _cache:
        _cache[col.name] = {m['nameId']: m for m in col.get(where={'type': 'building'})['metadatas'] if m.get('nameId')}
    return _cache[col.name]


def intent(col, question):
    """{kind: route|amenity|question, to: nameId|None, amenity: restroom|bench|water|None}. LLM 이 목록(enum) 안에서만 고름"""
    ps = places(col)
    listing = '\n'.join(f"{i}: {m['name']} / {m.get('name_en') or ''} / {m.get('name_th') or ''}" for i, m in ps.items())
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'to', 'amenity'], 'properties': {
        'kind': {'type': 'string', 'enum': ['route', 'amenity', 'question']},
        'to': {'anyOf': [{'type': 'string', 'enum': list(ps)}, {'type': 'null'}]},
        'amenity': {'anyOf': [{'type': 'string', 'enum': list(AMENITY)}, {'type': 'null'}]}}}
    return json.loads(llm(INTENT.format(places=listing), question, 100, schema=schema))


def reply(col, names, question):
    """(POST /api/ask 응답 {answer, language, action}, 넣은 문서 context). 길·편의시설은 어디로인지만 정하고, 실제 길은 폰(route.mjs)이 계산.
    길 묻기 답변 = 목적지 문서로 짧은 소개 + 카드 링크 (검색·고르기 없이 문서 고정)"""
    language = answer_language(question)
    it = intent(col, question)
    if it['kind'] == 'amenity' and it['amenity']:
        ko, en, th = AMENITY[it['amenity']]
        text = {'English': f'Showing the nearest {en} on the map.', 'ภาษาไทย': f'แสดง{th}ที่ใกล้ที่สุดบนแผนที่'}.get(
            language, f'가까운 {ko}을 지도에 표시합니다.' if ko == '화장실' else f'가까운 {ko}를 지도에 표시합니다.')
        return {'answer': text, 'language': language, 'action': {'type': 'amenity', 'category': it['amenity']}}, ''
    if it['kind'] == 'route' and it['to']:
        fixed = {}
        add_fixed(col, {'type': 'building', 'nameId': it['to']}, fixed)
        name = places(col)[it['to']]['name']
        # ponytail: "가는 길 + 뭐하는 곳" 섞인 질문도 짧은 소개만. 길게 원하면 일반 질문으로 다시 물으면 됨
        text, context = answer_docs(list(fixed.values()), f'{name}을(를) 2~3문장으로 짧게 소개하라: 무엇인지, 가서 무엇을 볼 수 있는지. '
                              '위치·방향·가는 길은 지도가 안내하므로 쓰지 않는다.', language)
        return {'answer': link_names(text, col.get(ids=list(fixed))['metadatas'], language), 'language': language,
                'action': {'type': 'route', 'to': it['to']}}, context
    text, context, metas = answer(col, names, question)
    return {'answer': link_names(text, metas, language), 'language': language, 'action': None}, context


def ask(col, names, question, debug=False):
    r, context = reply(col, names, question)
    print(r['answer'] + (f"\n[action] {json.dumps(r['action'], ensure_ascii=False)}" if r['action'] else ''))
    if debug:
        print('\n--- 검색된 청크 ---\n' + context)


def main():
    debug = '--debug' in sys.argv
    question = ' '.join(a for a in sys.argv[1:] if a != '--debug')
    col = Collection()
    names = name_keys(col)
    if question:
        return ask(col, names, question, debug)
    # ponytail: 질문마다 독립 검색·답변(대화 기록 없음). "거기는?" 같은 후속 질문이 필요하면 history 추가
    print('경복궁 해설 챗봇입니다. 빈 줄이나 exit 입력 시 종료.')
    while (question := input('\n질문> ').strip()) not in ('', 'exit', 'quit'):
        ask(col, names, question, debug)


if __name__ == '__main__':
    main()
