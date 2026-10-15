# Classifiers

`lion classify` turns the **recorded input blocks** of a recording into metadata
(groups and fields) with declarative JSON rules. The classification is derived
from the raw events on every read: nothing is stored, no command is executed
and a match is metadata only — it never claims that an action ran or succeeded.

Two read-only surfaces share the same engine:

- `lion classify [REF] [--json]` — full view of one recording, with rule
  details.
- `lion watch R` and `lion watch R-` — terse, text-only views of the **active**
  recording for use inside a recorded session.

See [ADR 0015](../decisions/0015-classifier-rules-read-time-ere.md) for the
rules and [ADR 0019](../decisions/0019-command-policies.md) for the optional
policy layer; [Recordings](recordings.md) owns the underlying event grammar.

## Blocks

A block is a non-empty `cmd` event of a recording, in chronological order:

- `comment`, `result`, `exit` and `segment` events are never blocks.
- `cmd` events with empty text are skipped.
- A block made up entirely of silent in-session reads is skipped: every segment
  must be exactly `lion watch R`, `lion watch R-`, `uv run lion watch R` or
  `uv run lion watch R-` (after stripping `sudo` and `NAME=value` prefixes).
  Such a read-only block does not appear in its own numbering, while a mixed
  block (`lion watch R; sudo apt install foo`) stays classified with its
  matches, commands and policy results. The detection is best-effort and
  cosmetic; aliases and other wrappers are not recognized.
- `index` numbers the remaining blocks from 1 in event order.

The recorded text is stored best-effort masked by the shell integration; the
classifier never unmasks or re-masks.

## Matching

The block text is first parsed by a bounded parser (version `classify/1`); if it
leaves the supported syntax subset, the block yields **no matches** — never a
guess. Supported are balanced quotes, plain redirections and the top-level
separators `&&`, `||`, `;`, `|` and the end of line. Unsupported are escaping,
subshells and grouping, command and process substitution, here-documents and
here-strings, control flow, function definitions and background jobs; the JSON
output does not repeat the reason, the engine reports no matches instead.

Rules have a `scope`:

- `segment` (default): the pattern is matched from the segment start
  (`re.match` semantics after the prefix strip), so printed text such as
  `echo "sudo nala install"` never matches. `shell.rm.recursive` carries the
  raw-buffer prefix class `(^|[;&|[:space:]])` instead of a bare `^`; it is
  matched from the start as well, and the same string is what the watch dog
  searches over the raw ZLE buffer.
- `block` (opt-in): the pattern is searched (`re.search`) in every **masked
  line** of the block — quoted spans and comments are replaced by a separator
  that can neither join two words nor fake a segment start — which allows
  cross-segment matches such as `curl … | sh`. Substitutions, here-documents
  and unquoted escaping never reach the masker: the parser gate above rejects
  them first, so printed and argument text cannot match.

Every matching rule contributes a match. There is **no first-match-wins**:
matches are deduplicated by `(group, rule_id, fields)` and ordered by
`(group, rule_id, fields)`.

## Groups and fields

The group vocabulary is closed; a rule may only emit fields of its group.

|Group|Fields|Typical triggers|
|-|-|-|
|`packages`|`manager`, `action`|apt/nala, brew, uv, pip, mamba, pnpm, snap, flatpak, dpkg: install, remove, update|
|`services`|`unit`, `action`, `scope`|`systemctl` start/stop/restart/enable/disable (`system` or `--user`), `service`|
|`containers`|`engine`, `action`, `target`|`docker`/`podman` compose calls; removing containers, images and volumes|
|`shell`|`target`, `action`|`source`, `exec zsh`, `chsh`, detectable writes into shell config files|
|`downloads`|`source`, `action`|`curl`, `wget`, `git clone`, downloads piped into a shell|
|`interruptions`|`action`|`reboot`, `shutdown`, `poweroff`, `halt`, `systemctl suspend`/`hibernate`, `logout`|
|`checks`|`subject`, `intent`|`--version`/`-V` queries and known status queries (`systemctl status`, `git status`, `docker ps`, …)|

## The rule file

Two files are merged: the bundled defaults
(`lion/classify/default_rules.json`) and the optional user file
`$XDG_CONFIG_HOME/lion/classifier.json` (default
`~/.config/lion/classifier.json`). A missing user file is fine; a present but
unreadable or invalid file is a loud error naming the path. `lion doctor`
reports the loaded rule set under the `classify` topic.

Top-level fields:

|Field|Type|Meaning|
|-|-|-|
|`schema_version`|int|must be `1`|
|`version`|string|ruleset version; a merged user version wins, otherwise the default version (`1`)|
|`rules`|list|non-empty list of rule objects; `id` must be unique per file|

Rule object:

|Field|Required|Meaning|
|-|-|-|
|`id`|yes|stable identifier (`<group>.<name>` by convention); a user rule with the same `id` replaces the default in place|
|`group`|yes|one of the closed group vocabulary|
|`scope`|no|`segment` (default) or `block`|
|`match`|yes|pattern in the ERE subset (see below)|
|`emit`|yes|non-empty object; keys are fields of the group, values are literals or `$n` capture references|
|`guard`|no|`none` (default) or `confirm`; `confirm` is read by the watch dog (`lion watch dog on`), never by `classify` itself|

