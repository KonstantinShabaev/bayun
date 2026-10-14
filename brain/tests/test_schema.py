import sqlite3
from importlib.resources import files


def test_schema_applies_and_fts_finds_fact() -> None:
    schema = files("bayun_brain.memory").joinpath("schema.sql").read_text(encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.executescript(schema)
    db.execute(
        "INSERT INTO facts (kind, about, text, source) VALUES (?, ?, ?, ?)",
        ("fact", "masha", "Маша спит во второй спальне", "voice"),
    )
    rows = db.execute("SELECT rowid FROM facts_fts WHERE facts_fts MATCH 'спальне'").fetchall()
    assert rows == [(1,)]
