"""A pty driver for the real interactive zsh used by the watch E2E scenarios.

Every scenario must run inside the disposable container started by
:file:`scripts/test-e2e.sh`. The driver starts ``zsh -i`` behind a pseudo
terminal with a sentinel prompt and an isolated ``HOME``/``ZDOTDIR``. Results are
never parsed from the pty text: the scenarios read the recording and spool files
instead, so prompt and echo noise cannot contaminate assertions. The pty text is
only used to detect that the shell reached its prompt.
"""

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path

#: Sentinel for the primary prompt; the shell prints nothing else on load.
PROMPT = "<<LIONP>>"
#: Sentinel for the continuation prompt.
PROMPT2 = "<<LIONP2>>"


class Zsh:
    """An interactive zsh behind a pty with a deterministic prompt."""

    def __init__(self, env: dict[str, str], zdotdir: Path) -> None:
        """Start ``zsh -i`` with the child environment of an E2E scenario.

        ``zdotdir`` receives a small wrapper that sources the real ``HOME``
        dotfiles (and with them the shlib watch script) and then sets the
        sentinel prompts, so no global or user prompt can clobber them. The
        caller's environment is never modified.
        """
        home = env["HOME"]
        zdotdir.mkdir(parents=True, exist_ok=True)
        (zdotdir / ".zshenv").write_text('[[ -r "$HOME/.zshenv" ]] && source "$HOME/.zshenv"\n')
        (zdotdir / ".zshrc").write_text(
            f"[[ -r \"$HOME/.zshrc\" ]] && source \"$HOME/.zshrc\"\nPROMPT='{PROMPT}'\nPROMPT2='{PROMPT2}'\n"
        )
        child_env = {
            **env,
            "HOME": home,
            "ZDOTDIR": str(zdotdir),
            "TERM": "dumb",
        }
        for noisy in ("ZSH_VERSION", "ZSH_NAME", "SHLVL"):
            child_env.pop(noisy, None)
        self.master, slave = pty.openpty()
        _ = fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        self._process = subprocess.Popen(
            ["zsh", "-i"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=child_env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave)
        self._closed = False

    @property
    def pid(self) -> int:
        """Return the shell's process id."""
        return self._process.pid

    def alive(self) -> bool:
        """Return whether the shell process is still running."""
        return self._process.poll() is None

    def _read_chunk(self, timeout: float) -> bytes | None:
        """Read one chunk within ``timeout``; ``None`` means nothing arrived."""
        ready, _, _ = select.select([self.master], [], [], max(0.0, timeout))
        if not ready:
            return None
        try:
            return os.read(self.master, 65536)
        except OSError:
            return b""

    def wait_prompt(self, timeout: float = 15.0) -> str:
        """Read until the primary prompt sentinel appears; return the raw text."""
        end = time.time() + timeout
        text = ""
        while time.time() < end:
            chunk = self._read_chunk(min(0.2, max(0.0, end - time.time())))
            if chunk is None:
                continue
            if chunk == b"":
                break
            text += chunk.decode("utf-8", "replace")
            if PROMPT in text.split(PROMPT2)[-1]:
                break
        return text

    def send_raw(self, data: bytes) -> None:
        """Write raw bytes to the pty without waiting."""
        os.write(self.master, data)

    def send(self, data: str) -> None:
        """Write text to the pty without waiting."""
        os.write(self.master, data.encode("utf-8"))

    def drain(self, seconds: float = 0.5) -> str:
        """Read whatever arrives within ``seconds`` without waiting for a prompt."""
        end = time.time() + seconds
        text = ""
        while time.time() < end:
            chunk = self._read_chunk(max(0.0, end - time.time()))
            if chunk is None or chunk == b"":
                break
            text += chunk.decode("utf-8", "replace")
        return text

    def run(self, command: str, timeout: float = 15.0) -> str:
        """Send one Enter-terminated line and read until the next prompt."""
        self.send(command + "\r")
        return self.wait_prompt(timeout)

    def paste_and_run(self, text: str) -> str:
        """Simulate a bracketed multiline paste followed by Enter."""
        self.send_raw(b"\x1b[200~" + text.encode("utf-8") + b"\x1b[201~")
        _ = self.drain(0.2)
        self.send_raw(b"\r")
        return self.wait_prompt()

    def signal(self, sig: signal.Signals = signal.SIGHUP) -> None:
        """Send a signal to the shell process."""
        os.kill(self.pid, sig)

    def close(self, grace: float = 5.0) -> None:
        """Exit the shell gracefully, then force-kill it if it ignores ``exit``."""
        if self._closed:
            return
        self._closed = True
        if self.alive():
            self.send("exit\r")
            try:
                self._process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=grace)
        os.close(self.master)

    def __enter__(self) -> "Zsh":
        """Return the driver for use as a context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the shell when leaving the context."""
        self.close()
