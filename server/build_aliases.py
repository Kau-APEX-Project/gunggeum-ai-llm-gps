"""카드별 영어·태국어 별칭 만들기 (오프라인 1회, 서비스 호출 아님): python server/build_aliases.py <저장 경로> (기본: ./aliases_raw.json)
외국어 답변 LLM 은 용어를 DB 이름 대신 자기 말로 씀('enthronements', 'ห้องนอน') → 매칭 쪽(cards.link)이 그 말을 알게 함.
근거는 DB 만: 카드마다 그 한국어 이름이 나오는 문서 문장 최대 5개 + DB 공식 표기·번역. 평가 질문·답변은 안 봄.
모델은 서비스와 같은 GPT-6 Luna(ask.llm) — 같은 모델이 쓰는 말버릇을 뽑으려고.
원본(aliases_raw.json)은 검수 근거라 레포에 안 넣음. 사람이 검수한 결과만 server/aliases.json 으로 커밋 (서버가 읽음)"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ask
from cards import card_id
from pg import Collection

PROMPT = """너는 경복궁 안내 시스템의 다국어 용어 사전을 만든다. 각 카드의 [한국어 이름·DB 공식 표기]와 실제 DB 문서의 [한국어 예문]을 보고,
안내 답변 LLM 이 이 예문 내용을 영어와 태국어로 자연스럽게 설명할 때 이 카드의 대상을 가리키려고 쓸 표현을 뽑아라.

규칙:
1. 공식 표기뿐 아니라 자연스럽게 풀어 쓴 표현, 짧게 부르는 말, 흔한 번역어를 넣는다. 영어는 단수형만 쓴다(복수는 코드가 처리).
   예: 즉위식 → enthronement, enthronement ceremony, coronation / พิธีขึ้นครองราชย์, พิธีราชาภิเษก
2. 묶음 카드(예: "국보 / 보물", "창건 / 중건 / 재건 / 복원 / 소실")는 개념마다 따로 넣는다 (National Treasure, Treasure / reconstruction, restoration, destroyed by fire …).
3. 두 등급으로 나눈다.
   - safe: 다른 대상과 헷갈릴 일이 없어 답변에 나오면 이 카드라고 봐도 되는 표현 (royal bedchamber, Imjin War, Japanese invasions of Korea)
   - context: 혼자서는 흔하지만, 이 카드의 한국어 이름이 문서에 나온 답변에서는 이 카드를 가리키는 표현 (bedchamber, sleeping quarters, ห้องนอน)
4. 누구에게나 쓰이는 말(hall, palace, king, queen, building, gate, room, ceremony, ตำหนัก, พระราชวัง, ห้อง)은 혼자 넣지 않는다.
5. 카드당 언어별로 safe·context 합쳐 최대 8개. 이 카드와 뜻이 다른 표현은 넣지 않는다. 확실하지 않으면 넣지 않는다."""
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['cards'], 'properties': {'cards': {'type': 'array', 'items': {
    'type': 'object', 'additionalProperties': False, 'required': ['id', 'en_safe', 'en_context', 'th_safe', 'th_context'],
    'properties': {'id': {'type': 'string'}, **{k: {'type': 'array', 'items': {'type': 'string'}}
                                                for k in ('en_safe', 'en_context', 'th_safe', 'th_context')}}}}}}


def cards_with_examples(col):
    """[{id, name, name_en, name_th, examples}] — 예문은 그 이름(조각)이 나오는 문서 문장, 자기 용어집 문서 머리는 뺌"""
    got = col.get()
    bodies = [re.sub(r'^\[[^\]]*\]\s*', '', d) for d in got['documents']]
    sentences = [s.strip() for b in bodies for s in re.split(r'(?<=다\.)\s+|(?<=\.)\s+(?=[가-힣\[])', b) if s.strip()]
    cards = {}
    for m in got['metadatas']:
        cards.setdefault(card_id(m), m)
    out = []
    for cid, m in cards.items():
        parts = [re.sub(r'\(.*?\)', '', p).strip() for p in m['name'].split('/')]
        parts = [p for p in parts if len(p) > 1]
        ex = [s for s in sentences if any(p in s for p in parts) and not s.startswith(m['name'])][:5]
        out.append({'id': cid, 'name': m['name'], 'name_en': m.get('name_en') or '', 'name_th': m.get('name_th') or '',
                    'examples': [e[:220] for e in ex]})
    return out


def ask_batch(batch):
    listing = '\n\n'.join(f"[{c['id']}] {c['name']} | 영어 공식: {c['name_en']} | 태국어 공식: {c['name_th']}\n"
                          + '\n'.join(f'- {e}' for e in c['examples']) for c in batch)
    return json.loads(ask.llm(PROMPT, listing, 4000, schema=SCHEMA))['cards']


def main():
    cards = cards_with_examples(Collection())
    batches = [cards[i:i + 10] for i in range(0, len(cards), 10)]
    with ThreadPoolExecutor(6) as ex:
        got = [c for b in ex.map(ask_batch, batches) for c in b]
    by = {c['id']: c for c in got}
    raw = {c['id']: {'name': c['name'], 'name_en': c['name_en'], 'name_th': c['name_th'], 'examples': c['examples'],
                     **{k: by.get(c['id'], {}).get(k, []) for k in ('en_safe', 'en_context', 'th_safe', 'th_context')}}
           for c in cards}
    Path(sys.argv[1] if len(sys.argv) > 1 else 'aliases_raw.json').write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(raw), 'cards,', sum(len(v[k]) for v in raw.values() for k in ('en_safe', 'en_context', 'th_safe', 'th_context')),
          'aliases, missing', [c['id'] for c in cards if c['id'] not in by], dict(ask.usage))


if __name__ == '__main__':
    main()
