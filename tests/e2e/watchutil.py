"""A tmux driver for the real interactive zsh used by the watch E2E scenarios.

Every scenario must run inside the disposable container started by
:file:`scripts/test-e2e.sh`. A tmux server (started by :file:`conftest.py` with
:file:`tmux.conf`) hosts one session per :class:`Zsh`; each session runs
``zsh -i`` in a real terminal emulator with an isolated ``HOME``/``ZDOTDIR``.
Two prompt modes exist:

* the default mode appends a monotonically increasing sequence number to a
  sentinel prompt so the shell's readiness is detected exactly — even across
  ``exec zsh``, because the counter lives in a file (see :meth:`Zsh.wait_prompt`);
* the ``p10k`` mode loads the real Powerlevel10k from the image. Powerlevel10k
  manages the prompt itself, so no sentinel may be set; the driver waits for
  observable output such as the ``●`` segment instead.

Results are never parsed from the terminal text: the scenarios read the recording
and spool files instead, so prompt and echo noise cannot contaminate assertions.
The screen is only used to detect readiness, to return one command's own output
and — for the p10k scenario — the presence of the prompt segment itself. When a
scenario fails, every driver persists its session state, input steps and screen
below the scenario's ``tmp_path`` as pure diagnostics (:func:`dump_diagnostics`).

Failures are loud: a failed tmux command raises :class:`TmuxError` instead of
being ignored, and a missing prompt raises :class:`ZshTimeoutError` instead of
returning a stale screen.
"""

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

#: Sentinel prefix for the primary prompt; the per-prompt sequence number and a
#: closing ``>>`` are appended at runtime, e.g. ``<<LIONP:3>>``.
PROMPT_PREFIX = "<<LIONP:"
#: Sentinel for the continuation prompt.
PROMPT2 = "<<LIONP2>>"

#: Pinned Powerlevel10k theme inside the E2E image (see :file:`Dockerfile`).
P10K_THEME = "/opt/powerlevel10k/powerlevel10k.zsh-theme"
#: The committed, minimal p10k config that lives next to this module.
P10K_CONFIG = (Path(__file__).resolve().parent / "p10k" / "p10k.zsh").as_posix()
#: The committed tmux configuration; the server is started with it by conftest.
TMUX_CONFIG = (Path(__file__).resolve().parent / "tmux.conf").as_posix()

