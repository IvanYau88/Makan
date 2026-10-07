"""`python -m makan.web`: argument handling, setup checks and what it hands to uvicorn."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from makan.web import __main__ as entry


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    """Replace uvicorn.run, and isolate the process from any real `.env` or build."""
    calls: list[dict[str, Any]] = []

    def run(app: str, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr("uvicorn.run", run)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAKAN_ENV_FILE", raising=False)
    monkeypatch.setenv("MAKAN_DEMO", "0")  # restored afterwards, however the test changes it
    monkeypatch.setenv("MAKAN_WEB_DIST", str(tmp_path / "dist"))
    return calls


def test_defaults_match_the_vite_proxy() -> None:
    args = entry.parse_args([])
    assert (args.host, args.port, args.demo, args.reload) == ("127.0.0.1", 8000, False, False)
    vite_config = Path(__file__).parents[1] / "web" / "vite.config.ts"
    assert f"http://{entry.DEFAULT_HOST}:{entry.DEFAULT_PORT}" in vite_config.read_text()


def test_flags_are_read() -> None:
    args = entry.parse_args(["--demo", "--reload", "--host", "0.0.0.0", "--port", "9001"])
    assert (args.host, args.port, args.demo, args.reload) == ("0.0.0.0", 9001, True, True)


@pytest.mark.parametrize("port", ["abc", "-1", "65536", ""])
def test_a_bad_port_is_a_usage_error(port: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        entry.parse_args(["--port", port])
    assert exit_info.value.code == 2
    assert "port" in capsys.readouterr().err


def test_an_unknown_flag_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        entry.parse_args(["--debug"])
    assert exit_info.value.code == 2


def test_it_runs_the_factory_without_reload_by_default(
    uvicorn_calls: list[dict[str, Any]],
) -> None:
    assert entry.main(["--demo", "--host", "0.0.0.0", "--port", "9001"]) == 0
    assert uvicorn_calls == [
        {
            "app": "makan.web:create_app_from_env",
            "factory": True,
            "host": "0.0.0.0",
            "port": 9001,
            "reload": False,
            "reload_dirs": None,
        }
    ]


def test_reload_watches_the_package(uvicorn_calls: list[dict[str, Any]]) -> None:
    assert entry.main(["--demo", "--reload"]) == 0
    (call,) = uvicorn_calls
    assert call["reload"] is True
    assert [Path(d) for d in call["reload_dirs"]] == [entry.PACKAGE_DIR]


def test_demo_sets_the_variable_the_factory_reads(
    uvicorn_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entry, "missing_packages", lambda *, demo: [] if demo else ["duckdb"])
    assert entry.main(["--demo"]) == 0
    assert os.environ["MAKAN_DEMO"] == "1"
    assert len(uvicorn_calls) == 1


def test_demo_from_the_env_file_counts(
    uvicorn_calls: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAKAN_DEMO")
    monkeypatch.setattr(os, "environ", os.environ.copy())  # load_dotenv writes here
    (tmp_path / ".env").write_text("MAKAN_DEMO=1\n")
    monkeypatch.setattr(entry, "missing_packages", lambda *, demo: [] if demo else ["duckdb"])
    assert entry.main([]) == 0
    assert len(uvicorn_calls) == 1


def test_missing_packages_are_named_with_the_fix(
    uvicorn_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(entry, "missing_packages", lambda *, demo: ["duckdb"])
    assert entry.main([]) == 1
    err = capsys.readouterr().err
    assert "duckdb not installed" in err
    assert 'pip install -e ".[web,overture]"' in err
    assert uvicorn_calls == []


def test_duckdb_is_only_needed_for_live_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert entry.missing_packages(demo=True) == ["fastapi", "uvicorn"]
    assert entry.missing_packages(demo=False) == ["fastapi", "uvicorn", "duckdb"]


def test_a_missing_named_env_file_stops_the_start(
    uvicorn_calls: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MAKAN_ENV_FILE", str(tmp_path / "nope.env"))
    assert entry.main(["--demo"]) == 1
    assert "does not exist" in capsys.readouterr().err
    assert uvicorn_calls == []


def test_it_says_when_there_is_no_built_page(
    uvicorn_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main(["--demo"]) == 0
    assert "serves the API only" in capsys.readouterr().err


def test_it_stays_quiet_when_the_page_is_built(
    uvicorn_calls: list[dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "dist").mkdir()
    assert entry.main(["--demo"]) == 0
    assert capsys.readouterr().err == ""


def test_importing_without_fastapi_explains_the_extra() -> None:
    code = "import sys; sys.modules['fastapi'] = None; import makan.web"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'pip install -e ".[web,overture]"' in result.stderr
