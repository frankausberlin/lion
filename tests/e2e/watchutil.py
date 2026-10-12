"""A tmux driver for the real interactive zsh used by the watch E2E scenarios.

Every scenario must run inside the disposable container started by
:file:`scripts/test-e2e.sh`. A tmux server (started by :file:`conftest.py` with
:file:`tmux.conf`) hosts one session per :class:`Zsh`; each session runs
``zsh -i`` in a real terminal emulator with an isolated ``HOME``/``ZDOTDIR``.
Two prompt modes exist:

* the default mode appends a monotonically increasing sequence number to a
  sentinel ``PROMPT`` so the shell's readiness is detected exactly — even across
  ``exec zsh``, because the counter lives in a file (see :meth:`Zsh.wait_prompt`);
* the ``p10k`` mode loads the real Powerlevel10k from the image. Powerlevel10k
  manages the prompt itself, so no sentinel may be set; the driver waits for
  observable output such as the ``●`` segment instead.

Results are never parsed from the terminal text: the scenarios read the recording
and spool files instead, so prompt and echo noise cannot contaminate assertions.
The screen is only used to detect readiness, to return one command's own output
and — for the p10k scenario — the presence of the prompt segment itself.
"""

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

#: Sentinel for the primary prompt; the sequence number is appended at runtime.
PROMPT = "<<LIONP>>"
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
_SEQ_RE = re.compile(r"<<LIONP:(\d+)>>")

