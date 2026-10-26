"""서버 공통: deploy/.env 로드 (양식 deploy/.env.example). 운영에서 환경변수로 넣으면 .env 가 없어도 됨"""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
except ImportError:
    pass
