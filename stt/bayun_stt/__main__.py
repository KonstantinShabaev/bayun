"""Запуск Wyoming-сервера: python -m bayun_stt --uri tcp://0.0.0.0:10300"""

import argparse
import asyncio
import logging
from functools import partial

from wyoming.server import AsyncServer

from .asr import load_engine
from .handler import SttEventHandler


async def main() -> None:
    parser = argparse.ArgumentParser(prog="bayun-stt")
    parser.add_argument("--uri", default="tcp://0.0.0.0:10300")
    parser.add_argument("--engine", default="dummy", help="движок ASR")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    engine = load_engine(args.engine)
    server = AsyncServer.from_uri(args.uri)
    logging.getLogger(__name__).info("bayun-stt слушает %s, движок %s", args.uri, engine.name)
    await server.run(partial(SttEventHandler, engine))


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
