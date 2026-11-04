"""Права Claude Code, запущенного голосом: разрешить, спросить голосом или запретить.

Решение принимается до выполнения каждого инструмента (колбэк `can_use_tool`).
Правила — в docs/claude-code-voice.md, раздел «Права». Непонятное — спрашиваем.
"""

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Verdict = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    # Для «ask» — что именно спросить голосом, для «deny» — причина для Claude Code.
    reason: str = ""


ALLOW = Decision("allow")

# Инструменты без побочных эффектов вне сессии.
_HARMLESS_TOOLS = {"TodoWrite", "Task", "Agent", "ExitPlanMode", "BashOutput", "KillShell"}
_READ_TOOLS = {"Read", "Glob", "Grep", "LS"}
_WRITE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
_NETWORK_TOOLS = {"WebFetch": "открыть страницу в интернете", "WebSearch": "поискать в интернете"}

# Секреты: сюда нельзя ни читать, ни писать, ни упоминать в командах.
_SECRET = re.compile(
    r"\.ssh\b|\.config/bayun|\.claude/\.credentials|\.netrc|(^|[\s/=\"'])/etc/|(^|[\s/\"'])\.env\b"
)

# Команды, которые можно без вопроса (по первым словам).
_SAFE_COMMANDS = (
    ("uv", "sync"),
    ("uv", "lock"),
    ("uv", "run", "ruff"),
    ("uv", "run", "mypy"),
    ("uv", "run", "pytest"),
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "show"),
    ("git", "add"),
    ("git", "commit"),
    ("git", "branch"),
    ("git", "switch", "-c"),
    ("git", "checkout", "-b"),
    ("git", "fetch"),
    ("git", "rev-parse"),
    ("ls",),
    ("cat",),
    ("head",),
    ("tail",),
    ("wc",),
    ("grep",),
    ("rg",),
    ("find",),
    ("pwd",),
    ("echo",),
    ("sed", "-n"),
)

# Запрещено всегда, в любом месте команды.
_FORBIDDEN_COMMANDS = {
    "sudo",
    "su",
    "systemctl",
    "loginctl",
    "shutdown",
    "reboot",
    "pkill",
    "killall",
    "pactl",
    "wpctl",
    "tv-open",
    "dd",
    "mkfs",
}

_SPLIT = re.compile(r"&&|\|\||;|\n|\|")


def decide(tool: str, tool_input: dict[str, Any], workdir: Path, branch_prefix: str) -> Decision:
    if tool in _HARMLESS_TOOLS:
        return ALLOW
    if tool in _READ_TOOLS or tool in _WRITE_TOOLS:
        return _decide_path(tool, tool_input, workdir)
    if tool in _NETWORK_TOOLS:
        target = tool_input.get("url") or tool_input.get("query") or ""
        return Decision("ask", f"{_NETWORK_TOOLS[tool]}: {target}".strip(": "))
    if tool == "Bash":
        return decide_command(str(tool_input.get("command", "")), workdir, branch_prefix)
    return Decision("ask", f"использовать инструмент {tool}")


def _decide_path(tool: str, tool_input: dict[str, Any], workdir: Path) -> Decision:
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if not raw:
        return ALLOW  # Glob и Grep без пути ищут в рабочей папке.
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = workdir / path
    if _mentions_secret(str(path)):
        return Decision("deny", "Это файл с секретами, его трогать нельзя.")
    if _inside(path, workdir):
        return ALLOW
    if tool in _WRITE_TOOLS:
        return Decision("deny", f"Писать можно только внутри {workdir}.")
    return Decision("ask", f"прочитать файл вне проекта: {path}")


def decide_command(command: str, workdir: Path, branch_prefix: str) -> Decision:
    """Команда Bash: решение по самой опасной её части."""
    if not command.strip():
        return ALLOW
    if _mentions_secret(command):
        return Decision("deny", "Команда трогает секреты.")
    if re.search(r"\|\s*(ba|z)?sh\b", command) or "$(" in command or "`" in command:
        return Decision("deny", "Подстановки и запуск скачанного через sh запрещены.")
    worst = ALLOW
    for part in _SPLIT.split(command):
        decision = _decide_simple(part.strip(), workdir, branch_prefix)
        if decision.verdict == "deny":
            return decision
        if decision.verdict == "ask" and worst.verdict == "allow":
            worst = decision
    return worst


