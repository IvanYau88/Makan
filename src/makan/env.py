"""Load a `.env` file into the environment, so the app starts without a shell script.

The file is `NAME=value` lines. Blank lines and `#` comments are skipped, `export NAME=value` is
accepted, and a value may be wrapped in single or double quotes. Line endings may be LF or CRLF,
and the file may be UTF-8 (with or without a byte order mark) or UTF-16, which Windows PowerShell
writes by default. A variable that is already set, even to an empty value, is never overridden.
"""

from __future__ import annotations

import codecs
import os
import re
from collections.abc import MutableMapping
from pathlib import Path

from makan.config import ConfigError

ENV_FILE_VARIABLE = "MAKAN_ENV_FILE"
DEFAULT_ENV_FILE = ".env"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_dotenv(
    path: Path | str | None = None, environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """Set the variables the file defines that `environ` lacks, and return their names.

    With no `path`, read the file named by `MAKAN_ENV_FILE`, or else `.env` in the current
    directory. A missing default file is fine, but a missing file that was asked for is an error.
    """
    environ = os.environ if environ is None else environ
    explicit = path or environ.get(ENV_FILE_VARIABLE, "").strip() or None
    file = Path(explicit or DEFAULT_ENV_FILE)
    if not file.is_file():
        if explicit:
            raise ConfigError(f"env file {str(file)!r} does not exist")
        return []
    loaded: list[str] = []
    for name, value in parse_dotenv(file.read_bytes()).items():
        if name not in environ:
            environ[name] = value
            loaded.append(name)
    return loaded


def parse_dotenv(raw: bytes) -> dict[str, str]:
    """Parse the file. The last definition of a name wins, and unreadable lines are skipped."""
    variables: dict[str, str] = {}
    for line in _decode(raw).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export") and line[6:7].isspace():
            line = line[6:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if separator and _NAME.fullmatch(name):
            variables[name] = _value(value.strip())
    return variables


def _decode(raw: bytes) -> str:
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def _value(text: str) -> str:
    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        if end != -1:
            return text[1:end]  # anything after the closing quote is ignored
    # Unquoted: a `#` that starts a comment follows whitespace, so a `#` inside a value stays.
    return re.split(r"\s+#", text, maxsplit=1)[0].rstrip()
