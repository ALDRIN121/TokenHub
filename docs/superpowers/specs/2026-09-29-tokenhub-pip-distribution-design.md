# Token Hub pip Distribution and Local Background Dashboard

**Status:** Reviewed and approved for implementation planning.

## Goal and user intent

A person with Python 3.12 or newer can install `tokenhub` from the public Python Package Index, run `tokenhub`, and see their own local dashboard. The command starts Token Hub in the background, opens the dashboard when it is ready, and returns control to the terminal. The server remains active after the terminal closes. The person starts it again manually after a reboot.

The first public release supports macOS, Linux, and Windows. The browser and API connect only to the loopback server on that person's computer. Existing source approval, privacy, Host, Origin, and no-CORS boundaries remain in force. Installing the package does not start a process or read provider data.

## Current state and distribution gap

The repository already defines the `tokenhub` console entry point and serves a built Vite UI when `frontend/dist` exists in a source checkout. The CLI currently runs Uvicorn in the foreground on `127.0.0.1:7432`.

A local build of version `0.1.0` showed that the wheel contains the entry point and Alembic migration Python files, but excludes the built UI and `backend/alembic.ini`. The source distribution also excludes those two runtime inputs. `backend/tokenhub/app.py` looks for the UI outside the installed Python package, and `backend/tokenhub/database/migrations.py` looks for `alembic.ini` outside it. Consequently, the existing artifacts do not satisfy the installed-dashboard goal.

## User-facing command contract

| Command | Behavior |
| --- | --- |
| `pip install tokenhub` | Installs Python dependencies, server code, migrations, and compiled UI. It does not start Token Hub. |
| `tokenhub` | Starts one background instance, waits for readiness, opens the local dashboard, prints its URL, and exits. If the instance already exists, it opens that dashboard instead. |
| `tokenhub start` | Explicit spelling of the default action. |
| `tokenhub start --no-open` | Starts or reuses the background instance and prints its URL without asking the operating system to open a browser. |
| `tokenhub status` | Reports whether the managed instance is running and its URL. It reports a crashed or stale instance as stopped. |
| `tokenhub open` | Opens the managed instance's dashboard; if stopped, it explains that `tokenhub start` is needed. |
| `tokenhub stop` | Gracefully stops the managed instance. Repeating it when already stopped is harmless. |

The default URL is `http://127.0.0.1:7432/`. A browser-launch failure leaves a healthy server running and prints the URL for manual opening. Startup reports success only after the specific process launched by the command is serving the dashboard and API. A different program occupying port 7432 produces a clear error; Token Hub does not open that program's page or silently choose another port. A documented `--port` option on `start` permits an alternate loopback port, and later `status`, `open`, and `stop` use the recorded port. If an instance is already running on another port, `start --port` reports that conflict instead of moving or duplicating the instance.

The process is not registered for login startup. A crash is reported by `status`; the user can run `tokenhub` again. The package does not install a system service, tray application, or public web server.

## Runtime architecture

The existing FastAPI application remains the sole API and UI server. A CLI lifecycle layer starts a detached child using the same installed Python interpreter, waits for process-specific readiness, and records its identity and port in the user's Token Hub data directory. It exposes the command contract above while keeping the server's foreground entry point private to the package. Foreground development and test execution remain possible.

Lifecycle state is stored under `~/.tokenhub` by default, separately from the SQLite usage database. Start operations are serialized so concurrent invocations cannot launch two managed instances. A stale instance record is cleared only after confirming that its recorded process is gone or has changed identity. `stop` verifies process identity before requesting graceful shutdown and never terminates a process solely because it has a reused PID. Runtime state and logs receive user-scoped file permissions where the operating system supports them. Background logs remain local and do not expose provider data through the API.

The server continues to bind only to a configured loopback host. The Host and same-origin write guards apply to the bundled UI and API exactly as they do in a source checkout. The lifecycle mechanism cannot turn provider discovery into approval or bypass the current import consent boundary.

## Package contents and build

The React/Vite build produces compiled assets inside `backend/tokenhub/web/`, which is committed with the repository and included as package data in both the wheel and source distribution. The Python server resolves that directory relative to its installed package. `npm` and Node are build-time tools for maintainers; neither is needed by a person installing the published wheel or source distribution. A release check rebuilds the UI and fails if the resulting assets differ from those committed in the package.

Alembic is configured from installed package paths rather than from the repository-level `backend/alembic.ini`. The migration Python files already present in the wheel stay inside the package. The application database and approved-source state stay in the user's data directory through package upgrades and removals.

The package declares its Python requirement, runtime dependencies, console entry point, MIT license, and release version in `pyproject.toml`. A root `LICENSE` file contains the MIT license text with `Copyright (c) 2026 Aldrin Joseph` and is included in release artifacts. The project name for the first upload is `tokenhub`; release preparation checks that PyPI accepts that name before upload.

## Failure behavior

- If the child exits before readiness, the start command returns an error, identifies the local log location, and leaves no live-instance record.
- If another program occupies the selected port, start returns an error and does not open a browser.
- If the browser cannot be opened, start still succeeds when the server is healthy and prints its URL.
- If a record is stale, status reports stopped and a subsequent start can replace the record.
- If process identity cannot be verified, stop refuses to signal it and explains how to resolve the stale record safely.
- Shutdown lets the existing application lifespan stop its collection thread and close its database resources.

## Verification and release

CI runs the existing Python, integration, and frontend checks, then rebuilds the UI and verifies committed asset freshness. It builds a wheel and source distribution, inspects both for the UI, migration files, license, and entry point, and installs each artifact into a clean environment without Node. An installed-package smoke test uses an isolated home and free loopback port, then verifies start, readiness, root UI, a static asset, API status, database migration, status, repeat start, open, and stop.

Lifecycle tests cover concurrent start, occupied port, early child failure, browser-launch failure, stale and reused process identifiers, graceful stop, and a clean retry. CI exercises the installed package on macOS, Linux, and Windows with Python 3.12. The release documentation gives the two-command path, the local URL, the management commands, and the Python environment requirements.

Release preparation builds and checks the artifacts, then validates an installation from TestPyPI after the release candidate is reviewed. Public PyPI upload is the final reviewed release action, after the artifacts and installed-package results are available to inspect. No publishing credential is stored in the repository; a trusted publishing setup is preferred when the project's PyPI ownership is ready.

## Alternatives considered

- A foreground-only CLI would require a terminal to remain open and misses the requested background behavior.
- Registering launchd, systemd, and Windows login services would add operating-system installation and automatic restart behavior the user did not request.
- Building the UI during each user's pip installation would require Node on their machine. Bundling the compiled assets gives the requested pip experience.
- Generating assets only in a release job would leave a clean source checkout unable to build the same complete package. Committed assets with a freshness check keep source and published builds aligned.

## Acceptance criteria

1. On macOS, Linux, and Windows, a clean install of the published distribution starts and opens the local dashboard with `tokenhub` while the server remains active after the command exits.
2. Both wheel and source distribution contain the compiled UI, migrations, and MIT license; installed users do not need Node or the source repository.
3. `status`, `open`, and `stop` manage only the user's verified Token Hub instance; duplicate starts do not create duplicate servers.
4. The dashboard and API retain the existing loopback, Host, Origin, CORS, and provider-approval boundaries.
5. The installed-package smoke test and cross-platform checks pass before public upload.
