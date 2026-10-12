"""The p10k scenario: the prompt dot follows the pointer in real Powerlevel10k.

Runs inside the disposable container (see :file:`conftest.py`). The image ships
a pinned Powerlevel10k and the committed minimal config next to this file; the
driver sources both before the user dotfiles. This is the only scenario that
asserts on the prompt text itself, because the prompt segment is the observable
feature under test: the ``●`` must appear while a recording is active and vanish
after ``stop``, and Powerlevel10k's instant prompt must stay silent.
"""

from pathlib import Path

import pytest
from e2eutil import Runner
from watchutil import P10K_THEME, Zsh

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("tmux_server")]

#: The prompt segment the integration renders while a recording is active.
DOT = "●"
#: Distinctive, case-insensitive text of Powerlevel10k's instant-prompt
#: console-output warning; stable across its quiet and verbose message variants.
INITIALIZATION_WARNING = "during zsh initialization"


def _install(runner: Runner, env: dict[str, str]) -> None:
    """Install shlib and the watch integration the real way."""
    runner("lion", "shlib", "install", env=env)
    runner("lion", "shell", "insert", env=env)


def test_p10k_dot_follows_pointer(runner: Runner, env: dict[str, str], tmp_path: Path) -> None:
    """The dot appears while recording, vanishes on stop and survives ``exec zsh``."""
    if not Path(P10K_THEME).is_file():
        pytest.fail(f"The E2E image is missing the pinned p10k theme: {P10K_THEME}")

    _install(runner, env)
    start = runner("lion", "watch", "start", "--note", "p10k e2e", env=env)
    assert "Recording started" in start

    shell = Zsh(env, tmp_path / "zdot", p10k=True)
    try:
        # Cold cache: the first real prompt renders the active segment. No
        # instant prompt exists yet, so no warning can appear — this step
        # asserts the segment and acts as a sanity net for load errors.
        first = shell.wait_for(DOT, present=True, timeout=15.0)
        assert DOT in first, repr(first)
        startup = shell.screen(scrollback=True)
        assert INITIALIZATION_WARNING not in startup.lower(), startup
        assert "warning" not in startup.lower(), startup

        # After stop the next prompt must drop the segment. A no-op Enter forces
        # the redraw; absence is observed over a bounded window.
        _ = runner("lion", "watch", "stop", env=env)
        shell.send(":\r")
        gone = shell.wait_for(DOT, present=False, timeout=3.0)
        assert DOT not in gone, repr(gone)

        # A fresh recording plus ``exec zsh``: the pointer is read when the
        # shell loads, so the segment reappears. This second zsh runs with a
        # warm instant-prompt cache, so Powerlevel10k now *would* print its
        # warning if any LION code performed console I/O during
        # initialization. Wait for the definitive post-init marker (not just a
        # settled screen, which a static instant prompt could fake) and then
        # read the whole screen (including scrollback) to assert both.
        _ = runner("lion", "watch", "start", "--note", "p10k exec", env=env)
        shell.send("exec zsh\r")
        assert shell.wait_ready(timeout=15.0)
        warm = shell.wait_settle(timeout=10.0, quiet=0.5)
        assert DOT in warm, repr(warm)
        warm_startup = shell.screen(scrollback=True)
        assert INITIALIZATION_WARNING not in warm_startup.lower(), warm_startup
        assert "warning" not in warm_startup.lower(), warm_startup
    finally:
        _ = runner.invoke("lion", "watch", "stop", env=env, expected=None)
        shell.close()


def test_p10k_detects_delayed_initialization_output(env: dict[str, str], tmp_path: Path) -> None:
    """Negative control: delayed console output during init must be detected.

    The warm instant prompt makes Powerlevel10k report console output that
    happens while the shell still initializes. This scenario proves the
    readiness signal and the screen read actually capture that warning, so the
    silent-load assertion in the main p10k scenario cannot pass vacuously.
    """
    if not Path(P10K_THEME).is_file():
        pytest.fail(f"The E2E image is missing the pinned p10k theme: {P10K_THEME}")

    (Path(env["HOME"]) / ".zshrc").write_text("sleep 0.3\nprint -r -- 'LION-NEGATIVE-CONTROL'\n")

    # The cold shell generates the instant-prompt cache; no warning yet because
    # the instant prompt is not active on the first run.
    cold = Zsh(env, tmp_path / "zdot-cold", p10k=True)
    try:
        assert cold.wait_ready(timeout=15.0)
    finally:
        cold.close()

    # The warm shell replays the cached instant prompt, so the delayed output is
    # console output during initialization and must be reported.
    warm = Zsh(env, tmp_path / "zdot-warm", p10k=True)
    try:
        assert warm.wait_ready(timeout=15.0)
        _ = warm.wait_settle(timeout=10.0, quiet=0.5)
        warm_startup = warm.screen(scrollback=True)
        assert INITIALIZATION_WARNING in warm_startup.lower(), warm_startup
    finally:
        warm.close()
