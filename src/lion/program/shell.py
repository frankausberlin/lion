"""Insert, remove and inspect the LION watch integration in the user's shell.

The integration is a zsh script that appends shell events to the active
recording and loads the compiled watch-dog guard artifact (ADR 0014) when it is
present. It lives either as ``~/.shlib/shlibs/90-lion-watch.sh`` (when shlib
is installed) or as a marked block in ``~/.zshrc``. Exactly one artifact may
exist; inserting never overwrites, and removing requires a recognized marker.

Only ``insert`` and ``remove`` write, and only shell configuration after a
``zsh -n`` syntax check and a timestamped backup. ``status`` never writes.
"""

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from lion.program import recording, shlib
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.zshsyntax import validate_zsh

#: The marker line that identifies the delivered integration.
MARKER = "# LION watch integration (managed by 'lion shell insert')"

#: The block markers used when the integration is inlined into ``~/.zshrc``.
BLOCK_START = "# >>>>>> LION watch"
BLOCK_END = "# <<<<<< LION watch"

#: Script name inside ``~/.shlib/shlibs``; a high number loads after p10k config.
SCRIPT_NAME = "90-lion-watch.sh"

#: The delivered integration; the artifact must match it byte for byte.
INTEGRATION = r"""# LION watch integration (managed by 'lion shell insert')
#
# Appends shell events to the active recording. Without an active record the
# hooks are a no-op (one empty-variable test). Loading must stay silent so the
# Powerlevel10k instant prompt is not disturbed.

zmodload zsh/datetime 2>/dev/null

if (( ${+EPOCHREALTIME} )); then

  typeset -g LION_WATCH_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/lion/recordings"
  typeset -g LION_WATCH_POINTER="$LION_WATCH_DIR/active"
  # Keep an open segment and a pending command across a repeated load
  # (source ~/.zshrc); only a fresh shell starts with an empty state.
  typeset -g LION_WATCH_REF=${LION_WATCH_REF-}
  typeset -g LION_WATCH_SEG=${LION_WATCH_SEG-}
  typeset -g LION_WATCH_T0=${LION_WATCH_T0-}
  typeset -g LION_WATCH_MASKED=''
  typeset -g LION_WATCH_ESCAPED=''
  typeset -g LION_WATCH_CAP=${LION_WATCH_CAP:-4096}
  typeset -g LION_WATCH_P10K=''
  # Value pattern: double-quoted, single-quoted or bare; the '"'"' idiom only
  # protects the single quote from the outer single-quoted string.
  typeset -g LION_WATCH_NAME='[A-Za-z0-9_]#(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL)[A-Za-z0-9_]#'
  typeset -g LION_WATCH_VALUE='("([^"\\]#|\\?)#"|'"'"'[^'"'"']#'"'"'|[^[:space:];&|]#)'
  typeset -g LION_WATCH_PATTERN="(#bi)($LION_WATCH_NAME)=$LION_WATCH_VALUE"

  # Read the pointer only on the first load; a reload keeps the current ref and
  # leaves the switch detection to precmd.
  if [[ -z $LION_WATCH_REF && -r "$LION_WATCH_POINTER" ]]; then
    read -r LION_WATCH_REF < "$LION_WATCH_POINTER" 2>/dev/null
  fi

  # Mask likely secrets best-effort and write the result to LION_WATCH_MASKED.
  # LION_WATCH_MASK=0 opts out; masking is not a security boundary.
  _lion_watch_mask() {
    setopt local_options extended_glob
    local text=$1 low
    LION_WATCH_MASKED=$text
    (( ${LION_WATCH_MASK:-1} )) || return 0
    low=${text:l}
    [[ $low == *token* || $low == *secret* || $low == *password* || $low == *passwd* \
       || $low == *api_key* || $low == *apikey* || $low == *credential* || $low == *authorization* ]] || return 0
    text=${text//${~LION_WATCH_PATTERN}/${match[1]}=***}
    text=${text//(#bi)(Authorization:)[[:space:]]#((Basic|Bearer|Token)[[:space:]]#)?[^[:space:]]#/${match[1]} ***}
    LION_WATCH_MASKED=$text
    return 0
  }

  # Escape one value with ${(q)} and cap the field; writes LION_WATCH_ESCAPED.
  _lion_watch_esc() {
    local s=$1
    if (( ${#s} > LION_WATCH_CAP )); then
      s="${s[1,LION_WATCH_CAP]}\"...<+$(( ${#1} - LION_WATCH_CAP ))B>\""
    fi
    local q=${(q)s}
    LION_WATCH_ESCAPED=${q[1,LION_WATCH_CAP]}
  }

  # Append one TAB-separated event line to the current segment.
  _lion_watch_rec() {
    local line=$1 field
    shift
    for field in "$@"; do
      line+=$'\t'"$field"
    done
    print -r -- "$line" >> "$LION_WATCH_SEG" 2>/dev/null
  }

  # Create the segment file + header lazily on the first event of this shell.
  _lion_watch_start_segment() {
    [[ -n $LION_WATCH_REF ]] || return 1
    [[ -z $LION_WATCH_SEG ]] || return 0
    local line boot ticks file rest
    read -r line < /proc/$$/stat 2>/dev/null || return 1
    line=${line##*\)}
    local -a fields
    fields=(${=line})
    ticks=${fields[20]}
    [[ -n $ticks ]] || return 1
    read -r boot < /proc/sys/kernel/random/boot_id 2>/dev/null
    file="$LION_WATCH_DIR/$LION_WATCH_REF/events/${ticks}-$$-$RANDOM.log"
    if { print -r -- "segment"$'\t'"$boot"$'\t'"$$"$'\t'"$ticks"$'\t'"$EPOCHREALTIME" >> "$file" } 2>/dev/null; then
      LION_WATCH_SEG=$file
      return 0
    fi
    return 1
  }

  _lion_watch_preexec() {
    local text=$1 cwd
    [[ -n $text ]] || text=$3
    [[ -n $text ]] || return 0
    _lion_watch_start_segment || return 0
    _lion_watch_esc "$PWD"; cwd=$LION_WATCH_ESCAPED
    _lion_watch_mask "$text"
    _lion_watch_esc "$LION_WATCH_MASKED"
    LION_WATCH_T0=$EPOCHREALTIME
    _lion_watch_rec cmd "$LION_WATCH_T0" "$cwd" "$LION_WATCH_ESCAPED"
    return 0
  }

  _lion_watch_precmd() {
    local code=$? previous
    if [[ -n $LION_WATCH_SEG && -n $LION_WATCH_T0 ]]; then
      local now=$EPOCHREALTIME
      _lion_watch_rec result "$now" "$code" "$LION_WATCH_T0" "$now"
      LION_WATCH_T0=''
    fi
    previous=$LION_WATCH_REF
    LION_WATCH_REF=''
    [[ -r "$LION_WATCH_POINTER" ]] && read -r LION_WATCH_REF < "$LION_WATCH_POINTER" 2>/dev/null
    [[ $LION_WATCH_REF == $previous ]] || { LION_WATCH_SEG=''; LION_WATCH_T0=''; }
    if [[ -z $LION_WATCH_P10K ]] && _lion_watch_register_p10k; then
      LION_WATCH_P10K=1
    fi
    return $code
  }

  # Pure comment input is recorded here: preexec never sees comments while
  # interactive_comments is on. Input with any non-comment line is covered by
  # preexec and skipped here, so a pasted block is never recorded twice. Empty
  # input is dropped. Return 0 always so history stays untouched.
  _lion_watch_addhistory() {
    setopt local_options extended_glob
    [[ -o interactive_comments ]] || return 0
    local input=$1 line cwd
    input=${input%$'\n'}
    local -a lines
    lines=(${(f)input})
    local comments_only=0
    for line in "${lines[@]}"; do
      [[ -n ${line//[[:space:]]/} ]] || continue
      [[ $line == [[:space:]]#\#* ]] || return 0
      comments_only=1
    done
    (( comments_only )) || return 0
    _lion_watch_start_segment || return 0
    _lion_watch_esc "$PWD"; cwd=$LION_WATCH_ESCAPED
    _lion_watch_mask "$input"
    _lion_watch_esc "$LION_WATCH_MASKED"
    _lion_watch_rec comment "$EPOCHREALTIME" "$cwd" "$LION_WATCH_ESCAPED"
    return 0
  }

  _lion_watch_zshexit() {
    local code=$?
    [[ -n $LION_WATCH_SEG ]] || return 0
    _lion_watch_rec exit "$EPOCHREALTIME" "$code"
    return 0
  }

  # Powerlevel10k segment: a red dot while a recording is active. It reads the
  # pointer file only and never starts a scan or a process.
  function prompt_lion_watch() {
    [[ -r "$LION_WATCH_POINTER" ]] || return 0
    local ref=''
    read -r ref < "$LION_WATCH_POINTER" 2>/dev/null
    [[ -n $ref ]] || return 0
    p10k segment -f 196 -t '●'
  }

  _lion_watch_register_p10k() {
    (( ${+POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS} )) || return 1
    (( ${POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS[(I)lion_watch]} )) || POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS+=(lion_watch)
    return 0
  }

  autoload -Uz add-zsh-hook
  add-zsh-hook preexec _lion_watch_preexec
  add-zsh-hook precmd _lion_watch_precmd
  add-zsh-hook zshaddhistory _lion_watch_addhistory
  add-zsh-hook zshexit _lion_watch_zshexit
  _lion_watch_register_p10k && LION_WATCH_P10K=1

fi

# Watch dog: confirm critical input before it runs (ADR 0014). The guard is
# active only while the compiled artifact exists ('lion watch dog on' writes
# it, 'off' removes it); data (patterns, rule ids) comes exclusively from that
# file. Loading stays silent, uses no process in the key path and only touches
# interactive shells. The second Enter runs; editing the buffer re-arms.

typeset -g LION_DOG_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/lion/dog/rules.zsh"
if [[ -o interactive && -r "$LION_DOG_FILE" ]]; then
  source "$LION_DOG_FILE" 2>/dev/null
  if (( ${#LION_DOG_PATTERNS} )); then
    typeset -g LION_DOG_ARMED=''
    _lion_dog_accept() {
      setopt local_options no_ksharrays
      local buf=$BUFFER
      if [[ -n $buf ]]; then
        local pat idx=0
        for pat in "${LION_DOG_PATTERNS[@]}"; do
          idx=$((idx + 1))
          if [[ $buf =~ $pat ]]; then
            if [[ $buf == "$LION_DOG_ARMED" ]]; then
              LION_DOG_ARMED=''; zle .accept-line
            else
              LION_DOG_ARMED=$buf
              zle -M "LION dog: ${LION_DOG_IDS[idx]} — Enter again to run, edit to cancel"
            fi
            return
          fi
        done
      fi
      LION_DOG_ARMED=''; zle .accept-line
    }
    # Any buffer change invalidates an armed confirmation: clearing the line
    # (Ctrl-U, Ctrl-C) or editing it must re-arm instead of re-confirming.
    _lion_dog_clear_armed() {
      [[ -n $LION_DOG_ARMED && $BUFFER != "$LION_DOG_ARMED" ]] && LION_DOG_ARMED=''
      return 0
    }
    zle -N accept-line _lion_dog_accept
    zle -A accept-line lion-dog-accept
    autoload -Uz add-zle-hook-widget
    add-zle-hook-widget line-pre-redraw _lion_dog_clear_armed
  fi
fi

true
"""


