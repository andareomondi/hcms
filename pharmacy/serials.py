import re

SERIAL_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/._]{3,63}$")


class SerialError(ValueError):
    """A serial that can't be accepted. The message is shown to staff."""


def normalise(raw):
    value = (raw or "").strip().upper()
    if not SERIAL_RE.match(value):
        shown = (raw or "").strip()[:30]
        raise SerialError(f"'{shown}' is not a valid serial. Use 4 to 64 letters, digits, or - / . _")
    return value


def parse_list(text):
    """One serial per line, as a barcode scanner types them. Duplicates in the same list are refused."""
    serials = [normalise(line) for line in (text or "").splitlines() if line.strip()]
    if len(set(serials)) != len(serials):
        raise SerialError("The same serial appears more than once in this list.")
    return serials
