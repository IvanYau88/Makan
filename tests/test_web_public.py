"""The public deployment: the entrypoint, its defaults, and a smoke check of every route that
matters in both public modes. Every app here is built from an explicit environment mapping, with
no key, no `.env`, and the network blocked, so a call that leaves the process fails the test."""

import importlib.metadata
import importlib.util
import json
import re
import socket
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packaging.requirements import Requirement

from makan.config import Config, ConfigError
from makan.places import CachedPlacesProvider, OvertureProvider
from makan.places.overture import Bounds, Row
from makan.providers import ProviderError
from makan.providers.unavailable import UnavailableProvider
from makan.web import create_app, create_app_from_env
from makan.web.public import PUBLIC_DEFAULTS, create_public_app, public_env
from tests.helpers import KLCC, overture_rows

ROOT = Path(__file__).resolve().parent.parent
RELEASE = "2026-09-23.1"
GROUP = "2d6c1b8e-0000-4000-8000-000000000000"
BROWSE: dict[str, Any] = {"mode": "browse", "latitude": KLCC[0], "longitude": KLCC[1]}
RECOMMEND: dict[str, Any] = {**BROWSE, "mode": "recommend", "request": "thai please"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a public smoke test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)


class Source:
    """Overture rows from a fixture, counting how often anything read them."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, release: str, bounds: Bounds) -> list[Row]:
        self.calls += 1
        return overture_rows()


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> Source:
    """Real places from the fixture, so the places-only mode is exercised without Overture."""
    fixture = Source()
    monkeypatch.setattr(
        "makan.web.app.places_provider",
        lambda config: CachedPlacesProvider(OvertureProvider(RELEASE, source=fixture)),
    )
    return fixture


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>Makan</title>", encoding="utf-8")
    (built / "assets" / "app.js").write_text("console.log('makan')", encoding="utf-8")
    return built


def public(dist: Path, **env: str) -> TestClient:
    return TestClient(create_public_app(env, web_dist=dist))


@pytest.fixture(params=["demo", "places"])
def site(request: pytest.FixtureRequest, dist: Path, source: Source) -> TestClient:
    """The public site in each of its two modes: the sample places, and the real ones."""
    env = {"MAKAN_DEMO": "1"} if request.param == "demo" else {}
    return public(dist, **env)


def mode_of(site: TestClient) -> str:
    return str(site.get("/api/health").json()["mode"])


def ndjson(response: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line]


# The entrypoint


@pytest.mark.parametrize("fit", ["0", "1"])
def test_the_root_entrypoint_exports_an_app_without_any_key_or_remote_work(
    monkeypatch: pytest.MonkeyPatch,
    source: Source,
    fit: str,
) -> None:
    for name in ("OPENROUTER_API_KEY", "MAKAN_MODEL", "MAKAN_DEMO", "MAKAN_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.test")  # half set, and ignored
    monkeypatch.setenv("MAKAN_FIT_ENABLED", fit)
    spec = importlib.util.spec_from_file_location("vercel_entrypoint", ROOT / "app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module.app, FastAPI)
    assert source.calls == 0  # importing it read no places
    assert TestClient(module.app).get("/api/health").json() == {"status": "ok", "mode": "places"}
    config = TestClient(module.app).get("/api/config").json()
    assert config["features"]["fit"] is (fit == "1")
    assert config["auth"] is None


def test_the_public_app_uses_real_places_by_default_and_sample_ones_on_request(
    dist: Path, source: Source
) -> None:
    assert mode_of(public(dist)) == "places"
    assert mode_of(public(dist, MAKAN_DEMO="1")) == "demo"
    assert mode_of(public(dist, MAKAN_DEMO="1", MAKAN_PLACES_ONLY="1")) == "demo"


def test_the_public_defaults_fill_only_what_is_unset_or_blank(tmp_path: Path) -> None:
    filled = public_env({"MAKAN_GROUPS_ENABLED": "  ", "MAKAN_PLACES_ONLY": "0"}, tmp_path)
    assert filled["MAKAN_GROUPS_ENABLED"] == "0"
    assert filled["MAKAN_ACCOUNTS_ENABLED"] == "0"
    assert filled["MAKAN_PLACES_ONLY"] == "0"  # a choice, even a "no", is kept
    assert filled["MAKAN_WEB_DIST"] == str(tmp_path)
    assert public_env({"MAKAN_WEB_DIST": "web/dist"}, tmp_path)["MAKAN_WEB_DIST"] == "web/dist"
    assert set(PUBLIC_DEFAULTS) >= {"MAKAN_GROUPS_ENABLED", "MAKAN_ACCOUNTS_ENABLED"}


def test_the_public_app_never_reads_dot_env(
    tmp_path: Path, dist: Path, monkeypatch: pytest.MonkeyPatch, source: Source
) -> None:
    (tmp_path / ".env").write_text("MAKAN_DEMO=1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAKAN_DEMO", raising=False)
    assert mode_of(public(dist)) == "places"


def test_a_public_site_can_still_be_pointed_at_the_live_providers(dist: Path) -> None:
    app = public(dist, MAKAN_PLACES_ONLY="0", MAKAN_MODEL="some/model", OPENROUTER_API_KEY="k")
    assert app.get("/api/health").json()["mode"] == "live"
    assert app.get("/api/config").json()["features"]["groups"] is False  # still off


# Smoke checks, in both modes


def test_the_page_and_its_assets_are_served(site: TestClient) -> None:
    page = site.get("/")
    assert page.status_code == 200 and "<title>Makan</title>" in page.text
    asset = site.get("/assets/app.js")
    assert asset.status_code == 200 and "makan" in asset.text
    assert site.get("/assets/missing.js").status_code == 404


def test_health_and_config_say_what_this_site_is(site: TestClient) -> None:
    mode = mode_of(site)
    assert site.get("/api/health").json() == {"status": "ok", "mode": mode}
    config = site.get("/api/config").json()
    assert config["mode"] == mode
    assert config["auth"] is None
    assert config["features"] == {
        "recommend": mode == "demo",
        "groups": False,
        "accounts": False,
        "fit": False,
    }
    assert config["map"]["tile_url"]


def test_browse_answers_as_json_with_its_own_data_source(site: TestClient, source: Source) -> None:
    mode = mode_of(site)
    response = site.post("/api/recommendations", json=BROWSE)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == mode and body["pick"] is None
    assert body["places"] and body["query"]["mode"] == "browse"
    if mode == "demo":
        assert body["data_source"] == "demo" and body["attribution"] is None
        assert all(p["name"].startswith("Sample ") for p in body["places"])
        assert source.calls == 0
    else:
        assert body["data_source"] == f"overture:{RELEASE}"
        assert "Overture" in body["attribution"]
        assert not any(p["name"].startswith("Sample ") for p in body["places"])
        assert source.calls == 1
    assert body["run"]["mode"] == mode


def test_browse_streams_progress_then_a_result(site: TestClient) -> None:
    response = site.post("/api/recommendations/stream", json=BROWSE)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = ndjson(response)
    assert lines[-1]["type"] == "result" and lines[-1]["recommendation"]["places"]
    assert {line["type"] for line in lines[:-1]} == {"run"}


def test_picking_works_on_the_sample_places_and_is_refused_on_the_real_ones(
    site: TestClient, source: Source
) -> None:
    plain = site.post("/api/recommendations", json=RECOMMEND)
    stream = site.post("/api/recommendations/stream", json=RECOMMEND)
    if mode_of(site) == "demo":
        assert plain.status_code == 200 and plain.json()["pick"]["name"].startswith("Sample ")
        assert ndjson(stream)[-1]["type"] == "result"
        return
    for refused in (plain, stream):
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "recommendations_unavailable"
    assert source.calls == 0  # nothing was searched for a request that cannot be answered


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/groups"),
        ("GET", f"/api/groups/{GROUP}"),
        ("POST", f"/api/groups/{GROUP}/join"),
        ("GET", "/api/me"),
        ("GET", "/api/me/export"),
        ("PUT", "/api/me/profile"),
        ("DELETE", "/api/me"),
        ("GET", "/api/me/visits"),
        ("POST", "/api/me/visits"),
        ("GET", f"/g/{GROUP}"),
        ("GET", f"/g/{GROUP}/"),
    ],
)
def test_group_and_account_routes_are_absent_when_switched_off(
    site: TestClient, method: str, path: str
) -> None:
    response = site.request(method, path, json={} if method != "GET" else None)
    assert response.status_code == 404
    if path.startswith("/api/"):
        assert response.json()["error"]["code"] == "not_found"


def test_only_refusals_are_registered_for_group_and_account_paths(site: TestClient) -> None:
    routes = [
        route
        for route in site.app.routes  # type: ignore[attr-defined]
        if getattr(route, "path", "").startswith(("/api/groups", "/api/me", "/g/"))
    ]
    assert routes and all(route.endpoint.__name__ == "not_found" for route in routes)


def test_a_stray_authorization_header_changes_nothing(site: TestClient) -> None:
    response = site.post(
        "/api/recommendations", json=BROWSE, headers={"Authorization": "Bearer not-a-token"}
    )
    assert response.status_code == 200


def test_accounts_and_a_database_in_the_environment_stay_unused_while_switched_off(
    dist: Path, source: Source
) -> None:
    app = public(
        dist,
        SUPABASE_URL="https://project.supabase.test",
        SUPABASE_ANON_KEY="anon",
        SUPABASE_SERVICE_ROLE_KEY="service",
        MAKAN_DATABASE_URL="postgresql://nowhere.invalid/db",  # connecting would fail the test
    )
    assert app.get("/api/config").json()["auth"] is None
    assert app.get("/api/me").status_code == 404


def test_a_group_or_account_switch_can_still_turn_the_feature_on(
    dist: Path, source: Source
) -> None:
    app = public(dist, MAKAN_DEMO="1", MAKAN_GROUPS_ENABLED="1")
    assert app.get("/api/config").json()["features"]["groups"] is True
    assert app.post("/api/groups", json={}).status_code == 422  # reached the group route
    assert app.get(f"/g/{GROUP}").status_code == 200


def test_a_missing_build_leaves_the_api_and_says_so_in_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, source: Source
) -> None:
    with caplog.at_level("WARNING", logger="makan.web"):
        app = public(tmp_path / "missing")
    assert "no built page" in caplog.text
    assert app.get("/").status_code == 404
    assert app.get("/api/health").status_code == 200


# The switches, outside the public entrypoint


def test_groups_are_on_by_default_and_a_false_switch_removes_them(tmp_path: Path) -> None:
    base = {"MAKAN_DEMO": "1", "MAKAN_WEB_DIST": str(tmp_path)}
    (tmp_path / "index.html").write_text("<h1>Makan</h1>")
    on = TestClient(create_app_from_env(base))
    assert on.get("/api/config").json()["features"]["groups"] is True
    assert on.get(f"/g/{GROUP}").status_code == 200
    off = TestClient(create_app_from_env({**base, "MAKAN_GROUPS_ENABLED": "false"}))
    assert off.get("/api/config").json()["features"]["groups"] is False
    assert off.get(f"/g/{GROUP}").status_code == 404
    assert off.post("/api/groups", json={}).status_code == 404


def test_the_accounts_switch_wins_over_a_complete_supabase_setting(tmp_path: Path) -> None:
    env = {"MAKAN_DEMO": "1", "MAKAN_WEB_DIST": str(tmp_path), "MAKAN_ACCOUNTS_ENABLED": "0"}
    assert (
        Config.from_env({**env, "MAKAN_MODEL": "m", "SUPABASE_URL": "https://p.test"}).supabase
        is None
    )
    app = TestClient(create_app_from_env({**env, "SUPABASE_URL": "https://p.test"}))
    assert app.get("/api/config").json()["auth"] is None


@pytest.mark.parametrize("value", ["maybe", "2", "enabled"])
def test_a_switch_must_be_a_clear_yes_or_no(value: str) -> None:
    with pytest.raises(ConfigError, match="MAKAN_GROUPS_ENABLED"):
        Config.from_env({"MAKAN_MODEL": "m", "MAKAN_GROUPS_ENABLED": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", True), ("1", True), ("on", True), ("0", False), ("No", False), ("OFF", False)],
)
def test_switch_values(value: str, expected: bool) -> None:
    config = Config.from_env({"MAKAN_MODEL": "m", "MAKAN_ACCOUNTS_ENABLED": value})
    assert config.accounts_enabled is expected


def test_places_only_mode_needs_no_key_and_never_calls_a_model(
    tmp_path: Path, source: Source
) -> None:
    app = TestClient(
        create_app_from_env({"MAKAN_PLACES_ONLY": "1", "MAKAN_WEB_DIST": str(tmp_path)})
    )
    assert app.get("/api/health").json()["mode"] == "places"
    assert app.post("/api/recommendations", json=BROWSE).status_code == 200
    with pytest.raises(ProviderError):
        UnavailableProvider().complete(model="m", messages=[], tools=[])


def test_groups_stay_off_without_a_model_even_when_switched_on(source: Source) -> None:
    config = Config(model="none", groups_enabled=True)
    app = create_app(
        provider=UnavailableProvider(),
        places=CachedPlacesProvider(OvertureProvider(RELEASE, source=source)),
        config=config,
        mode="places",
    )
    assert TestClient(app).post("/api/groups", json={}).status_code == 404


def test_the_duckdb_bounds_come_from_the_environment() -> None:
    config = Config.from_env(
        {
            "MAKAN_MODEL": "m",
            "MAKAN_DUCKDB_THREADS": "1",
            "MAKAN_DUCKDB_MEMORY_MB": "512",
            "MAKAN_DUCKDB_DIR": "/tmp/x",
            "MAKAN_PLACES_DEADLINE_SECONDS": "15",
        }
    )
    assert (config.duckdb.threads, config.duckdb.memory_mb) == (1, 512)
    assert (config.duckdb.work_dir, config.duckdb.deadline_s) == ("/tmp/x", 15.0)
    default = Config(model="m").duckdb
    assert default.deadline_s < 30  # inside the graph's own 30 second step timeout


# What the Vercel project says


def test_vercel_json_builds_the_page_and_stays_inside_the_free_limits() -> None:
    config = json.loads((ROOT / "vercel.json").read_text("utf-8"))
    assert "npm" in config["buildCommand"] and "web" in config["buildCommand"]
    assert config["env"]["MAKAN_WEB_DIST"] == "web/dist"
    entry = config["functions"]["app.py"]
    assert (ROOT / "app.py").is_file()
    assert 0 < entry["maxDuration"] <= 300
    excluded = entry["excludeFiles"]
    for heavy in ("web/node_modules", "tests", ".venv", ".git"):
        assert heavy in excluded
    assert "web/dist" not in excluded  # the built page must stay in the function


def _requirement_name(requirement: str) -> str:
    return re.split(r"[\[<>=!~; ]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _vercel_installed(pyproject: dict[str, Any]) -> list[str]:
    """What `uv sync --no-dev` installs from pyproject.toml, which Vercel reads and requirements.txt
    it ignores: the base dependencies and the default groups, and never an extra."""
    groups = pyproject["dependency-groups"]
    defaults = pyproject["tool"]["uv"]["default-groups"]
    return [*pyproject["project"]["dependencies"], *(r for g in defaults for r in groups[g])]


def test_python_is_pinned_and_the_production_install_is_the_runtime_only() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    version = (ROOT / ".python-version").read_text("utf-8").strip()
    assert version in ("3.12", "3.13", "3.14")  # what Vercel supports
    minimum = pyproject["project"]["requires-python"].removeprefix(">=")
    assert tuple(map(int, version.split("."))) >= tuple(map(int, minimum.split(".")))
    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert all("==" in line for line in lines), "every production dependency is pinned"
    names = {_requirement_name(line) for line in lines}
    project = pyproject["project"]
    wanted = {
        _requirement_name(dependency)
        for dependency in (
            *project["dependencies"],
            *project["optional-dependencies"]["web"],
            *project["optional-dependencies"]["overture"],
        )
    } - {"uvicorn"}  # Vercel runs the app itself
    assert names == wanted
    assert not names & {"pytest", "ruff", "mypy", "uvicorn", "psycopg"}
    # Vercel installs from pyproject.toml's default groups, so the file it reads must say the same
    # thing, with the same pins, as the requirements file a person installs from.
    assert sorted(pyproject["dependency-groups"]["public"]) == sorted(lines)


def test_the_entrypoint_imports_with_only_the_packages_vercel_installs() -> None:
    """The bug this guards: Vercel installed pyproject.toml's base dependencies (only httpx) and no
    extra, so importing app.py failed on `fastapi`. A fresh interpreter blocks every installed
    package outside what Vercel installs, and the entrypoint must still import."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    installed: set[str] = set()
    pending = [Requirement(text) for text in _vercel_installed(pyproject)]
    while pending:  # the closure of what Vercel installs, with the extras each requirement asks for
        requirement = pending.pop()
        name = requirement.name.lower().replace("_", "-")
        if name in installed and not requirement.extras:
            continue
        installed.add(name)
        for text in importlib.metadata.requires(name) or []:
            child = Requirement(text)
            wanted = requirement.extras or {""}
            if child.marker is None or any(child.marker.evaluate({"extra": e}) for e in wanted):
                pending.append(child)
    assert {"fastapi", "httpx", "pyjwt", "cryptography", "duckdb"} <= installed
    blocked = sorted(
        module
        for module, owners in importlib.metadata.packages_distributions().items()
        if not {o.lower().replace("_", "-") for o in owners} & (installed | {"makan"})
    )
    assert {"psycopg", "uvicorn"} <= set(blocked)  # the environment has them and Vercel does not
    script = (
        "import sys, json\n"
        "for module in json.loads(sys.argv[2]): sys.modules[module] = None\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import app\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(ROOT), json.dumps(blocked)],
        capture_output=True,
        text=True,
        check=False,
        env={"MAKAN_WEB_DIST": str(ROOT / "missing")},
    )
    assert done.returncode == 0, done.stderr


