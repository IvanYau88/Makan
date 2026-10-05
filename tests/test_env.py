"""Loading `.env` into the environment: Windows line endings, quoting, and never overriding."""

from pathlib import Path

import pytest

from makan.config import ConfigError
from makan.env import load_dotenv, parse_dotenv


def write(tmp_path: Path, content: str | bytes, name: str = ".env") -> Path:
    file = tmp_path / name
    file.write_bytes(content.encode() if isinstance(content, str) else content)
    return file


def test_a_crlf_file_loads_without_carriage_returns_in_the_values(tmp_path: Path) -> None:
    file = write(tmp_path, "MAKAN_MODEL=some/model\r\nOPENROUTER_API_KEY=sk-test\r\n\r\n# note\r\n")
    environ: dict[str, str] = {}
    assert load_dotenv(file, environ) == ["MAKAN_MODEL", "OPENROUTER_API_KEY"]
    assert environ == {"MAKAN_MODEL": "some/model", "OPENROUTER_API_KEY": "sk-test"}


def test_variables_already_in_the_environment_are_never_overridden(tmp_path: Path) -> None:
    file = write(tmp_path, "A=file\nB=file\nC=file\n")
    environ = {"A": "real", "B": ""}  # an empty value is still set
    assert load_dotenv(file, environ) == ["C"]
    assert environ == {"A": "real", "B": "", "C": "file"}


def test_parsing_rules() -> None:
    raw = b"""
# a comment
PLAIN=value
SPACED = padded value  
export EXPORTED=yes
DOUBLE="has # hash and = sign"
SINGLE='quoted' # trailing comment
COMMENTED=value # not part of it
HASH=a#b
EMPTY=
UNCLOSED="oops
EXPORTONLY=1
not a variable
1BAD=x
LATER=1
LATER=2
"""
    assert parse_dotenv(raw) == {
        "PLAIN": "value",
        "SPACED": "padded value",
        "EXPORTED": "yes",
        "DOUBLE": "has # hash and = sign",
        "SINGLE": "quoted",
        "COMMENTED": "value",
        "HASH": "a#b",
        "EMPTY": "",
        "UNCLOSED": '"oops',
        "EXPORTONLY": "1",
        "LATER": "2",
    }


@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-8-sig", "utf-16"], ids=["utf-8", "utf-8-bom", "utf-16-powershell"]
)
def test_the_encodings_windows_editors_write(encoding: str) -> None:
    raw = "NAME=café\r\nOTHER=2\r\n".encode(encoding)
    assert parse_dotenv(raw) == {"NAME": "café", "OTHER": "2"}


def test_a_missing_default_file_is_fine_and_a_missing_named_file_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_dotenv(environ={}) == []
    with pytest.raises(ConfigError, match="does not exist"):
        load_dotenv(environ={"MAKAN_ENV_FILE": str(tmp_path / "nope.env")})


def test_the_default_file_is_dot_env_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "FROM_DEFAULT=1\n")
    monkeypatch.chdir(tmp_path)
    environ: dict[str, str] = {}
    load_dotenv(environ=environ)
    assert environ == {"FROM_DEFAULT": "1"}


def test_makan_env_file_names_another_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, "FROM_DEFAULT=1\n")
    other = write(tmp_path, "FROM_OTHER=1\r\n", "prod.env")
    monkeypatch.chdir(tmp_path)
    environ = {"MAKAN_ENV_FILE": str(other)}
    load_dotenv(environ=environ)
    assert environ == {"MAKAN_ENV_FILE": str(other), "FROM_OTHER": "1"}
