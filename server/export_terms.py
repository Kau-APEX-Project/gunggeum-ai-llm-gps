"""Amazon Translate 사용자 용어집(Custom Terminology) CSV 만들기: python server/export_terms.py <저장 경로> (기본: ./terms.csv)
라이브 자막 번역(BE pipeline)이 전각·용어 이름을 카드와 같은 표기('Geunjeongjeon (Throne Hall)')로 옮기게 함
→ cards.link_names 가 번역문에서 이름을 찾아 카드를 닮. 카드와 같은 DB 메타에서 뽑아야 글자가 맞음.
열: ko,en,th (한국어 이름 → name_en·name_th). AWS 콘솔이나 `aws translate import-terminology` 로 등록 후
BE .env 의 TRANSLATE_TERMINOLOGY 에 그 이름을 넣음"""
import csv
import re
import sys

from cards import card_id, unique


def rows(metas):
    """[(ko, en, th)]. '삼도 / 어도' 는 한국어 이름마다 한 줄, 괄호(한자)는 뗌.
    같은 한국어 이름이 여러 카드면 cards.unique 규칙(전각 우선, 용어끼리면 뺌: 사신 四神·使臣).
    1글자 이름(궐·칸)은 문장 속 아무 글자에나 걸려서 뺌"""
    cards, names = {}, {}
    for m in metas:
        cards.setdefault(card_id(m), m)
    for cid, m in cards.items():
        for p in m['name'].split('/'):
            if len(p := re.sub(r'\(.*?\)', '', p).strip()) > 1:
                names.setdefault(p, set()).add(cid)
    return [(ko, cards[cid].get('name_en') or '', cards[cid].get('name_th') or '')
            for ko, cid in sorted(unique(names).items())]


assert rows([{'type': 'term', 'no': 70, 'name': '사신(四神)', 'name_en': 'Sasin (Four Guardians)'},
             {'type': 'term', 'no': 122, 'name': '사신(使臣)', 'name_en': 'Sasin (envoy)'},
             {'type': 'term', 'no': 67, 'name': '꽃담', 'name_en': 'Kkotdam (term)'},
             {'type': 'building', 'serial': 171, 'name': '꽃담', 'name_en': 'Kkotdam (Flower Wall)'},
             {'type': 'term', 'no': 5, 'name': '삼도 / 어도', 'name_en': 'Samdo / Eodo (royal path)'},
             {'type': 'term', 'no': 6, 'name': '궐', 'name_en': 'Gwol'}]) == [
    ('꽃담', 'Kkotdam (Flower Wall)', ''), ('삼도', 'Samdo / Eodo (royal path)', ''), ('어도', 'Samdo / Eodo (royal path)', '')]


def main():
    from pg import Collection
    out = rows(Collection().get()['metadatas'])
    path = sys.argv[1] if len(sys.argv) > 1 else 'terms.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        csv.writer(f).writerows([('ko', 'en', 'th'), *out])
    print(path, len(out), 'terms, missing th', sum(not th for *_, th in out))


if __name__ == '__main__':
    main()