def test_the_vercel_install_source_does_not_depend_on_an_extra() -> None:
    """`uv sync --no-dev` ignores `[project.optional-dependencies]`, so the default groups, and not
    an extra, are what make the install complete."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert pyproject["tool"]["uv"]["default-groups"] == ["public"]
    installed = {_requirement_name(r) for r in _vercel_installed(pyproject)}
    assert {"httpx", "fastapi", "pyjwt", "duckdb"} <= installed
    assert not installed & {"uvicorn", "psycopg", "pytest", "ruff", "mypy"}


def test_the_ignore_file_keeps_secrets_and_environments_out_of_the_upload() -> None:
    ignored = (ROOT / ".vercelignore").read_text("utf-8").splitlines()
    assert {".env", ".venv", "web/node_modules"} <= set(ignored)


def test_the_public_app_builds_without_the_postgres_extra_or_uvicorn_or_duckdb() -> None:
    """What the Vercel install lacks: psycopg, uvicorn, and (until the first search) a loaded
    DuckDB. A fresh interpreter blocks the first two and reports whether the third was imported."""
    script = (
        "import sys; sys.path.insert(0, sys.argv[1] + '/src')\n"
        "sys.modules['psycopg'] = None; sys.modules['uvicorn'] = None\n"
        "from makan.web.public import create_public_app\n"
        "create_public_app({'MAKAN_WEB_DIST': sys.argv[1] + '/missing'})\n"
        "print('duckdb' in sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", script, str(ROOT)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False"