class ShellError(ValueError):
    """The watch integration cannot be inserted, removed or inspected."""


@dataclass(frozen=True)
class ShellReport:
    """Messages for stdout and warnings for stderr of one shell operation."""

    messages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def get_home() -> Path:
    """Return the managed home directory, rejecting a foreign ``ZDOTDIR``."""
    return shlib.get_home()


def script_path(home: Path) -> Path:
    """Return the shlib-mode artifact path for ``home``."""
    return home / ".shlib" / "shlibs" / SCRIPT_NAME


def block_text() -> str:
    """Return the complete inlined block for ``~/.zshrc``."""
    return f"{BLOCK_START}\n{INTEGRATION}{BLOCK_END}\n"


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _regular_target(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ShellError(f"Refusing to replace non-regular file: {path}")


def _parts(text: str) -> tuple[str, str, str] | None:
    """Split ``.zshrc`` text into before/block/after around the watch markers.

    Raises:
        ShellError: If the markers are ambiguous or out of order.
    """
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == BLOCK_START]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == BLOCK_END]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ShellError("Ambiguous LION watch markers; repair ~/.zshrc before continuing.")
    a, b = starts[0], ends[0]
    return "".join(lines[:a]), "".join(lines[a : b + 1]), "".join(lines[b + 1 :])


