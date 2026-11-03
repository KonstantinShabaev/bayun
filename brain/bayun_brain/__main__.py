"""Запуск: python -m bayun_brain --config config.yaml"""

import argparse
import logging
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_config
from .llm import ChatModel, LiteLlmChat


def run() -> None:
    parser = argparse.ArgumentParser(prog="bayun-brain")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-llm", action="store_true", help="заглушка вместо LLM")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    config = load_config(args.config)
    chat: ChatModel | None = None if args.no_llm else LiteLlmChat(config)
    uvicorn.run(create_app(config, chat), host=args.host, port=args.port)


if __name__ == "__main__":
    run()
