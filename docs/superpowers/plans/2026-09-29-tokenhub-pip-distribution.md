# Token Hub pip Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an installable Token Hub package whose command starts and opens a persistent local dashboard on macOS, Linux, and Windows.

**Architecture:** Ship the compiled Vite UI inside the Python package and configure Alembic from package paths. A CLI launcher manages one detached local server per user; a private token challenge identifies that server before the launcher opens or stops it.

**Tech Stack:** Python 3.12+, FastAPI, Uvicorn, Alembic, setuptools, filelock, React/Vite, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-29-tokenhub-pip-distribution-design.md`

## Global Constraints

- Python 3.12 or newer; first release supports macOS, Linux, and Windows.
- `tokenhub` starts a background server, opens the browser when ready, then exits; the server survives terminal closure.
- Restart after reboot is manual. Installation itself never starts a process or reads provider data.
- Default server URL is `http://127.0.0.1:7432/`; alternate ports use `tokenhub start --port PORT`.
- Preserve the loopback bind, strict Host and Origin guards, no CORS, and approval before provider import.
- Include the compiled UI in both wheel and source distribution; end users need no Node.
- Use the MIT license with `Copyright (c) 2026 Aldrin Joseph`.
- Public PyPI upload follows review of the release artifacts and installed-package results.

## Review Focus

1. **Foreign server on the selected port:** Task 4 tests that its response never satisfies the per-instance token challenge and start does not open that page.
2. **Two simultaneous starts:** Task 4 tests that the cross-platform lock permits only one child and both callers receive the same URL.
3. **Reused PID or corrupted instance record:** Task 3 tests strict record validation; Task 5 tests that stop never signals a PID and never sends an unauthenticated shutdown.
4. **Child exits or hangs before readiness:** Task 4 tests cleanup of its own child and record, an actionable error, and no browser opening.
5. **No usable browser:** Task 5 tests that the server remains running, the URL is printed, and the command still succeeds.

---

## File map

- `frontend/vite.config.ts` generates the committed UI under `backend/tokenhub/web/`.
- `backend/tokenhub/app.py` resolves installed UI assets and mounts private runtime routes before the static catch-all.
- `backend/tokenhub/database/migrations.py` constructs an Alembic configuration without a repository-level INI file.
- `backend/tokenhub/runtime/control.py` owns token-checked readiness and shutdown HTTP routes.
- `backend/tokenhub/runtime/instance.py` owns validated, atomic instance records.
- `backend/tokenhub/runtime/client.py` owns direct loopback requests to those private routes.
- `backend/tokenhub/runtime/manager.py` owns locking, subprocess startup, readiness, status, and shutdown.
- `backend/tokenhub/server.py` runs Uvicorn in the child and connects graceful shutdown.
- `backend/tokenhub/cli.py` maps user commands to the manager; `backend/tokenhub/__main__.py` supports the installed child invocation.
- `scripts/verify_distribution.py` checks both built archives and performs an installed-package smoke test.
- `.github/workflows/package.yml` runs cross-platform checks and artifact validation.

### Task 1: Make source and installed builds self-contained

**Files:**
- Modify: `frontend/vite.config.ts`, `backend/tokenhub/app.py`, `backend/tokenhub/database/migrations.py`, `pyproject.toml`, `README.md`
- Create: `backend/tokenhub/web/index.html`, `backend/tokenhub/web/favicon.svg`, `backend/tokenhub/web/assets/*`
- Test: `tests/api/test_static_mount.py`, `tests/api/test_lifespan.py`

**Interfaces:**
- Produces `FRONTEND_DIST = Path(__file__).resolve().parent / "web"`.
- Produces `migration_config() -> alembic.config.Config` with `script_location` inside the installed `tokenhub` package.

- [ ] **Step 1: Write the failing package-path tests.** Add:

~~~python
def test_frontend_dist_is_inside_the_installed_package() -> None:
    import tokenhub.app as app_module

    assert app_module.FRONTEND_DIST == Path(app_module.__file__).resolve().parent / "web"
    assert (app_module.FRONTEND_DIST / "index.html").is_file()


def test_migration_config_needs_no_checkout_ini() -> None:
    from tokenhub.database.migrations import migration_config

    config = migration_config()
    assert config.config_file_name is None
    assert Path(config.get_main_option("script_location")).is_dir()
~~~

