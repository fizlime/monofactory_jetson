import re


def positive_integer(value, maximum, label):
    """Manual distance must be explicit; never truncate or fall back on zero."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label}: 1~{maximum} 정수를 입력하세요.")
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,10}", text) or not 1 <= int(text) <= maximum:
        raise ValueError(f"{label}: 1~{maximum} 정수를 입력하세요.")
    return int(text)