#: Matches ANSI escape sequences: CSI, OSC (hyperlinks) and single-char escapes.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
#: Matches the sentinel prompt with its sequence number, e.g. ``<<LIONP:3>>``.
_SEQ_RE = re.compile(re.escape(PROMPT_PREFIX) + r"(\d+)>>")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and carriage returns from ``text``.

    The returned string keeps every printable character, so a prompt segment
    such as ``●`` survives while colour and hyperlink codes do not.
    """
    return _ANSI_RE.sub("", text).replace("\r", "")


def strip_prompt(text: str) -> str:
    """Remove sentinel prompt markers (``<<LIONP:N>>``) from ``text``."""
    return _SEQ_RE.sub("", text)


#: Monotonic counter so every session gets a unique, tmux-safe name.
_session_counter = 0

#: Every driver created since the last :func:`reset_diagnostics`.
_drivers: list["Zsh"] = []


class TmuxError(RuntimeError):
    """A tmux client command failed."""


class ZshTimeoutError(RuntimeError):
    """The interactive shell did not reach the expected state in time."""


def reset_diagnostics() -> None:
    """Forget every driver; the suite calls this before each scenario."""
    _drivers.clear()


def dump_diagnostics() -> list[Path]:
    """Persist the terminal state of every driver of the current scenario."""
    paths: list[Path] = []
    for driver in _drivers:
        try:
            paths.append(driver.write_diagnostics())
        except OSError:
            continue
    return paths


def _tmux(
    *args: str,
    stdin: str | None = None,
    timeout: float = 15.0,
    check: bool = True,
    prefix: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one tmux client command and capture its output.

    ``prefix`` and ``env`` allow an unprivileged tmux server (the ``lion-e2e``
    user scenarios) to receive the client through ``runuser`` and its own
    ``TMUX_TMPDIR`` socket directory.

    Raises:
        TmuxError: If the command exits non-zero and ``check`` is true.
    """
    result = subprocess.run(
        [*prefix, "tmux", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=None if env is None else {**os.environ, **env},
    )
    if check and result.returncode != 0:
        raise TmuxError(f"tmux {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result


def _prompt_line(screen: str) -> str:
    """Return the last non-empty line of a captured pane: the current prompt.

    The prompt is always the bottom-most content after a command finished, so
    looking only at it ignores the echoed command and earlier prompts that are
    still on screen.
    """
    lines = [line for line in screen.splitlines() if line.strip()]
    return lines[-1] if lines else ""


class Zsh:
    """An interactive zsh inside its own tmux session with a deterministic prompt."""

    def __init__(
        self,
        env: dict[str, str],
        zdotdir: Path,
        p10k: bool = False,
        tmux_prefix: tuple[str, ...] = (),
        tmux_env: dict[str, str] | None = None,
    ) -> None:
        """Start ``zsh -i`` in a fresh tmux session with the scenario environment.

        ``zdotdir`` receives a small wrapper that sources the real ``HOME``
        dotfiles (and with them the shlib watch script). In the default mode it
        then installs a ``precmd`` hook that renders the sentinel prompt with a
        per-prompt sequence number, so readiness is exact and survives
        ``exec zsh``. With ``p10k=True`` the wrapper mirrors a real p10k
        ``.zshrc``: it sources the instant-prompt preamble, then the real
        Powerlevel10k theme and the committed p10k config, and only then the user
        dotfiles. No prompt is set, because Powerlevel10k owns the prompt and the
        sentinel would break its rendering. ``tmux_prefix``/``tmux_env`` drive an
        unprivileged tmux server (see :func:`_tmux`). The caller's environment is
        never modified.
        """
        global _session_counter
        _session_counter += 1
        self._session = f"lion-e2e-{os.getpid()}-{_session_counter}"
        self._p10k = p10k
        self._prompt_seq = -1
        self._closed = False
        self._tmux_prefix = tmux_prefix
        self._tmux_env = tmux_env
        self._steps: list[str] = []
        self._final_screen: str | None = None
        self._diagnostics_dir = zdotdir.parent / "shell-diagnostics"

        home = env["HOME"]
        zdotdir.mkdir(parents=True, exist_ok=True)
        (zdotdir / ".zshenv").write_text('[[ -r "$HOME/.zshenv" ]] && source "$HOME/.zshenv"\n')
        seq_file = zdotdir / ".lion-prompt-seq"
        ready_file = zdotdir / ".lion-p10k-ready"
        self._ready_file: Path | None = ready_file if p10k else None
        if p10k:
            # A definitive readiness signal: the precmd hook runs after every
            # dotfile and after Powerlevel10k initialized, right before the real
            # prompt. Writing a file (not the console) cannot itself trigger the
            # instant-prompt warning, so it does not disturb what the scenario
            # asserts; a settled screen, by contrast, can be a static instant
            # prompt while initialization is still running.
            (zdotdir / ".zshrc").write_text(
                'if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then\n'
                '  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"\n'
                "fi\n"
                f"source {P10K_THEME}\n"
                f"source {P10K_CONFIG}\n"
                '[[ -r "$HOME/.zshrc" ]] && source "$HOME/.zshrc"\n'
                "autoload -Uz add-zsh-hook\n"
                f"_lion_p10k_ready_file={shlex.quote(str(ready_file))}\n"
                "_lion_p10k_ready() { : > $_lion_p10k_ready_file }\n"
                "add-zsh-hook precmd _lion_p10k_ready\n"
            )
        else:
            (zdotdir / ".zshrc").write_text(
                '[[ -r "$HOME/.zshrc" ]] && source "$HOME/.zshrc"\n'
                "autoload -Uz add-zsh-hook\n"
                f"_lion_prompt_seq_file={shlex.quote(str(seq_file))}\n"
                "_lion_prompt_seq() {\n"
                "  local n=0\n"
                "  [[ -r $_lion_prompt_seq_file ]] && read -r n < $_lion_prompt_seq_file\n"
                "  (( n = n + 1 ))\n"
                "  print -r -- $n > $_lion_prompt_seq_file\n"
                '  PROMPT="' + PROMPT_PREFIX + '${n}>>"\n'
                "  return 0\n"
                "}\n"
                "add-zsh-hook precmd _lion_prompt_seq\n"
                f"PROMPT2='{PROMPT2}'\n"
            )

        session_args = [
            "new-session",
            "-d",
            "-s",
            self._session,
            "-x",
            "80",
            "-y",
            "24",
            "-e",
            f"HOME={home}",
            "-e",
            f"ZDOTDIR={zdotdir}",
            "-e",
            "LC_ALL=C",
            "-e",
            "LANG=C",
        ]
        if "XDG_DATA_HOME" in env:
            session_args += ["-e", f"XDG_DATA_HOME={env['XDG_DATA_HOME']}"]
        session_args += ["zsh", "-i"]
        self._tmux("-f", TMUX_CONFIG, *session_args)
        # Pin this window to 80x24 with window-size manual so an attaching client
        # cannot resize it mid-test (a global manual size crashes session
        # creation in tmux 3.4/3.6; see tmux.conf).
        self._tmux("resize-window", "-t", self._session, "-x", "80", "-y", "24")
        _drivers.append(self)

    def _tmux(
        self,
        *args: str,
        stdin: str | None = None,
        timeout: float = 15.0,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run one tmux client command for this session's server."""
        return _tmux(*args, stdin=stdin, timeout=timeout, check=check, prefix=self._tmux_prefix, env=self._tmux_env)

    @property
    def pid(self) -> int:
        """Return the shell's process id (stable across ``exec zsh``)."""
        result = self._tmux("list-panes", "-t", self._session, "-F", "#{pane_pid}")
        return int(result.stdout.strip().splitlines()[0])

    def alive(self) -> bool:
        """Return whether the shell process is still running."""
        if self._tmux("has-session", "-t", self._session, check=False).returncode != 0:
            return False
        result = self._tmux("list-panes", "-t", self._session, "-F", "#{pane_dead}", check=False)
        return result.returncode == 0 and result.stdout.strip() == "0"

    def _capture(self, scrollback: bool = False) -> str:
        """Return the stripped pane text, optionally including the scrollback.

        Raises:
            TmuxError: If the pane cannot be captured (for example after the
                session was destroyed); diagnostics handle that explicitly.
        """
        args = ["capture-pane", "-p", "-J", "-t", self._session]
        if scrollback:
            args += ["-S", "-"]
        return strip_ansi(self._tmux(*args).stdout)

    def screen(self, scrollback: bool = False) -> str:
        """Return the current pane text for a screen assertion."""
        return self._capture(scrollback=scrollback)

    def wait_prompt(self, timeout: float = 15.0) -> str:
        """Poll the pane until a prompt with a new sequence number renders.

        Returns the visible pane text. The sequence number makes readiness exact
        even when the previous prompt is still on screen, and it keeps increasing
        across ``exec zsh`` because it is read from a file.

        Raises:
            ZshTimeoutError: If no new prompt appears within ``timeout``; a
                silent stale screen would otherwise fake readiness.
        """
        deadline = time.monotonic() + timeout
        while True:
            screen = self._capture()
            sequences = [int(match) for match in _SEQ_RE.findall(screen)]
            newest = max(sequences) if sequences else -1
            if newest > self._prompt_seq:
                self._prompt_seq = newest
                return screen
            if time.monotonic() >= deadline:
                raise ZshTimeoutError(
                    f"No new prompt in tmux session {self._session} within {timeout:.0f}s; last screen:\n{screen}"
                )
            time.sleep(0.05)

    def wait_for(self, text: str, present: bool = True, timeout: float = 8.0) -> str:
        """Poll the current prompt until ``text`` appears, or until ``timeout``.

        Only the current prompt line is inspected, so a stale copy of ``text``
        that is still on screen cannot satisfy the condition. With
        ``present=False`` the method keeps polling for the full window and then
        returns the last prompt line: absence can only be observed over a
        bounded window. The returned text is already stripped of ANSI escapes.
        """
        deadline = time.monotonic() + timeout
        while True:
            line = _prompt_line(self._capture())
            if present and text in line:
                return line
            if time.monotonic() >= deadline:
                return line
            time.sleep(0.05)

    def wait_ready(self, timeout: float = 15.0) -> bool:
        """Wait until a p10k shell finished initialization, and report success.

        The marker is written by the wrapper's ``precmd`` hook, which runs after
        every dotfile and after Powerlevel10k initialized. Unlike a settled
        screen this cannot be satisfied by a static instant prompt while
        initialization is still running. Returns ``True`` for non-p10k shells.
        The marker survives ``exec zsh``; delete it with :meth:`reset_ready`
        before re-executing the shell.
        """
        if self._ready_file is None:
            return True
        deadline = time.monotonic() + timeout
        while not self._ready_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self._ready_file.exists()

    def reset_ready(self) -> None:
        """Delete the p10k readiness marker so the next generation must write it.

        The marker is a file, so after ``exec zsh`` the file of the previous
        shell generation would satisfy :meth:`wait_ready` immediately. Removing
        it before the ``exec`` makes the wait observe the new generation.
        """
        if self._ready_file is not None:
            self._ready_file.unlink(missing_ok=True)

    def _log_step(self, entry: str) -> None:
        """Record one input step for the failure diagnostics."""
        self._steps.append(f"{time.strftime('%H:%M:%S')} {entry}")

    def _send_literal(self, text: str) -> None:
        """Send literal text to the pane without pressing Enter."""
        if text:
            self._log_step(f"literal {text!r}")
            self._tmux("send-keys", "-t", self._session, "-l", text)

    def _send_enter(self) -> None:
        """Press Enter in the pane."""
        self._log_step("enter")
        self._tmux("send-keys", "-t", self._session, "Enter")

    def _fresh_output(self, previous: int, fallback: str) -> str:
        """Return the pane text after the prompt with sequence ``previous``.

        The unique prompt markers make this exact: everything after the previous
        prompt is the echoed command, its output and the new prompt. Falls back
        to the visible screen when the marker has scrolled out of the history.
        """
        if previous < 0:
            return fallback
        marker = f"{PROMPT_PREFIX}{previous}>>"
        full = self._capture(scrollback=True)
        index = full.rfind(marker)
        if index < 0:
            return fallback
        return full[index + len(marker) :]

    def wait_settle(self, timeout: float = 8.0, quiet: float = 0.3) -> str:
        """Poll the pane until its text stops changing for ``quiet`` seconds.

        Used in p10k mode, where no sentinel prompt is available, to wait until
        Powerlevel10k finished initialization before the screen is read.
        """
        deadline = time.monotonic() + timeout
        last = self._capture()
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(0.1)
            current = self._capture()
            if current != last:
                last = current
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= quiet:
                break
        return last

    def send(self, data: str) -> None:
        """Send one Enter-terminated line without waiting for a prompt."""
        self._send_literal(data.rstrip("\r\n"))
        self._send_enter()

    def run(self, command: str, timeout: float = 15.0) -> str:
        """Send one Enter-terminated line and return only its own output.

        In the default mode the return value starts right after the previous
        prompt, so a stale line on screen cannot satisfy an assertion. In p10k
        mode the return value is the settled visible screen.
        """
        if self._p10k:
            self.send(command)
            return self.wait_settle(timeout)
        previous = self._prompt_seq
        self.send(command)
        screen = self.wait_prompt(timeout)
        return self._fresh_output(previous, screen)

    def paste_and_run(self, text: str, timeout: float = 15.0) -> str:
        """Simulate a bracketed multiline paste followed by Enter."""
        buffer = f"lion-paste-{self._session}"
        self._log_step(f"paste {text!r}")
        self._tmux("load-buffer", "-b", buffer, "-", stdin=text)
        self._tmux("paste-buffer", "-p", "-b", buffer, "-t", self._session)
        self._tmux("delete-buffer", "-b", buffer)
        previous = self._prompt_seq
        self._send_enter()
        screen = self.wait_prompt(timeout)
        return self._fresh_output(previous, screen)

    def _remember_screen(self) -> None:
        """Capture the final screen once, tolerating an already destroyed session."""
        if self._final_screen is not None:
            return
        try:
            self._final_screen = self._capture(scrollback=True)
        except (TmuxError, subprocess.SubprocessError):
            self._final_screen = None

    def write_diagnostics(self) -> Path:
        """Write this shell's state, input steps and screen below its zdotdir.

        The file lands in ``<zdotdir>/../shell-diagnostics/``, which is inside
        the scenario's ``tmp_path`` and therefore part of the E2E artifact copy.
        It is pure diagnostics: assertions never read it, and it may contain
        synthetic test values only, never real credentials.
        """
        self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
        path = self._diagnostics_dir / f"{self._session}.txt"
        pane = self._tmux("list-panes", "-t", self._session, "-F", "#{pane_id} #{pane_pid} #{pane_dead}", check=False)
        pane_text = (
            pane.stdout.strip()
            if pane.returncode == 0 and pane.stdout.strip()
            else f"unavailable ({pane.stderr.strip() or pane.returncode})"
        )
        screen = self._final_screen
        if screen is None:
            try:
                screen = self._capture(scrollback=True)
            except (TmuxError, subprocess.SubprocessError) as exc:
                screen = f"<capture failed: {exc}>"
        lines = [
            f"session: {self._session}",
            f"pane: {pane_text}",
            f"closed: {self._closed}",
            "",
            "steps:",
            *self._steps,
            "",
            "screen+scrollback:",
            screen,
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def close_window(self, grace: float = 5.0) -> None:
        """Close the terminal window, which hangs up the shell (SIGHUP).

        Waits for the shell process to disappear before returning: ``kill-window``
        destroys the session immediately, so ``alive()`` (session state) is no
        longer a proxy for the process having handled SIGHUP and run ``zshexit``.
        """
        try:
            pid = self.pid
        except RuntimeError:
            pid = None
        self._remember_screen()
        _ = self._tmux("kill-window", "-t", self._session, check=False)
        if pid is not None:
            deadline = time.monotonic() + grace
            while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.05)

    def close(self, grace: float = 5.0) -> None:
        """Destroy the shell's session while it is still alive.

        tmux reaps a pane's process as it closes a *live* pane; sending ``exit``
        first (with ``remain-on-exit``) would leave the shell as a zombie that
        the segment-liveness check still reports as alive. The pane receives
        SIGHUP, so the integration's ``zshexit`` runs and records the exit event.
        When this was the last session, the server is stopped too.
        """
        if self._closed:
            return
        self._closed = True
        try:
            pid = self.pid
        except RuntimeError:
            pid = None
        self._remember_screen()
        _ = self._tmux("kill-session", "-t", self._session, check=False)
        sessions = self._tmux("list-sessions", "-F", "#{session_name}", check=False)
        if sessions.returncode != 0 or not sessions.stdout.strip():
            _ = self._tmux("kill-server", check=False)
        if pid is not None:
            deadline = time.monotonic() + grace
            while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.05)

    def __enter__(self) -> "Zsh":
        """Return the driver for use as a context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the shell when leaving the context."""
        self.close()
