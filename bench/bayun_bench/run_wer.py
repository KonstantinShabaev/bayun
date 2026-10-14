"""WER/CER по датасету команд.

Вход: commands.csv (id, text, audio_path, speaker, noise) и CSV с гипотезами (id, hypothesis).
Распознавание аудио движками добавится в M0–M1; пока считаем метрики по готовым гипотезам.

    bayun-bench bench/commands.csv results/whisper.csv
"""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import jiwer


@dataclass(frozen=True)
class Score:
    n: int
    wer: float
    cer: float


def _prep(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def score(references: dict[str, str], hypotheses: dict[str, str]) -> Score:
    ids = sorted(references.keys() & hypotheses.keys())
    if not ids:
        raise ValueError("Нет общих id между эталоном и гипотезами")
    ref = [_prep(references[i]) for i in ids]
    hyp = [_prep(hypotheses[i]) for i in ids]
    return Score(n=len(ids), wer=jiwer.wer(ref, hyp), cer=jiwer.cer(ref, hyp))


def _read(path: Path, column: str) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as f:
        return {row["id"]: row[column] for row in csv.DictReader(f)}


def main() -> None:
    parser = argparse.ArgumentParser(prog="bayun-bench")
    parser.add_argument("commands", type=Path)
    parser.add_argument("hypotheses", type=Path)
    args = parser.parse_args()

    result = score(_read(args.commands, "text"), _read(args.hypotheses, "hypothesis"))
    print(f"команд: {result.n}  WER: {result.wer:.1%}  CER: {result.cer:.1%}")


if __name__ == "__main__":
    main()
