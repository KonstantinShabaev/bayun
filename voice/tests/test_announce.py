from bayun_voice.announce import Announcer


def test_announcer_queues_and_skips_resolved() -> None:
    announcer = Announcer("http://brain")
    announcer.feed(": ping")
    announcer.feed('data: {"type": "announcement", "id": "a1", "text": "Таймер сработал."}')
    announcer.feed(
        'data: {"type": "announcement", "id": "q1", "text": "Пушить?", "expects_reply": true,'
        ' "conversation_id": "code-q1"}'
    )
    announcer.feed('data: {"type": "resolved", "id": "q1"}')
    first = announcer.next()
    assert first is not None and first.text == "Таймер сработал."
    assert announcer.next() is None
    # После переподключения brain повторяет открытые вопросы: второй раз не говорим.
    announcer.feed('data: {"type": "announcement", "id": "a1", "text": "Таймер сработал."}')
    assert announcer.next() is None


def test_announcer_keeps_reply_context() -> None:
    announcer = Announcer("http://brain")
    announcer.feed(
        'data: {"type": "announcement", "id": "q2", "text": "Пушить?", "expects_reply": true,'
        ' "conversation_id": "code-q2"}'
    )
    item = announcer.next()
    assert item is not None and item.expects_reply and item.conversation_id == "code-q2"
