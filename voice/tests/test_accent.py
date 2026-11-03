from bayun_voice.accent import zh_accent, zh_max_accent, zh_strong_accent

# «здрасьте» в фонемах espeak
HELLO = ["z", "d", "r", "ˈ", "ɑ", "s", "t", "ʲ", "e", "!"]


def test_zh_accent_keeps_russian_but_swaps_r() -> None:
    assert "".join(zh_accent(HELLO)) == "sdlˈɑstʲe!"


def test_zh_max_accent_devoices_and_breaks_clusters() -> None:
    assert "".join(zh_max_accent(HELLO)) == "sətəlˈɑsətʲe!"


def test_zh_max_accent_keeps_final_n_and_softness() -> None:
    assert "".join(zh_max_accent(["d", "ʲ", "e", "n", " "])) == "tʲen "


def test_zh_strong_accent_adds_final_vowel() -> None:
    # «спорь со» → «сполʲə со»
    phonemes = ["s", "p", "ˈ", "o", "r", "ʲ", " ", "s", "ə"]
    assert "".join(zh_strong_accent(phonemes)) == "spˈolʲə sə"


def test_text_accents_for_silero() -> None:
    from bayun_voice.accent import zh_strong_text_accent, zh_text_accent

    assert zh_text_accent("Привет, Жора!") == "Пливет, Шола!"
    assert zh_strong_text_accent("Привет, мир") == "Пливетэ, милэ"


def test_pronounce_keeps_endings_and_case() -> None:
    from bayun_voice.tts import Pronounced

    class Echo:
        rate = 16000
        audio_tags = False

        def synthesize(self, text: str) -> bytes:
            return text.encode()

    tts = Pronounced(Echo(), {"Волтер": "Уо+лтэр"})
    assert tts.fix("Я Волтер. Спроси волтера.") == "Я Уо+лтэр. Спроси Уо+лтэра."


def test_yandex_sends_form_and_returns_pcm() -> None:
    import httpx
    from bayun_voice.tts import YandexTts

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\x01\x02")

    tts = YandexTts("key", "filipp", "good", 1.0)
    tts._client = httpx.Client(transport=httpx.MockTransport(handler), headers=tts._client.headers)
    assert tts.synthesize("[laughs] Привет") == b"\x01\x02"
    body = seen[0].content.decode()
    assert "emotion=good" in body and "format=lpcm" in body and "laughs" not in body
    assert seen[0].headers["Authorization"] == "Api-Key key"
