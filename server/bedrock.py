"""AWS Bedrock Titan 임베딩 (DB 저장과 질문 검색에 같은 모델을 씀). 자격 증명은 .env의 AWS_* 값."""
import json
import os

import boto3
import config  # noqa: F401  deploy/.env 로드

REGION = os.environ.get('AWS_REGION', 'ap-northeast-2')
client = boto3.client('bedrock-runtime', region_name=REGION)


def embedder(texts):
    """문장 목록 → 벡터 목록 (Titan v2, 1024차원, 정규화됨)"""
    return [json.loads(client.invoke_model(modelId='amazon.titan-embed-text-v2:0', body=json.dumps({'inputText': t}))
                       ['body'].read())['embedding'] for t in texts]
