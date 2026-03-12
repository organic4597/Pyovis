#!/usr/bin/env python3
"""
Pyovis QnA Bot 실행 스크립트.

사용법:
    python run_qna.py [--host HOST] [--port PORT]

기본값:
    host: 0.0.0.0
    port: 8080

전제 조건:
    Brain 모델 서버가 port 8001에서 실행 중이어야 합니다.
    시작 방법: ./scripts/start_model.sh brain
"""

import argparse
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (어디서 실행하든 임포트 가능)
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pyovis QnA Bot 웹 서버")
    parser.add_argument(
        "--host", default="0.0.0.0", help="바인드 호스트 (기본: 0.0.0.0)"
    )
    parser.add_argument("--port", type=int, default=8080, help="포트 번호 (기본: 8080)")
    parser.add_argument("--reload", action="store_true", help="개발용 자동 리로드")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"\n{'=' * 48}")
    print("  Pyovis QnA Bot")
    print(f"  http://localhost:{args.port}")
    print(f"  LLM 서버: http://localhost:8001  (Brain 필요)")
    print(f"{'=' * 48}\n")

    uvicorn.run(
        "qna_bot.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