`$n` references (`$1` … `$9`, exactly one digit) count **all** parentheses of
the pattern, including unreferenced groups, as in POSIX ERE. A capture that
did not participate resolves to an empty string. An invalid reference, an
unknown field, an unknown group or an unknown key fails loudly.

The merged rule list carries a deterministic content hash (SHA-256 over the
canonicalized rules: `id`, `group`, `scope`, `match`, `emit`, `guard`; the file
order and the user layer do not change equal content). `schema_version`,
`version` and the hash are part of every classification.

Example user file:

```json
{
  "schema_version": 1,
  "version": "2",
  "rules": [
    {
      "id": "packages.nala",
      "group": "packages",
      "match": "^nala[[:space:]]+(install|remove)([[:space:]]|$)",
      "emit": {"manager": "nala", "action": "$1"},
      "guard": "none"
    }
  ]
}
```

## Guards

`guard: "confirm"` marks a rule the **watch dog** compiles into its ZLE
artifact; `lion classify` and the terse `watch R`/`R-` reads ignore the field.
`lion watch dog on` renders every confirm rule once (leading `^` widened to
`(^|[;&|[:space:]])(sudo[[:space:]]+)?` for the raw-buffer search) and writes
`$XDG_DATA_HOME/lion/dog/rules.zsh`; after a rule change the artifact is stale
until the next `on` (doctor `watch.dog`). The built-in confirm set and the
guard's limits are documented in
[Configuration](config.md#watch-dog) and
[Guard critical input](../how-to/guard-critical-input.md).

## The ERE subset

Patterns are written once and must stay compilable by Python `re` **and** by
Zsh ERE (the watch dog reuses the same strings). The loader validates the
subset before anything runs and translates the POSIX classes for Python.

Supported: literals, `.`, `*`, `+`, `?`, `{m,n}`, `^`, `$`, `|`, capture groups
`( … )`, bracket expressions including the POSIX classes below, and backslash
escapes of non-alphanumeric characters (`\.`, `\|`, `\\`, …).

Supported POSIX character classes (translated to their ASCII sets):
`[[:alnum:]]`, `[[:alpha:]]`, `[[:blank:]]`, `[[:digit:]]`, `[[:lower:]]`,
`[[:space:]]`, `[[:upper:]]`, `[[:xdigit:]]`.

Rejected loudly: backreferences and alphanumeric escapes (`\1`, `\b`, `\d`,
`\s`, `\w`), `(?…)` group constructs (non-capturing, lookarounds, named
groups), non-greedy quantifiers (`*?`, `+?`, `??`), unknown POSIX classes,
unterminated classes and any pattern Python `re` cannot compile.

Patterns must also stay free of super-linear backtracking shapes: nested
quantifiers (`(a+)+`, `(a*)*`, `(a?)*`) and alternations under a repetition
with overlapping start characters (`(a|a)*`, `(a|ab)+`) are rejected, so
untrusted recorded text can never freeze a read or the watch dog. The check is
deliberately conservative; disjoint alternations such as `(a|b)+` and single
optional groups such as `([[:space:]]+-[^[:space:]]+)?` stay allowed.

## Policies

An optional policy file `$XDG_CONFIG_HOME/lion/policies.toml` (default
`~/.config/lion/policies.toml`, TOML) evaluates each **command segment** of a
block against declared work rules (ADR 0019). A missing file or an empty
policy list activates nothing and leaves every output unchanged; a present but
unreadable or invalid file is a loud error naming the path. Policies are
read-only data: nothing is stored, no command is executed, and a policy
outcome never changes the exit code.

A policy carries an `id`, a human-readable `text` and the optional criteria
`success` and `fail`. A criterion is a list of patterns over the closed group
and field catalog: the list means OR (also across groups), the fields inside
one pattern mean AND, and a list value means OR over the values. A missing
field never matches; an empty field table matches every match of the group.
There is no negation, no regex and no expression language.

```toml
schema_version = 1

[[policies]]
id = "P01"
text = "Install and remove Debian packages with nala."
success = [{ group = "packages", fields = { action = ["install", "remove"], manager = "nala" } }]
fail = [{ group = "packages", fields = { action = ["install", "remove"], manager = ["apt", "apt-get"] } }]

[[policies]]
id = "P02"
text = "Use uv for global Python packages."
```

Top-level fields:

|Field|Type|Meaning|
|-|-|-|
|`schema_version`|int|must be `1`|
|`policies`|list|possibly empty list of policy tables; `id` must be unique|

Policy table:

|Field|Required|Meaning|
|-|-|-|
|`id`|yes|unique, non-empty identifier without control characters|
|`text`|yes|non-empty rule text (newline and tab allowed)|
|`success`|no|non-empty list of patterns counting as compliant|
|`fail`|no|non-empty list of patterns counting as a violation|

A pattern table has the required `group` (the closed vocabulary) and the
optional `fields` table: every named field must be in that group's catalog and
carry a non-empty string or a non-empty list of non-empty strings. Unknown
keys, unknown groups or fields, wrong types and duplicate ids fail loudly.

Evaluation is per command, never per block or line: a block with
`nala install foo && apt install bar` yields two evaluations, and a `success`
hit in one command never influences a `fail` hit in another. For one policy
and one command the result is at most one of:

|Outcome|Meaning|
|-|-|
|`success`|At least one match satisfies a `success` pattern and none satisfies a `fail` pattern|
|`fail`|At least one match satisfies a `fail` pattern and none satisfies a `success` pattern|
|`conflict`|Both criteria are satisfied|

Only segment-scope matches are evaluated: a block-scope match such as
`curl … | sh` stays visible on the block but is never attributed to a single
command. The contributing matches stay attached as evidence (`criterion` and
1-based `match_index` into the command's matches); results appear in policy
file order. A policy without a hit produces no result, and a policy without
criteria stays a valid working instruction.

## `lion classify`

```
lion classify [REF] [--json]
```

`REF` resolves like every recording reference: exact reference, unique prefix,
`latest` or `active` (see [Recordings](recordings.md)). Without `REF` the
active recording wins, otherwise the newest stored one; without any recording
the command reports that and exits `0`. In `--json` mode this is
`{"ref": null, "ruleset": null, "parser_version": "classify/1", "blocks": []}`.

Text output shows one header line plus one `#<index>  $ <preview>` line per
block (`<preview>` is the block collapsed to one line and cut at 40
characters), followed by one line per match (`<group>: <field>=<value>, …`) or
`-` for a block without matches.

With an active policy file the block shows only its block-scope matches
(indented as before), followed by one `command <n>: <text>` line per parsed
segment; a command shows its own matches or `-`, plus one
`policy <id> <success|fail|conflict>: <text>` line per policy result. Blocks
with unsupported syntax show `-` and no commands.

JSON shape:

```json
{
  "ref": "2026-10-12T12-07-20.988141Z",
  "ruleset": {"schema_version": 1, "version": "1", "hash": "<sha256>"},
  "parser_version": "classify/1",
  "blocks": [
    {
      "index": 1,
      "segment": "2350-17006-5385.log",
      "time": "2026-10-12T12:07:32.986805+00:00",
      "text": "sudo nala install foo",
      "matches": [
        {
          "rule_id": "packages.apt",
          "source": "default",
          "group": "packages",
          "fields": {"manager": "nala", "action": "install"},
          "guard": "none"
        }
      ]
    }
  ]
}
```

`source` is `default` or `user` (the layer that provided the effective rule).
Blocks without matches appear with `"matches": []`.

With an active policy file the payload gains a top-level `policies` list (the
loaded definitions with `id`, `text` and the present `success`/`fail`
patterns) and every block gains `commands`:

```json
{
  "policies": [
    {
      "id": "P01",
      "text": "Install and remove Debian packages with nala.",
      "fail": [{"group": "packages", "fields": {"action": ["install", "remove"], "manager": ["apt", "apt-get"]}}]
    }
  ],
  "blocks": [
    {
      "index": 1,
      "commands": [
        {
          "index": 1,
          "text": "apt install bar",
          "matches": [
            {
              "rule_id": "packages.apt",
              "source": "default",
              "group": "packages",
              "fields": {"manager": "apt", "action": "install"},
              "guard": "none"
            }
          ],
          "policy_results": [
            {"policy_id": "P01", "status": "fail", "evidence": [{"criterion": "fail", "match_index": 1}]}
          ]
        }
      ]
    }
  ]
}
```

The command matches repeat the segment-scope hits of that segment; the
existing block `matches` stay the deduplicated union and every other field is
unchanged. Without a policy file the payload is byte-identical to before.

## `lion watch R` and `R-`

Inside an active recording (read-only, no collection):

- `lion watch R` shows the recording ref, the set ADR fields in catalog order
  (`adr:` line), the still-open catalog fields (`open:` line) and every block
  as `R<n>: <40-character preview>  <matches>`, where matches render as
  `group:field=value,field=value` joined with `; ` and `-` marks a block
  without matches.
- `lion watch R-` prints exactly one such line for the previous block (the R
  and R- reads themselves are silent and never numbered).

Without an active recording both print `No active recording.` and exit `0`;
`R-` without any block prints `No previous block.` Both commands are text-only
(no `--json`).

## Limits

- A match is metadata, not a success report; the classifier never runs the
  command and never verifies an effect.
- Best-effort masking means a secret typed in plain sight can appear in a
  preview; see [Recordings](recordings.md) for the masking rules.
- Silent-read filtering, quote masking and the supported syntax subset are
  deliberately conservative; everything outside them yields no matches.
- Policies evaluate only per command segment: block-scope matches
  (`curl … | sh`) are never policy evidence, and a block whose syntax leaves
  the parser subset yields no commands and no evaluation.
- The policy language has no negation: a missing field never matches, and
  there is no `!=`, no regex and no expression evaluation.