- [ ] **Step 2: Run those tests and confirm both fail on the current source paths.** Run `uv run pytest tests/api/test_static_mount.py tests/api/test_lifespan.py -q`.
- [ ] **Step 3: Change the build destination and installed resource paths.** In Vite, set `outDir: '../backend/tokenhub/web'`; in `app.py` use the package-local path above. In `migrations.py` use:

~~~python
SCRIPT_LOCATION = Path(__file__).resolve().parent / "migrations"


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config
~~~

- [ ] **Step 4: Declare package data and generate the UI.** Add this to `pyproject.toml`, remove the generated `backend/tokenhub.egg-info/` cache before the first new source build, then run `npm --prefix frontend ci` and `npm --prefix frontend run build` and stage `backend/tokenhub/web/`:

~~~toml
[tool.setuptools.package-data]
tokenhub = ["web/index.html", "web/favicon.svg", "web/assets/*"]
~~~

- [ ] **Step 5: Update the README's source-build note and run targeted checks.** The app now serves `backend/tokenhub/web/`. Run `uv run pytest tests/api/test_static_mount.py tests/api/test_lifespan.py -q`, `uv run ruff check backend tests`, and `uv run mypy backend`.
- [ ] **Step 6: Commit.** Stage only the files in this task and commit with `feat: bundle UI and migration resources in Python package`.

### Task 2: Add private, token-checked runtime control

**Files:**
- Create: `backend/tokenhub/runtime/__init__.py`, `backend/tokenhub/runtime/control.py`, `backend/tokenhub/server.py`
- Modify: `backend/tokenhub/app.py`
- Test: `tests/api/test_runtime_control.py`

**Interfaces:**
- Produces `RuntimeControl(token: str, shutdown: Callable[[], None])` and `proof(token: str, message: str) -> str`.
- Produces `create_app(settings: TokenHubSettings | None = None, runtime_control: RuntimeControl | None = None) -> FastAPI`.
- Produces `run_server(settings: TokenHubSettings, control_token: str | None = None) -> None`.
- Private routes: `GET /api/v1/_runtime/ready` accepts one random `X-TokenHub-Challenge` and returns `proof(token, f"ready:{challenge}")`. `POST /api/v1/_runtime/stop` accepts one random `X-TokenHub-Nonce` and matching `proof(token, f"stop:{nonce}")`. The prefixes separate the two purposes. The POST also passes the existing Origin guard. The secret is never sent in an HTTP header or response.

- [ ] **Step 1: Write the route tests.** In `tests/api/test_runtime_control.py`, create a test app with a synthetic token and callback. Assert missing or duplicate challenge headers return 404; the right ready request returns `{"proof": proof(token, f"ready:{challenge}")}`; a wrong-Origin stop returns 403; a wrong stop proof returns 404; the authorized stop returns 202 and calls the callback once. Assert a normal app has neither private route. Also assert a readiness proof cannot authorize a stop request.

~~~python
token = "a" * 64
challenge = "b" * 64
stops: list[str] = []
app = create_app(
    TokenHubSettings(home_directory=tmp_path / "home", data_directory=tmp_path / "data"),
    RuntimeControl(token, lambda: stops.append("stop")),
)
with TestClient(app, base_url="http://127.0.0.1:7432") as http:
    ready = http.get(
        "/api/v1/_runtime/ready",
        headers={"x-tokenhub-challenge": challenge},
    )
    assert ready.json() == {"proof": proof(token, f"ready:{challenge}")}
    assert http.post(
        "/api/v1/_runtime/stop",
        headers={
            "origin": "http://127.0.0.1:7432",
            "x-tokenhub-nonce": challenge,
            "x-tokenhub-proof": ready.json()["proof"],
        },
    ).status_code == 404
    assert stops == []
~~~
- [ ] **Step 2: Run the new tests and confirm they fail.** Run `uv run pytest tests/api/test_runtime_control.py -q`.
- [ ] **Step 3: Implement the control router before the static mount.** Use HMAC-SHA256 and `secrets.compare_digest`, reject duplicate header values, and install the router only when `runtime_control` is supplied. The core guard remains the first middleware for these requests:

~~~python
@dataclass(frozen=True, slots=True)
class RuntimeControl:
    token: str
    shutdown: Callable[[], None]


def proof(token: str, message: str) -> str:
    return hmac.new(bytes.fromhex(token), message.encode(), hashlib.sha256).hexdigest()