def _backup(path: Path, suffix: str) -> Path:
    candidate = path.with_name(path.name + suffix)
    index = 0
    while candidate.exists() or candidate.is_symlink():
        index += 1
        candidate = path.with_name(f"{path.name}{suffix}.{index}")
    with candidate.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        _ = stream.write(path.read_bytes())
    return candidate


def _write(path: Path, text: str, mode: int) -> None:
    """Atomically replace a file that was backed up by the caller."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            _ = stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_new(path: Path, text: str, mode: int) -> None:
    """Publish a new file via a hard link, never overwriting one."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            _ = stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ShellError(f"Refusing to overwrite existing file: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _shlib_installed(home: Path) -> bool:
    return bool(shlib.status(home).get("installed"))


def _insert_shlib(home: Path) -> ShellReport:
    target = script_path(home)
    if not (home / ".shlib" / "shlibs").is_dir():
        raise ShellError("Shlib is installed but ~/.shlib/shlibs is missing; reinstall shlib first.")
    rc = home / ".zshrc"
    if rc.exists() and _parts(_read(rc)) is not None:
        raise ShellError("A watch block exists in ~/.zshrc while shlib is installed; run 'lion shell remove' first.")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ShellError(f"Refusing to replace non-regular file: {target}")
    if target.exists():
        if _read(target) == INTEGRATION:
            return ShellReport(messages=("Watch integration is already inserted; no files changed.",))
        raise ShellError(f"Watch script differs from the delivered integration: {target} (run 'lion shell remove').")
    validate_zsh(INTEGRATION)
    _write_new(target, INTEGRATION, 0o600)
    return ShellReport(messages=(f"Watch integration inserted: {target}",))


