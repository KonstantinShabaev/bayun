"""Запуск: python -m bayun_brain --config config.yaml"""

import argparse
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_config


def run() -> None:
    parser = argparse.ArgumentParser(prog="bayun-brain")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    app = create_app(load_config(args.config))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    run()