#: Monotonic counter so every session gets a unique, tmux-safe name.
_session_counter = 0


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and carriage returns from ``text``.

    The returned string keeps every printable character, so a prompt segment
    such as ``●`` survives while colour and hyperlink codes do not.
    """
    return _ANSI_RE.sub("", text).replace("\r", "")


def _tmux(*args: str, stdin: str | None = None, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    """Run one tmux client command on the default socket and capture its output."""
    return subprocess.run(
        ["tmux", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


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

    def __init__(self, env: dict[str, str], zdotdir: Path, p10k: bool = False) -> None:
        """Start ``zsh -i`` in a fresh tmux session with the scenario environment.

        ``zdotdir`` receives a small wrapper that sources the real ``HOME``
        dotfiles (and with them the shlib watch script). In the default mode it
        then installs a ``precmd`` hook that renders the sentinel prompt with a
        per-prompt sequence number, so readiness is exact and survives
        ``exec zsh``. With ``p10k=True`` the wrapper mirrors a real p10k
        ``.zshrc``: it sources the instant-prompt preamble, then the real
        Powerlevel10k theme and the committed p10k config, and only then the user
        dotfiles. No prompt is set, because Powerlevel10k owns the prompt and the
        sentinel would break its rendering. The caller's environment is never
        modified.
        """
        global _session_counter
        _session_counter += 1
        self._session = f"lion-e2e-{os.getpid()}-{_session_counter}"
        self._p10k = p10k
        self._prompt_seq = -1
        self._closed = False

        home = env["HOME"]
        zdotdir.mkdir(parents=True, exist_ok=True)
        (zdotdir / ".zshenv").write_text('[[ -r "$HOME/.zshenv" ]] && source "$HOME/.zshenv"\n')
        seq_file = zdotdir / ".lion-prompt-seq"
        if p10k:
            (zdotdir / ".zshrc").write_text(
                'if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then\n'
                '  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"\n'
                "fi\n"
                f"source {P10K_THEME}\n"
                f"source {P10K_CONFIG}\n"
                '[[ -r "$HOME/.zshrc" ]] && source "$HOME/.zshrc"\n'
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
                '  PROMPT="<<LIONP:${n}>>"\n'
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
        result = _tmux("-f", TMUX_CONFIG, *session_args)
        if result.returncode != 0:
            raise RuntimeError(f"tmux new-session failed ({result.returncode}): {result.stderr.strip()}")
        # Pin this window to 80x24 with window-size manual so an attaching client
        # cannot resize it mid-test (a global manual size crashes session
        # creation in tmux 3.4/3.6; see tmux.conf).
        _tmux("resize-window", "-t", self._session, "-x", "80", "-y", "24")

    @property
    def pid(self) -> int:
        """Return the shell's process id (stable across ``exec zsh``)."""
        result = _tmux("list-panes", "-t", self._session, "-F", "#{pane_pid}")
        if result.returncode != 0:
            raise RuntimeError(f"tmux list-panes failed: {result.stderr.strip()}")
        return int(result.stdout.strip().splitlines()[0])

    def alive(self) -> bool:
        """Return whether the shell process is still running."""
        if _tmux("has-session", "-t", self._session).returncode != 0:
            return False
        result = _tmux("list-panes", "-t", self._session, "-F", "#{pane_dead}")
        return result.returncode == 0 and result.stdout.strip() == "0"

    def _capture(self, scrollback: bool = False) -> str:
        """Return the stripped pane text, optionally including the scrollback."""
        args = ["capture-pane", "-p", "-J", "-t", self._session]
        if scrollback:
            args += ["-S", "-"]
        result = _tmux(*args)
        if result.returncode != 0:
            return ""
        return strip_ansi(result.stdout)

    def screen(self, scrollback: bool = False) -> str:
        """Return the current pane text for a screen assertion."""
        return self._capture(scrollback=scrollback)

    def wait_prompt(self, timeout: float = 15.0) -> str:
        """Poll the pane until a prompt with a new sequence number renders.

        Returns the visible pane text. The sequence number makes readiness exact
        even when the previous prompt is still on screen, and it keeps increasing
        across ``exec zsh`` because it is read from a file.
        """
        deadline = time.time() + timeout
        while True:
            screen = self._capture()
            sequences = [int(match) for match in _SEQ_RE.findall(screen)]
            newest = max(sequences) if sequences else -1
            if newest > self._prompt_seq:
                self._prompt_seq = newest
                return screen
            if time.time() >= deadline:
                return screen
            time.sleep(0.05)

    def wait_for(self, text: str, present: bool = True, timeout: float = 8.0) -> str:
        """Poll the current prompt until ``text`` appears, or until ``timeout``.

        Only the current prompt line is inspected, so a stale copy of ``text``
        that is still on screen cannot satisfy the condition. With
        ``present=False`` the method keeps polling for the full window and then
        returns the last prompt line: absence can only be observed over a
        bounded window. The returned text is already stripped of ANSI escapes.
        """
        deadline = time.time() + timeout
        while True:
            line = _prompt_line(self._capture())
            if present and text in line:
                return line
            if time.time() >= deadline:
                return line
            time.sleep(0.05)

    def _send_literal(self, text: str) -> None:
        """Send literal text to the pane without pressing Enter."""
        if text:
            _tmux("send-keys", "-t", self._session, "-l", text)

    def _send_enter(self) -> None:
        """Press Enter in the pane."""
        _tmux("send-keys", "-t", self._session, "Enter")

    def _fresh_output(self, previous: int, fallback: str) -> str:
        """Return the pane text after the prompt with sequence ``previous``.

        The unique prompt markers make this exact: everything after the previous
        prompt is the echoed command, its output and the new prompt. Falls back
        to the visible screen when the marker has scrolled out of the history.
        """
        if previous < 0:
            return fallback
        marker = f"<<LIONP:{previous}>>"
        full = self._capture(scrollback=True)
        index = full.rfind(marker)
        if index < 0:
            return fallback
        return full[index + len(marker) :]

    def _wait_settle(self, timeout: float = 8.0, quiet: float = 0.3) -> str:
        """Poll the pane until its text stops changing for ``quiet`` seconds.

        Used in p10k mode, where no sentinel prompt is available.
        """
        deadline = time.time() + timeout
        last = self._capture()
        stable_since = time.time()
        while time.time() < deadline:
            time.sleep(0.1)
            current = self._capture()
            if current != last:
                last = current
                stable_since = time.time()
            elif time.time() - stable_since >= quiet:
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
            return self._wait_settle(timeout)
        previous = self._prompt_seq
        self.send(command)
        screen = self.wait_prompt(timeout)
        return self._fresh_output(previous, screen)

    def paste_and_run(self, text: str, timeout: float = 15.0) -> str:
        """Simulate a bracketed multiline paste followed by Enter."""
        buffer = f"lion-paste-{self._session}"
        _tmux("load-buffer", "-b", buffer, "-", stdin=text)
        _tmux("paste-buffer", "-p", "-b", buffer, "-t", self._session)
        _tmux("delete-buffer", "-b", buffer)
        previous = self._prompt_seq
        self._send_enter()
        screen = self.wait_prompt(timeout)
        return self._fresh_output(previous, screen)

    def close_window(self) -> None:
        """Close the terminal window, which hangs up the shell (SIGHUP)."""
        _tmux("kill-window", "-t", self._session)

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
        _tmux("kill-session", "-t", self._session)
        sessions = _tmux("list-sessions", "-F", "#{session_name}")
        if sessions.returncode != 0 or not sessions.stdout.strip():
            _tmux("kill-server")
        if pid is not None:
            deadline = time.time() + grace
            while Path(f"/proc/{pid}").exists() and time.time() < deadline:
                time.sleep(0.05)

    def __enter__(self) -> "Zsh":
        """Return the driver for use as a context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the shell when leaving the context."""
        self.close()