def _insert_zshrc(home: Path) -> ShellReport:
    rc = home / ".zshrc"
    _regular_target(rc)
    text = _read(rc) if rc.exists() else ""
    block = block_text()
    parts = _parts(text)
    if parts is not None:
        if parts[1] == block:
            return ShellReport(messages=("Watch integration is already inserted; no files changed.",))
        raise ShellError(f"Watch block differs from the delivered integration: {rc} (run 'lion shell remove').")
    script = script_path(home)
    if script.exists() or script.is_symlink():
        raise ShellError(f"An existing watch script requires reconciliation: {script}")
    validate_zsh(INTEGRATION)
    joined = "" if not text else text if text.endswith("\n") else text + "\n"
    joined += block
    validate_zsh(joined)
    mode = stat.S_IMODE(rc.stat().st_mode) if rc.exists() else 0o644
    backup = _backup(rc, ".before-lion-watch") if rc.exists() else None
    _write(rc, joined, mode)
    messages = [f"Watch integration inserted: {rc}"]
    if backup is not None:
        messages.append(f"Backup: {backup}")
    return ShellReport(messages=tuple(messages))


def insert(home: Path) -> ShellReport:
    """Insert the watch integration once, without overwriting anything."""
    if _shlib_installed(home):
        return _insert_shlib(home)
    return _insert_zshrc(home)


def _remove_shlib(target: Path) -> ShellReport:
    if target.is_symlink() or not target.is_file():
        raise ShellError(f"Refusing to remove non-regular file: {target}")
    text = _read(target)
    if not text.startswith(MARKER):
        raise ShellError(f"Foreign file without the LION watch marker: {target}; reconcile it before removing.")
    target.unlink()
    return ShellReport(messages=(f"Watch integration removed: {target}",))


