"""Postgres(pgvector) 문서 저장소. Chroma 컬렉션에서 쓰던 get·query 만 같은 모양으로 흉내 냄
(ask·live·eval 의 검색 코드를 그대로 두려고). 접속: .env 의 DATABASE_URL.

테이블 하나: docs(pos 넣은 순서, id, document 본문, meta 메타데이터, embedding Titan 1024차원)
"""
import os

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from bedrock import embedder


def where_sql(where=None, where_document=None):
    """{'type': 'term'} → meta->>'type' = 'term',  {'$contains': x} → 본문에 x 포함"""
    conds, args = [], []
    for k, v in (where or {}).items():
        conds.append('meta->>%s = %s')
        args += [k, str(v)]
    if where_document:
        conds.append('strpos(document, %s) > 0')
        args.append(where_document['$contains'])
    return (' where ' + ' and '.join(conds) if conds else ''), args


class Collection:
    name = 'gyeongbokgung'  # keyword_index 캐시 키

    def __init__(self):
        self.db = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True)
        self.db.execute('create extension if not exists vector')
        register_vector(self.db)

    def rebuild(self, ids, documents, metadatas):
        """테이블을 지우고 새로 넣음 (build_db.py). 지운 문단이 남지 않게 전부 다시 만듦"""
        vecs = embedder(documents)
        self.db.execute('drop table if exists docs')
        self.db.execute('create table docs (pos int, id text primary key, document text, meta jsonb, '
                        f'embedding vector({len(vecs[0])}))')
        # ponytail: 인덱스 없이 전체 스캔(585개). 수만 개를 넘으면 hnsw 인덱스
        with self.db.cursor() as cur:
            cur.executemany('insert into docs values (%s, %s, %s, %s, %s)',
                            [(n, i, d, Jsonb(m), np.array(v)) for n, (i, d, m, v)
                             in enumerate(zip(ids, documents, metadatas, vecs))])

    def count(self):
        return self.db.execute('select count(*) from docs').fetchone()[0]

    def get(self, ids=None, where=None, include=('documents', 'metadatas')):
        sql, args = where_sql(where)
        if ids is not None:
            sql += (' and' if sql else ' where') + ' id = any(%s)'
            args.append(list(ids))
        rows = self.db.execute(f'select id, document, meta from docs{sql} order by pos', args).fetchall()
        return {'ids': [r[0] for r in rows], 'documents': [r[1] for r in rows], 'metadatas': [r[2] for r in rows]}

    def query(self, query_texts, n_results=10, where=None, where_document=None):
        """벡터 거리(L2) 가까운 순. Chroma 처럼 질문마다 한 줄씩: {'ids': [[...]], 'documents': [[...]]}"""
        sql, args = where_sql(where, where_document)
        out = {'ids': [], 'documents': [], 'metadatas': []}
        for v in embedder(query_texts):
            rows = self.db.execute(f'select id, document, meta from docs{sql} order by embedding <-> %s limit %s',
                                   args + [np.array(v), n_results]).fetchall()
            for k, c in enumerate(out):
                out[c].append([r[k] for r in rows])
        return out
