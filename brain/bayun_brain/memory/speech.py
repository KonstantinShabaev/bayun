"""Числа и сроки словами для озвучки: «через двадцать одну минуту»."""

_UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEENS = [
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
    "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
]  # fmt: skip
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят",
         "восемьдесят", "девяносто"]  # fmt: skip
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот",
             "восемьсот", "девятьсот"]  # fmt: skip


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def number_words(n: int, feminine: bool = False) -> str:
    """0–999 словами, в винительном падеже: «одну» для минуты и секунды."""
    if n == 0:
        return "ноль"
    if n >= 1000:
        return str(n)
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        words.append(_TENS[rest // 10])
        unit = _UNITS[rest % 10]
        if feminine and rest % 10 in (1, 2):
            unit = "одну" if rest % 10 == 1 else "две"
        words.append(unit)
    return " ".join(w for w in words if w)


def _part(n: int, one: str, few: str, many: str, feminine: bool) -> str:
    if n == 1:
        return one
    return f"{number_words(n, feminine)} {plural(n, one, few, many)}"


def duration_words(seconds: int) -> str:
    """90 → «одну минуту тридцать секунд», 3600 → «час», 1800 → «тридцать минут»."""
    seconds = max(0, int(seconds))
    if seconds >= 24 * 3600:
        days, rest = divmod(seconds, 24 * 3600)
        text = _part(days, "день", "дня", "дней", False)
        hours = round(rest / 3600)
        return f"{text} {_part(hours, 'час', 'часа', 'часов', False)}" if hours else text
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(_part(hours, "час", "часа", "часов", False))
    if minutes:
        parts.append(_part(minutes, "минуту", "минуты", "минут", True))
    # Секунды важны только у коротких сроков.
    if secs and not hours and minutes < 10:
        parts.append(_part(secs, "секунду", "секунды", "секунд", True))
    if not parts:
        return "секунду"
    if parts[0] in ("час", "минуту", "секунду") and len(parts) > 1:
        parts[0] = "один " + parts[0] if parts[0] == "час" else "одну " + parts[0]
    return " ".join(parts)


def time_words(hours: int, minutes: int) -> str:
    """Время коротко, как говорят: «семь тридцать», «семь ноль пять», «семь часов»."""
    hour = "один" if hours == 1 else number_words(hours) if hours else "ноль"
    if minutes == 0:
        return f"{hour} {plural(hours, 'час', 'часа', 'часов')}"
    tail = number_words(minutes) if minutes >= 10 else f"ноль {number_words(minutes)}"
    return f"{hour} {tail}"


def clock_words(hours: int, minutes: int) -> str:
    """Время для озвучки: «семь часов пять минут»."""
    count = number_words(hours) if hours != 1 else "один"
    text = f"{count} {plural(hours, 'час', 'часа', 'часов')}"
    if hours == 0:
        text = "ноль часов"
    if minutes:
        text += f" {number_words(minutes, True)} {plural(minutes, 'минута', 'минуты', 'минут')}"
    # Именительный падеж: «двадцать одна минута».
    return text.replace("одну минута", "одна минута")
