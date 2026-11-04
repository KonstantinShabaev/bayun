"""Настоящая сессия Claude Code через Agent SDK (optional extra `code`).

SDK запускает CLI `claude` и берёт его вход на этой машине: токенов в конфиге Баюна нет.
"""

import logging
from typing import Any

from ..config import CodeConfig
from .tasks import Question, SessionRequest, SessionResult

_LOGGER = logging.getLogger(__name__)

# То, что запрещено всегда, отрезаем ещё и правилами CLI: на случай, если колбэк
# почему-то не вызовется (например, правило allow в настройках проекта).
DISALLOWED = [
    "Bash(sudo:*)",
    "Bash(systemctl:*)",
    "Bash(git push --force:*)",
    "Bash(git push -f:*)",
    "Bash(gh pr merge:*)",
]


class SdkSession:
    def __init__(self, config: CodeConfig) -> None:
        # Импорт при старте: без extra `code` brain падает сразу, а не на первой задаче.
        import claude_agent_sdk  # noqa: F401

        self.config = config

    async def __call__(self, request: SessionRequest) -> SessionResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            PermissionResultAllow,
            PermissionResultDeny,
            ResultMessage,
            TextBlock,
            ToolPermissionContext,
            ToolUseBlock,
        )

        async def can_use_tool(
            tool: str, data: dict[str, Any], context: ToolPermissionContext
        ) -> PermissionResultAllow | PermissionResultDeny:
            if tool == "AskUserQuestion":
                return await ask(data)
            decision = await request.can_use_tool(tool, data)
            if decision.verdict == "allow":
                return PermissionResultAllow(updated_input=data)
            return PermissionResultDeny(message=decision.reason or "Нельзя.")

        async def ask(data: dict[str, Any]) -> PermissionResultAllow:
            raw = data.get("questions") or []
            questions = [
                Question(
                    str(q.get("question", "")),
                    [str(o.get("label", "")) for o in q.get("options") or []],
                )
                for q in raw
            ]
            answers = await request.ask_user(questions)
            return PermissionResultAllow(
                updated_input={
                    **data,
                    "answers": {q.question: a for q, a in zip(questions, answers, strict=True)},
                }
            )

        options = ClaudeAgentOptions(
            cwd=str(request.workdir),
            resume=request.resume,
            model=self.config.model,
            max_turns=self.config.max_turns,
            max_budget_usd=self.config.max_budget_usd,
            permission_mode="default",
            can_use_tool=can_use_tool,
            disallowed_tools=DISALLOWED,
            # CLAUDE.md проекта, но не пользовательские allow-правила.
            setting_sources=["project"],
            system_prompt={"type": "preset", "preset": "claude_code"},
        )
        text = ""
        async with ClaudeSDKClient(options=options) as client:
            await client.query(request.prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text = block.text
                        elif isinstance(block, ToolUseBlock):
                            request.on_progress(_action(block.name, block.input))
                elif isinstance(message, ResultMessage):
                    _LOGGER.info(
                        "Claude Code: %s, ходов %d, $%s",
                        message.subtype, message.num_turns, message.total_cost_usd,
                    )  # fmt: skip
                    return SessionResult(
                        message.session_id, message.result or text, is_error=message.is_error
                    )
        return SessionResult(None, text or "Клод ничего не ответил.", is_error=True)


def _action(tool: str, data: dict[str, Any]) -> str:
    """Для «что там с Клодом?»: коротко, что он сейчас делает."""
    if tool == "Bash":
        return f"запускает {str(data.get('command', ''))[:60]}"
    if tool in ("Edit", "MultiEdit", "Write"):
        return f"правит {str(data.get('file_path', '')).rsplit('/', 1)[-1]}"
    if tool in ("Read", "Grep", "Glob"):
        return "читает код"
    return tool