def authorized_stop(request: Request, token: str) -> bool:
    nonces = request.headers.getlist("x-tokenhub-nonce")
    proofs = request.headers.getlist("x-tokenhub-proof")
    return (
        len(nonces) == len(proofs) == 1
        and secrets.compare_digest(proofs[0], proof(token, f"stop:{nonces[0]}"))
    )
~~~

- [ ] **Step 4: Run Uvicorn through a controllable Server object.** In `server.py`, create the app with a callback that sets `server.should_exit = True`, then call `server.run()`. Preserve `proxy_headers=False` and `access_log=False`.

~~~python
def run_server(settings: TokenHubSettings, control_token: str | None = None) -> None:
    server: uvicorn.Server

    def shutdown() -> None:
        server.should_exit = True

    control = RuntimeControl(control_token, shutdown) if control_token else None
    app = create_app(settings, runtime_control=control)
    config = uvicorn.Config(
        app, host=settings.host, port=settings.port,
        proxy_headers=False, access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
~~~
- [ ] **Step 5: Run the route and security tests.** Run `uv run pytest tests/api/test_runtime_control.py tests/api/test_security.py tests/api/test_static_mount.py -q`.
- [ ] **Step 6: Commit.** Commit this task as `feat: add authenticated local runtime control`.

### Task 3: Store and verify one local instance

**Files:**
- Create: `backend/tokenhub/runtime/instance.py`, `backend/tokenhub/runtime/client.py`
- Test: `tests/runtime/test_instance.py`, `tests/runtime/test_client.py`

**Interfaces:**
- Produces `InstanceRecord(pid: int, port: int, token: str)`.
- Produces `read_record(data_directory: Path) -> InstanceRecord | None`, `write_record(data_directory: Path, record: InstanceRecord) -> None`, and `delete_record(data_directory: Path) -> None`.
- Produces `probe(record: InstanceRecord) -> bool` and `request_stop(record: InstanceRecord) -> bool`.

- [ ] **Step 1: Write record tests.** Round-trip a valid record; assert missing, malformed JSON, invalid port, nonpositive PID, and a token shorter than 64 hexadecimal characters return `None`. Assert a write replaces the record atomically and the file mode is 0600 on POSIX.

~~~python
record = InstanceRecord(pid=123, port=7432, token="a" * 64)
write_record(tmp_path, record)
assert read_record(tmp_path) == record
(tmp_path / "runtime.json").write_text('{"pid": -1, "port": 7432, "token": "bad"}')
assert read_record(tmp_path) is None
~~~
- [ ] **Step 2: Write client tests using a synthetic local HTTP server.** Assert `probe` uses a fresh random challenge and accepts only the correct HMAC response; `request_stop` sends `Origin: http://127.0.0.1:PORT` and a fresh HMAC stop proof. HTTP errors, connection refusal, and a foreign 200 response with a forged proof are not identity proof.
- [ ] **Step 3: Run the tests and confirm they fail.** Run `uv run pytest tests/runtime/test_instance.py tests/runtime/test_client.py -q`.
- [ ] **Step 4: Implement validated storage and direct loopback calls.** Use a same-directory temporary file plus `os.replace` for the record, `os.chmod(path, 0o600)` on POSIX, `urllib.request.build_opener(ProxyHandler({}))`, and a short timeout. For readiness, send a fresh challenge, parse the response's `proof` string after HTTP 200, and compare it with `proof(token, f"ready:{challenge}")`. For stop, send a fresh nonce and `proof(token, f"stop:{nonce}")`. No secret or process signal is sent by this module.

~~~python
challenge = secrets.token_hex(32)
request = urllib.request.Request(
    f"http://127.0.0.1:{record.port}/api/v1/_runtime/ready",
    headers={"X-TokenHub-Challenge": challenge},
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request, timeout=0.5) as response:
    actual = json.load(response)["proof"]
return secrets.compare_digest(actual, proof(record.token, f"ready:{challenge}"))
~~~
- [ ] **Step 5: Run tests and type checks.** Run `uv run pytest tests/runtime/test_instance.py tests/runtime/test_client.py -q`, `uv run ruff check backend tests`, and `uv run mypy backend`.
- [ ] **Step 6: Commit.** Commit as `feat: verify managed local instances with private challenge`.

### Task 4: Start and inspect a background server

**Files:**
- Create: `backend/tokenhub/runtime/manager.py`
- Modify: `pyproject.toml`
- Test: `tests/runtime/test_manager.py`

**Interfaces:**
- Produces `RuntimeManager(data_directory: Path)` with `start(port: int | None = None) -> str` and `status() -> str | None`. `None` reuses an existing instance on any recorded port or selects 7432 for a new instance.
- Consumes `InstanceRecord`, `read_record`, `write_record`, `delete_record`, and `probe` from Task 3.
- Uses `filelock.FileLock` at `data_directory / "runtime.lock"` to serialize start decisions.

- [ ] **Step 1: Write failing manager tests.** Mock spawning and probing to verify: a healthy instance is reused even when it uses port 9000 and no port was requested; an explicit different port is rejected; two simultaneous starts produce one child; a foreign process on the port never becomes ready; early exit and timeout remove the new record and return an error. Verify the browser is not called by this layer.

~~~python
record = InstanceRecord(pid=123, port=9000, token="a" * 64)
write_record(tmp_path, record)
monkeypatch.setattr("tokenhub.runtime.manager.probe", lambda _: True)
manager = RuntimeManager(tmp_path)
assert manager.start() == "http://127.0.0.1:9000/"
with pytest.raises(RuntimeError, match="already running"):
    manager.start(port=7432)
~~~
- [ ] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/runtime/test_manager.py -q`.
- [ ] **Step 3: Add `filelock>=3.12,<4` to runtime dependencies and implement launch.** Create the data directory if needed (`mode=0o700` on POSIX). Under the lock, generate `secrets.token_hex(32)`, open a local log, start `[sys.executable, "-m", "tokenhub", "_serve", str(port)]` with the token in a private environment variable, and write the record. On POSIX use `start_new_session=True`; on Windows use `creationflags=subprocess.DETACHED_PROCESS`. Redirect standard streams to the local log, never to a pipe the parent leaves unread.

~~~python
self.data_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
with FileLock(str(self.data_directory / "runtime.lock"), timeout=10):
    existing = read_record(self.data_directory)
    if existing and probe(existing):
        if port is not None and port != existing.port:
            raise RuntimeError("Token Hub is already running on another port")
        return f"http://127.0.0.1:{existing.port}/"
    selected_port = DEFAULT_PORT if port is None else port
    token = secrets.token_hex(32)
    child_env = {**os.environ, "TOKENHUB_INTERNAL_CONTROL_TOKEN": token}
    log_path = self.data_directory / "runtime.log"
    log_fd = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(log_fd, "ab", buffering=0) as log_file:
        child = subprocess.Popen(
            [sys.executable, "-m", "tokenhub", "_serve", str(selected_port)],
            env=child_env, stdin=subprocess.DEVNULL, stdout=log_file,
            stderr=subprocess.STDOUT, close_fds=True,
            start_new_session=os.name != "nt",
            creationflags=subprocess.DETACHED_PROCESS if os.name == "nt" else 0,
        )
~~~
- [ ] **Step 4: Implement readiness and failure cleanup.** Poll `probe(record)` for at most 10 seconds, checking `Popen.poll()` between attempts. On failure, terminate only that newly created `Popen` handle, wait for it, remove its matching record, and report the log path. `status()` reads the record and returns its loopback URL only if `probe` verifies the token.
- [ ] **Step 5: Run manager tests and checks.** Run `uv run pytest tests/runtime/test_manager.py -q`, `uv run ruff check backend tests`, and `uv run mypy backend`.
- [ ] **Step 6: Commit.** Commit as `feat: launch one background Token Hub server`.

### Task 5: Expose start, status, open, and stop commands

**Files:**
- Modify: `backend/tokenhub/runtime/manager.py`, `backend/tokenhub/cli.py`, `tests/api/test_settings_and_cli.py`
- Create: `backend/tokenhub/__main__.py`
- Test: `tests/runtime/test_manager.py`

**Interfaces:**
- Adds `RuntimeManager.stop() -> bool`. It sends the private stop request only after `probe` succeeds and waits for readiness to disappear.
- Produces `main(argv: Sequence[str] | None = None) -> int` for the console script and module invocation.

- [ ] **Step 1: Write failing CLI and shutdown tests.** Verify `tokenhub` and `tokenhub start` use background start, `--no-open` suppresses browser opening, `status` prints the recorded URL, `open` on a stopped instance exits with guidance, repeat `stop` is harmless, browser failure prints the URL and returns success, and a reused PID never receives a signal or an unverified stop request.

~~~python
manager = SimpleNamespace(
    start=lambda port=None: "http://127.0.0.1:7432/",
    status=lambda: "http://127.0.0.1:7432/",
    stop=lambda: True,
)
monkeypatch.setattr(cli, "RuntimeManager", lambda _: manager)
monkeypatch.setattr(cli.webbrowser, "open", lambda _: False)
assert cli.main([]) == 0
assert "http://127.0.0.1:7432/" in capsys.readouterr().out
assert cli.main(["start", "--no-open"]) == 0
~~~
- [ ] **Step 2: Run those tests and confirm they fail.** Run `uv run pytest tests/api/test_settings_and_cli.py tests/runtime/test_manager.py -q`.
- [ ] **Step 3: Implement `stop` under the runtime lock.** Read the record; if `probe` fails, return `False` without sending a stop request. If verified, call `request_stop`, poll until the challenge stops responding, then remove the matching record. A failed stop request raises a clear error and retains the record.
- [ ] **Step 4: Replace the foreground-only CLI with `argparse` command routing.** Keep `_serve` hidden from help. The child reads its control token from `TOKENHUB_INTERNAL_CONTROL_TOKEN`, removes it from its environment, and calls `run_server`. Parent commands call the manager. `webbrowser.open(url)` runs only after `start` returns readiness or `open` verifies a running instance; failure leaves the URL printed.

~~~python
def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "_serve":
        token = os.environ.pop("TOKENHUB_INTERNAL_CONTROL_TOKEN")
        run_server(TokenHubSettings(port=int(args[1])), token)
        return 0
    parser = argparse.ArgumentParser(prog="tokenhub")
    parser.add_argument("command", nargs="?", choices=("start", "status", "open", "stop"), default="start")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-open", action="store_true")
    options = parser.parse_args(args)
    manager = RuntimeManager(TokenHubSettings().data_directory)
    if options.command == "stop":
        print("Token Hub stopped" if manager.stop() else "Token Hub is not running")
        return 0
    if options.command == "status":
        url = manager.status()
        print(url if url else "Token Hub is not running")
        return 0 if url else 1
    if options.command == "open":
        url = manager.status()
        if url is None:
            print("Token Hub is not running; run tokenhub start")
            return 1
    else:
        url = manager.start(options.port)
    print(url)
    if options.command == "open" or not options.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return 0
~~~
- [ ] **Step 5: Add the module entry point and run checks.** `backend/tokenhub/__main__.py` calls `raise SystemExit(main())`. Run `uv run pytest tests/api/test_settings_and_cli.py tests/runtime -q`, `uv run ruff check backend tests`, and `uv run mypy backend`.
- [ ] **Step 6: Commit.** Commit as `feat: manage the local dashboard from tokenhub CLI`.

### Task 6: Verify real distribution artifacts on all target systems

**Files:**
- Create: `LICENSE`, `scripts/verify_distribution.py`, `.github/workflows/package.yml`
- Modify: `pyproject.toml`, `README.md`
- Test: `tests/release/test_verify_distribution.py`

**Interfaces:**
- Produces a reproducible release check that inspects the wheel and source archive, installs each in an isolated environment, and runs the actual CLI against a temporary home.
- The MIT license copyright line is `Copyright (c) 2026 Aldrin Joseph`.

- [ ] **Step 1: Write the failing archive test.** Create a tiny synthetic wheel without web files and assert the verifier reports the missing index:

~~~python
def test_archive_requires_bundled_index(tmp_path: Path) -> None:
    wheel = tmp_path / "tokenhub-0.1.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("tokenhub/__init__.py", "")
    with pytest.raises(AssertionError, match="tokenhub/web/index.html"):
        verify_archive(wheel)
~~~

- [ ] **Step 2: Run the new test and confirm it fails because `verify_archive` is absent.** Run `uv run pytest tests/release/test_verify_distribution.py -q`.
- [ ] **Step 3: Implement archive checks in `scripts/verify_distribution.py`.** Inspect both wheel and source archive for `tokenhub/web/index.html`, one JS and CSS asset, `tokenhub/database/migrations/versions/0005_usage_metadata.py`, the entry point, and the license. Normalize the source archive's leading project directory before checking:

~~~python
def verify_archive(path: Path) -> None:
    if path.suffix == ".whl":
        with ZipFile(path) as archive:
            members = archive.namelist()
    else:
        with tarfile.open(path, "r:gz") as archive:
            members = [name.split("/", 1)[1] for name in archive.getnames() if "/" in name]
        members = [name.removeprefix("backend/") for name in members]
    assert "tokenhub/web/index.html" in members, "missing tokenhub/web/index.html"
    assert any(name.startswith("tokenhub/web/assets/") and name.endswith(".js") for name in members)
    assert any(name.startswith("tokenhub/web/assets/") and name.endswith(".css") for name in members)
    assert "tokenhub/database/migrations/versions/0005_usage_metadata.py" in members
    assert any(name.endswith("/entry_points.txt") for name in members)
    assert any(name.endswith("/LICENSE") or name == "LICENSE" for name in members)
~~~

- [ ] **Step 4: Add the canonical [OSI MIT license](https://opensource.org/license/mit) and project metadata.** Add `license = "MIT"` and `license-files = ["LICENSE"]` to `[project]`, using the approved copyright line in `LICENSE`. Keep `requires-python = ">=3.12"` and the existing `tokenhub` console entry point.
- [ ] **Step 5: Extend the verifier with an installed-package smoke run.** For each artifact, create a clean `venv` outside the checkout, install with that environment's pip, set `HOME` and `USERPROFILE` to a temporary directory, choose a free loopback port, and run `tokenhub start --port PORT --no-open`. Assert root HTML, one referenced asset, `/api/v1/status`, SQLite's Alembic head, `status`, repeat start, and `stop`. Exercise `main(["open"])` in the installed environment with `webbrowser.open` stubbed, so CI does not launch a real browser. Always stop the child in a `finally` block.
- [ ] **Step 6: Add the cross-platform CI workflow.** Matrix: `ubuntu-latest`, `macos-latest`, `windows-latest` with Python 3.12 and Node for the build stage. Run `npm ci`, frontend tests, `npm run build`, and fail when `git status --porcelain -- backend/tokenhub/web` is nonempty. Run Python tests, integration tests, Ruff, mypy, `uv build`, archive verification, and the installed smoke script for wheel and source archive.
- [ ] **Step 7: Update the README and run the release check locally.** Document `pip install tokenhub`, `tokenhub`, `status/open/stop`, `--no-open`, `--port`, manual restart after reboot, local logs, and Python 3.12+. Run `uv run pytest -q`, `uv run pytest tests/integration -m integration -q`, `npm --prefix frontend run test -- --run`, `npm --prefix frontend run build`, `uv build`, and `python scripts/verify_distribution.py dist/*`.
- [ ] **Step 8: Commit.** Commit as `build: verify installable Token Hub releases`.

### Task 7: Prepare and publish the first public package

**Files:**
- Review: `pyproject.toml`, `LICENSE`, `README.md`, `dist/tokenhub-0.1.0-py3-none-any.whl`, `dist/tokenhub-0.1.0.tar.gz`
- Create or modify after account setup: `.github/workflows/publish.yml`

**Interfaces:**
- Produces a publicly installable `tokenhub==0.1.0` release from the reviewed artifacts.

- [ ] **Step 1: Confirm the `tokenhub` project name is accepted by PyPI.** Resolve any ownership or naming conflict before building the final versioned artifacts.
- [ ] **Step 2: Run all Task 6 checks on the final commit and retain the two artifacts and their checksums.** The Git tree and generated UI must be clean.
- [ ] **Step 3: Configure PyPI and TestPyPI trusted publishing for this repository.** The publish workflow uses GitHub's OIDC permission, release-scoped environments, and no stored upload token; validate its configuration with a release candidate on TestPyPI.
- [ ] **Step 4: Download the TestPyPI candidate artifacts, install each direct file in a fresh environment, and run the installed smoke check.** Confirm the downloaded wheel and source archive serve the dashboard and migration.
- [ ] **Step 5: Present the exact artifacts, checksums, version, TestPyPI result, and planned PyPI destination for final release review.** Public upload happens after that review.
- [ ] **Step 6: Publish `0.1.0` to PyPI and verify `pip install tokenhub==0.1.0` in a new environment.** Confirm the local dashboard starts, opens, and stops; link the published project in the release notes.

## Self-review checklist

- [x] Every spec acceptance criterion maps to Tasks 1–7.
- [x] The five Review Focus conditions have tests in Tasks 3–5.
- [x] The plan verifies that wheel and source archive contain the UI, migrations, license, and entry point.
- [x] Runtime control routes are registered before the static root mount.
- [x] Browser opening and public publishing occur only at the stated stages.