def remove(home: Path) -> ShellReport:
    """Remove the single watch artifact, or report that none exists.

    Raises:
        ShellError: If both artifacts exist, the markers are ambiguous, or a
            foreign file would have to be touched.
    """
    rc = home / ".zshrc"
    text = _read(rc) if rc.exists() else ""
    parts = _parts(text)
    target = script_path(home)
    script_present = target.is_symlink() or target.exists()
    if script_present and parts is not None:
        raise ShellError("Two watch artifacts present (~/.zshrc block and shlib script); remove one manually first.")
    if script_present:
        report = _remove_shlib(target)
    elif parts is not None:
        _regular_target(rc)
        before, _block, after = parts
        new_text = before + after
        validate_zsh(new_text)
        mode = stat.S_IMODE(rc.stat().st_mode)
        backup = _backup(rc, ".before-lion-watch-remove")
        _write(rc, new_text, mode)
        report = ShellReport(messages=(f"Watch integration removed: {rc}", f"Backup: {backup}"))
    else:
        return ShellReport(messages=("Watch integration is not inserted; no files changed.",))
    warnings: list[str] = []
    if recording.get_active_pointer().exists():
        warnings.append(
            "A recording is active; its events stop at the next prompt in each shell. "
            "The record stays readable with 'lion watch show'."
        )
    return ShellReport(messages=report.messages, warnings=tuple(warnings))


def status(home: Path) -> dict[str, object]:
    """Return the read-only integration status for ``home`` without writing.

    Raises:
        ShellError: If the watch markers are ambiguous.
        OSError: If a file cannot be read.
    """
    rc = home / ".zshrc"
    text = _read(rc) if rc.exists() else ""
    parts = _parts(text)
    installed = bool(shlib.status(home).get("installed"))
    target = script_path(home)
    warnings: list[str] = []
    if installed:
        mode = "shlib"
        path = target
        script_ok = target.is_file() and not target.is_symlink()
        inserted = script_ok
        modified = script_ok and _read(target) != INTEGRATION
        if not script_ok:
            warnings.append(f"Shlib is installed, but the watch script is missing: {target}. Run 'lion shell insert'.")
        elif modified:
            warnings.append(
                "The watch script differs from the delivered integration; "
                "run 'lion shell remove' and 'lion shell insert' again."
            )
        if parts is not None:
            warnings.append("A watch block exists in ~/.zshrc while shlib is installed; keep exactly one artifact.")
    else:
        mode = "zshrc"
        path = rc
        inserted = parts is not None
        modified = inserted and parts is not None and parts[1] != block_text()
        if modified:
            warnings.append(
                "The watch block differs from the delivered integration; "
                "run 'lion shell remove' and 'lion shell insert' again."
            )
        if target.exists() or target.is_symlink():
            warnings.append(f"A watch script exists without shlib installed: {target}")
    if not inserted and recording.get_active_pointer().exists():
        warnings.append("A recording is active but the watch integration is not inserted; no events are captured.")
    return {
        "mode": mode,
        "path": str(path),
        "inserted": inserted,
        "modified": modified,
        "warnings": warnings,
    }


def shell_checks(ctx: DoctorContext) -> list[Finding]:
    """Check the watch integration state without writing (doctor topic ``shell``)."""
    try:
        result = status(ctx.home)
    except (OSError, ValueError) as exc:
        return [
            Finding(
                topic="shell",
                name="shell.status",
                status=CheckStatus.ERROR,
                message=f"Watch integration unreadable: {exc}",
                hint="Repair ~/.zshrc and ~/.shlib, then run 'lion shell status'.",
            )
        ]
    findings: list[Finding] = []
    inserted = bool(result.get("inserted"))
    modified = bool(result.get("modified"))
    mode = result.get("mode")
    path = result.get("path")
    if inserted and not modified:
        findings.append(
            Finding(
                topic="shell",
                name="shell.inserted",
                status=CheckStatus.OK,
                message=f"Watch integration inserted ({mode}): {path}",
            )
        )
    elif not inserted:
        findings.append(
            Finding(
                topic="shell",
                name="shell.inserted",
                status=CheckStatus.SKIP,
                message="Watch integration is not installed.",
                hint="Optional: 'lion shell insert' enables recording shell sessions.",
            )
        )
    if modified:
        findings.append(
            Finding(
                topic="shell",
                name="shell.modified",
                status=CheckStatus.WARN,
                message=f"Watch integration differs from the delivered version: {path}",
                hint="Run 'lion shell remove' and 'lion shell insert' to refresh it.",
            )
        )
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        for warning in cast("list[object]", warnings):
            findings.append(
                Finding(
                    topic="shell",
                    name="shell.warning",
                    status=CheckStatus.WARN,
                    message=str(warning),
                    hint="Reconcile the shell artifacts manually.",
                )
            )
    return findings