def _decide_simple(part: str, workdir: Path, branch_prefix: str) -> Decision:
    if not part:
        return ALLOW
    try:
        words = shlex.split(part)
    except ValueError:
        return Decision("ask", f"выполнить команду: {part}")
    # Переменные окружения перед командой: FOO=1 uv run pytest.
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        words.pop(0)
    if not words:
        return ALLOW
    if any(Path(word).name in _FORBIDDEN_COMMANDS for word in words[:2]):
        return Decision("deny", f"Команда {words[0]} запрещена при работе голосом.")
    name = words[0]
    if name == "cd":
        target = Path(words[1]).expanduser() if len(words) > 1 else Path.home()
        if _inside(target if target.is_absolute() else workdir / target, workdir):
            return ALLOW
        return Decision("deny", f"Работать можно только внутри {workdir}.")
    if name == "rm":
        return _decide_rm(words, workdir)
    if name == "git" and len(words) > 1 and words[1] == "push":
        return _decide_push(words[2:], branch_prefix)
    if name == "git" and len(words) > 1 and words[1] in ("reset", "clean", "rebase"):
        return Decision("ask", f"выполнить git {words[1]}, это может стереть изменения")
    if name == "gh":
        return _decide_gh(words[1:])
    if name == "uv" and len(words) > 1 and words[1] in ("add", "remove"):
        return Decision("ask", f"изменить зависимости: {' '.join(words[2:])}")
    if name in ("curl", "wget"):
        return Decision("ask", f"скачать из интернета: {part}")
    for safe in _SAFE_COMMANDS:
        if tuple(words[: len(safe)]) == safe:
            return ALLOW
    return Decision("ask", f"выполнить команду: {part}")


def _decide_rm(words: list[str], workdir: Path) -> Decision:
    targets = [w for w in words[1:] if not w.startswith("-")]
    if not targets:
        return ALLOW
    for target in targets:
        path = Path(target).expanduser()
        path = path if path.is_absolute() else workdir / path
        if not _inside(path, workdir) or path.resolve() == workdir.resolve():
            return Decision("deny", f"Удалять можно только внутри {workdir}.")
    if ".git" in " ".join(targets):
        return Decision("deny", "Папку .git удалять нельзя.")
    return Decision("ask", f"удалить {' '.join(targets)}")


def _decide_push(args: list[str], branch_prefix: str) -> Decision:
    flags = [a for a in args if a.startswith("-")]
    dangerous = {"-f", "--force", "--force-with-lease", "--mirror", "--delete", "-d", "--all"}
    if dangerous & set(flags) or any(a.startswith("--force") for a in flags):
        return Decision("deny", "Принудительный push и удаление веток запрещены.")
    positional = [a for a in args if not a.startswith("-")]
    refspecs = positional[1:]  # первый — remote
    if not refspecs:
        return Decision(
            "deny",
            f"Укажи ветку явно: git push -u origin {branch_prefix}<имя>.",
        )
    for refspec in refspecs:
        target = refspec.split(":")[-1].removeprefix("+").removeprefix("refs/heads/")
        if refspec.startswith("+") or not target.startswith(branch_prefix):
            return Decision("deny", f"Push можно только в ветки {branch_prefix}*.")
    return Decision("ask", f"запушить ветку {' '.join(refspecs)} в гитхаб")


def _decide_gh(args: list[str]) -> Decision:
    head = args[:2]
    if head in (["pr", "view"], ["pr", "list"], ["pr", "diff"], ["pr", "checks"], ["run", "list"]):
        return ALLOW
    if head == ["pr", "create"]:
        return Decision("ask", "открыть пулл-реквест")
    if (
        head
        and head[0] in ("pr", "repo", "api", "release", "secret", "auth")
        and (
            head[-1] in ("merge", "delete", "close", "edit", "set", "login", "logout")
            or head[0] in ("api", "secret", "auth")
        )
    ):
        return Decision("deny", "Мержить, удалять и менять настройки репо голосом нельзя.")
    return Decision("ask", f"выполнить gh {' '.join(args)}")


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _mentions_secret(text: str) -> bool:
    return _SECRET.search(text) is not None
