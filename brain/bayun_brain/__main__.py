"""Запуск: python -m bayun_brain --config config.yaml"""

import argparse
import logging
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_config
from .ha import HomeAssistant
from .llm import ChatModel, LiteLlmChat
from .video import Video


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
    token = config.home_assistant.resolved_token()
    home = HomeAssistant(config.home_assistant.url, token) if token else None
    video = Video(config.video.agent_url) if config.video.agent_url else None
    uvicorn.run(create_app(config, chat, home, video), host=args.host, port=args.port)


if __name__ == "__main__":
    run()
