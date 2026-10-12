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
from watchutil import P10K_THEME, Zsh, strip_ansi

pytestmark = pytest.mark.e2e

#: The prompt segment the integration renders while a recording is active.
DOT = "●"
#: Distinctive text of Powerlevel10k's instant-prompt startup warning.
INSTANT_PROMPT_WARNING = "Console output during zsh initialization"


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
        first = strip_ansi(shell.wait_for(DOT, present=True, timeout=15.0))
        assert DOT in first, repr(first)
        assert INSTANT_PROMPT_WARNING not in first, first
        assert "warning" not in first.lower(), first

        # After stop the next prompt must drop the segment. A no-op Enter forces
        # the redraw; absence is observed over a bounded window.
        _ = runner("lion", "watch", "stop", env=env)
        shell.send(":\r")
        gone = strip_ansi(shell.wait_for(DOT, present=False, timeout=3.0))
        assert DOT not in gone, repr(gone)

        # A fresh recording plus ``exec zsh``: the pointer is read when the
        # shell loads, so the segment reappears. This second zsh runs with a
        # warm instant-prompt cache, so Powerlevel10k now *would* print its
        # warning if any LION code performed console I/O during
        # initialization. Read the whole startup window and assert both.
        _ = runner("lion", "watch", "start", "--note", "p10k exec", env=env)
        shell.send("exec zsh\r")
        warm = strip_ansi(shell.wait_for(DOT, present=False, timeout=4.0))
        assert DOT in warm, repr(warm)
        assert INSTANT_PROMPT_WARNING not in warm, warm
        assert "warning" not in warm.lower(), warm
    finally:
        _ = runner.invoke("lion", "watch", "stop", env=env, expected=None)
        shell.close()
