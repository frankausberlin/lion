# End-to-end tests

This suite exercises the installed `lion` executable with real external tools
in a disposable Ubuntu 24.04 Docker container. General testing rules live in
[AGENTS.md](../../AGENTS.md#testing).

## Prerequisites and execution

Run from the repository root with Bash, `just`, Docker and an accessible,
running Docker daemon. The image build needs network access to fetch base
images, OS packages and the locked Python dependencies. Python and Zsh for
these tests are installed inside the image.

The build requires BuildKit (the default since Docker 23) for its cache
mounts; the legacy builder is not supported (`DOCKER_BUILDKIT=0` fails on the
`RUN --mount` steps). The image installs only the `e2e` dependency group
(`pytest`), not the `dev` group, so the heavy development tools never enter the
test container.

```bash
just test-e2e
```

No separate manual image setup is needed. Each invocation runs
[scripts/test-e2e.sh](../../scripts/test-e2e.sh), which:

1. Creates a unique `e2e-artifacts.*` directory in the repository root.
2. Builds the `lion-e2e` image using the local source,
   [Dockerfile](Dockerfile) and `uv.lock`; Docker may reuse cached layers. The
   dependency layer is copied and installed before `src/`, so a source change
   only rebuilds the project and test layers.
3. Creates a fresh container with networking disabled and
   `LION_E2E_CONTAINER=1`, without host mounts or privileged mode.
4. Runs pytest against `tests/e2e` with the `e2e` marker, temporary files
   under `/artifacts/work` and a JUnit report at `/artifacts/junit.xml`.
5. Copies `/artifacts` to the host diagnostics directory and removes the
   container on exit, including after test failures when a container exists.

The image remains available for subsequent builds. Re-run `just test-e2e`
after changing code or tests so the image contains the current files.

## Isolation

Package installation and removal happen only in the container's package
database. The package scenario requires root **inside the disposable
container**; Lion itself does not require root. The permissions scenarios
switch to the dedicated unprivileged `lion-e2e` user
using `runuser --preserve-environment`; only their temporary directories are
owned by that user. Package mutation remains root-only. Shlib and the
`doctor` scenarios
use a temporary `HOME` and `ZDOTDIR`, and package-history and `doctor` tests use
a temporary `XDG_DATA_HOME`.

Run this suite only through `just test-e2e`. Do not enable the opt-in flag
manually on a workstation or execute the package mutation steps there.
The tests check the opt-in flag and Docker marker; the package test also
checks the effective user. These checks prevent accidental execution and
must remain in new scenarios.

Ordinary `pytest`, `just test` and `just check` exclude the `e2e` marker.
The Docker command explicitly selects it. The opted-in container run fails
if any scenario is skipped; missing root
privileges or a missing Docker marker are errors. Outside the runner, guards
skip the suite to prevent accidental system mutations.

## Existing scenarios

| Test | Behavior checked |
| --- | --- |
| [Package lifecycle](test_package_lifecycle.py) | Builds two local dependency-free package versions without maintainer scripts; scans the baseline, installs, upgrades, purges and scans each state. Checks structural changes for install/removal and a nonstructural version change for upgrade. Checks exact package changes, create/confirm/append behavior, persisted TOML, stable history references, diff resolution and unchanged files after read-only commands. |
| [Shlib lifecycle](test_shlib_lifecycle.py) | Installs and uninstalls with real Zsh in a temporary home. Checks literal export values, linked scripts, load order, installer additions, syntax-failure handling, file permissions and repeated uninstall. |
| [Doctor environment](test_doctor_environment.py) | Runs `doctor` over a real filesystem: the deterministic bare profile, pure JSON, read-only preservation, reco permissions/content, hostile-filename safety, no-overwrite on a second run, damaged history and legacy `scans/`, and an unusable data path. |
| [Doctor permissions](test_doctor_permissions.py) | Runs scan, shlib installation and doctor as the unprivileged `lion-e2e` user. Checks preservation of existing history, shell files, exports and permissions; denied reco publication returns JSON/exit 1 without partial files. |
| [Runner diagnostics](test_runner.py) | A real subprocess timeout retains the command and partial stdout/stderr. |
| [Doctor shlib](test_doctor_shlib.py) | Installs the real shell library and inspects the resulting `shlib.*` findings: clean install, missing reference copy, drifted `.zshrc`, missing directory, `dash/` defects, ambiguous markers and a foreign `ZDOTDIR`. |

The package scenario retains four distinct history entries even when removal
restores the initial collector data. The shlib scenario compares shell behavior
before and after flattening.

Physical GPU discovery is outside this suite's coverage. The image deliberately
installs no `lspci` and no `nvidia-smi`, which keeps the `doctor` hardware and
`tools.lspci` findings deterministic; the environment scenario asserts that
both tools are absent, so a base-image change fails loudly. One consequence is
that a clean `ok`/`skip`-only `doctor` run is not reachable in this image, since
a missing `lspci` is always at least a `warn`; the "clean run writes nothing"
invariant therefore stays a unit test in `tests/test_doctor.py`.

## Results and troubleshooting

Read the pytest summary in the terminal and the final
`E2E diagnostics: <path>` line. Within that directory:

| Artifact | Purpose |
| --- | --- |
| `junit.xml` | Test results and assertion failures. |
| `work/**/commands.log` | Package-, shlib- and doctor-scenario commands, exit codes, stdout and stderr. |
| `work/**/user-commands.log` | Non-root doctor and shlib commands and output. |
| `work/**/data/lion/history/*.toml` | Persisted package-scenario snapshots. |
| Other files under `work/` | Temporary shell configuration, `recos/`, and package fixture files. |

If the build fails before container creation, the diagnostics directory may be
empty; use the terminal build output. For Docker connection errors, verify the
daemon and your access to it. For dependency download failures, check build-time
network access. Do not enable runtime networking to fix a build failure.

For assertion failures, start with the failing assertion and corresponding
command log, then inspect the saved snapshots or shell files. Fix the cause and
rerun the runner; do not remove isolation checks to make the test pass.

GitHub Actions runs this suite in a separate job on pull requests and pushes to
`main`, and uploads `e2e-artifacts.*` on failure. Locally, diagnostics are
gitignored and can be deleted after inspection.

## Adding a scenario

- Add a `test_*.py` file here with `pytestmark = pytest.mark.e2e`. The shared
  [conftest.py](conftest.py) guards every test with the container check and
  provides the `runner` (logged subprocess) and `env` (isolated child
  environment) fixtures; no extra imports are needed.
- Invoke the installed `lion` command as a subprocess. Use real tools for the
  lifecycle under test rather than mocking collectors or package-manager calls.
- Isolate writable state with `tmp_path` and pass environment overrides only
  to the relevant child processes. Use synthetic secret values, never real
  credentials.
- Keep fixtures self-contained and runnable without network access. Avoid
  dependencies between tests or assumptions about their execution order.
- Assert expected changes and preservation: exit codes, output, persisted
  state, permissions where relevant, and no writes from read-only commands.
- Give subprocesses timeouts and record their commands, exit codes and output
  below `tmp_path` so the runner retains useful diagnostics.
- If additional OS tools are required, add them to the Dockerfile's build step.
  Run `just check` and `just test-e2e`, then report the results and any
  remaining coverage limits.
