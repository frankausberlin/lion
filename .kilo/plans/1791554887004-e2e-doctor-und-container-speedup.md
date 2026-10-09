# LION: E2E-Suite für `doctor` ausbauen + Container-Handling beschleunigen

## Ziel

1. `doctor` end-to-end im echten, disposable Container stabil testen: reale
   externe Tools (`lspci`/`nvidia-smi`/`apt-mark`/`dpkg`/`zsh`), echtes
   Dateisystem, echte Permissions, echtes `reco.sh` — also genau die Pfade, die
   `tests/test_doctor.py` durch Mocks ersetzt.
2. Den E2E-Build und das Container-Handling beschleunigen, ohne die Isolation
   oder die Aussagekraft der Tests zu schwächen.

## Ausgangslage (Fakten)

- Testlauf ist **nicht** der Flaschenhals: der letzte Lauf dauerte
  `1.893s` für beide Szenarien (`e2e-artifacts.0mdseI/junit.xml`). Der
  Flaschenhals ist der `docker build` (`apt-get` + `uv sync --dev`).
- Das Image enthält bewusst nur `python3`, `python3-venv`, `ca-certificates`,
  `zsh` — **kein** `pciutils`/`nvidia-smi`. Dadurch ist `doctor` im Container
  deterministisch: `collectors.hardware` = `warn` (unavailable),
  `tools.lspci` = `warn`, `tools.apt_mark` = `ok`, `history.entries` = `skip`,
  `shlib.installed` = `skip`, Gesamt `warn`, Exit `0`, genau ein `reco.sh`.
  Das ist der ideale reale Anker.
- `lspci` **darf nicht** ins Image: es liest `/sys/bus/pci` des Hosts, wäre also
  host-abhängig und nicht deterministisch (README: „Physical GPU discovery is
  outside this suite's coverage").
- Der Build nutzt bereits Layer-Caches, aber `COPY src/` steht **vor**
  `uv sync`; damit invalidiert jede Quelländerung auch die komplette
  Dependency-Installation. Das Image zieht mit `--dev` u. a. `basedpyright`
  (großer Node-Download), `ruff`, `pip-audit`, `pre-commit`, `colorlog`,
  `pytest-cov` — im Testcontainer ungenutzt.
- Der Testcontainer braucht zur Laufzeit nur `pytest` (die E2E-Tests importieren
  ausschließlich `pytest`, stdlib und das installierte `lion`).

## Scope

**In Scope**

- Neue `doctor`-E2E-Szenarien (Umgebung, Storage/History, reale shlib).
- Gemeinsame E2E-Helper (Guard + Subprozess-Runner) für die neuen Tests.
- Eigene `e2e`-Dependency-Group + `uv.lock`-Update.
- Dockerfile-Split (Deps-Layer vor `src/`) + BuildKit-Cache-Mount für `uv`.
- Doku: `tests/e2e/README.md`, ggf. `AGENTS.md`; `docs/` nur falls nötig.

**Out of Scope**

- `pciutils`/`nvidia-smi` im Image, GPU-E2E, `--strict`/Topic-Subkommandos.
- Retention/Prune von `recos/`.
- Refactoring der bestehenden zwei Testdateien (bleiben unverändert; optionale
  spätere Konsolidierung auf den neuen Helper).
- Migrations von CI auf `docker/buildx` + GHA-Cache (optionaler Follow-up).

## Entscheidungen

1. **Eigene `e2e`-Group statt `--dev`.** `[dependency-groups] e2e = ["pytest"]`
   in `pyproject.toml`; Image baut mit `--no-dev --group e2e`. `just check`/CI
   bleiben unberührt (Default-Gruppe bleibt `dev`).
2. **Dockerfile-Split.** Zuerst `pyproject.toml`+`uv.lock`+`README.md` kopieren
   und `uv sync --locked --no-dev --group e2e --no-install-project` ausführen,
   **dann** `src/` kopieren und `uv sync --locked --no-dev --group e2e` erneut —
   so cached die teure Dependency-Schicht über Quelländerungen hinweg.
3. **BuildKit-Cache-Mounts** (`--mount=type=cache,target=/root/.cache/uv`,
   optional apt). `# syntax=docker/dockerfile:1`; BuildKit ist Docker-23+-Default.
4. **Deterministische Umgebung erhalten:** keine GPU-Tools ins Image; ein
   expliziter Guard im Bare-Test stellt sicher, dass `lspci`/`nvidia-smi`
   fehlen (frühes, lautes Scheitern, falls sich das Basis-Image ändert).
5. **Ein eigener `reco.sh` pro `warn`/`error`-Lauf, nie überschrieben.** Der
   Kollisionstest bleibt Unit-getestet (Zeit nicht frorierbar); E2E prüft die
   Invariante „zweiter Lauf erhält den ersten byte-identisch" + „N=2".
6. **„Clean run schreibt nichts" bleibt Unit-Test.** Im E2E-Image gibt es immer
   einen `warn` (kein `lspci`), daher ist ein reiner `ok`/`skip`-Lauf dort nicht
   erreichbar; wird im README explizit als Grenze dokumentiert.

## Tasks (geordnet)

### A. `e2e`-Dependency-Group

1. `pyproject.toml`: `[dependency-groups] e2e = ["pytest>=9.1.1"]` ergänzen.
2. `uv lock` ausführen (nur `uv.lock` ändert sich).
3. Prüfen: `uv run pytest -q --collect-only` (Default-Gruppe unverändert),
   `just check` grün.

### B. Dockerfile beschleunigen (`tests/e2e/Dockerfile`)

1. `# syntax=docker/dockerfile:1` als erste Zeile.
2. uv-Sync aufteilen:
   - `COPY pyproject.toml uv.lock README.md ./`
   - `RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --group e2e --no-install-project --python /usr/bin/python3 --no-managed-python`
   - `COPY src/ src/`
   - `RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --group e2e --python /usr/bin/python3 --no-managed-python`
3. Optional: apt-Cache-Mounts (`target=/var/cache/apt,sharing=locked` und
   `target=/var/lib/apt/lists,sharing=locked`); dann `rm -rf /var/lib/apt/lists/*`
   entfernen.
4. `.dockerignore` bleibt unverändert (bereits minimal).
5. `CMD` bleibt (`uv run --no-sync pytest tests/e2e -m e2e …`).
6. `scripts/test-e2e.sh` bleibt unverändert.

### C. Gemeinsamer E2E-Helper (`tests/e2e/conftest.py`, neu)

- `require_container()`: Skip-Guard (`LION_E2E_CONTAINER=1` **und**
  `/.dockerenv`), identisch zu den bestehenden Tests.
- Fixture `container` (ruft den Guard).
- Fixture/Factory `make_runner(tmp_path)`: gibt einen Aufrufer zurück, der
  `subprocess.run` (Timeout, `LC_ALL=C`, übergebenes `env`) ausführt, Befehl,
  Exitcode, stdout, stderr in ein Log unter `tmp_path` schreibt und bei
  unerwartetem Exitcode mit vollem Output fehlschlägt.
- Keine Änderung an den bestehenden zwei Testdateien.

### D. `tests/e2e/test_doctor_environment.py` (neu, `pytestmark = pytest.mark.e2e`)

Jeder Test setzt `HOME=[tmp home]`, `XDG_DATA_HOME=[tmp data]`, `ZDOTDIR` unset
(oder = `HOME`), `LC_ALL=C`.

- **D1 Profil + reco (Kernfall):** `lion doctor --json` auf leerem
  Datenverzeichnis.
  - Assert je Befund: `collectors.host ok`, `collectors.packages ok`,
    `collectors.tools ok`, `collectors.hardware warn`;
    `tools.lspci warn` mit `commands == ["sudo apt install pciutils"]`,
    `tools.apt_mark ok`, `tools.nvidia_smi/rocm_smi/zsh skip`;
    `history.entries skip`; `storage.data_dir/history/recos ok`;
    `shlib.installed skip`.
  - `status == "warn"`, Exit `0`.
  - Guard: `shutil.which("lspci") is None` und `shutil.which("nvidia-smi") is None`.
  - Genau **eine** `recos/*.sh`, Modus `0o700`; `recos/`-Dir Modus `0o700`;
    Inhalt enthält `sudo apt install pciutils`, `#!/usr/bin/env bash`,
    „führt dieses Skript niemals aus", Header mit Version + Host.
- **D2 JSON-Reinheit / `--show`:** `lion doctor --json --show` ⇒ stdout ist
  reines JSON, kein `#!/usr/bin/env bash` im stdout. Danach `lion doctor --show`
  (Text) ⇒ Shebang im stdout.
- **D3 Read-only-Invariante:** Vor/nach einem `warn`-Lauf ohne History:
  `history/` darf **nicht** existieren, `.history.lock` **nicht** existieren,
  nur `recos/` entsteht.
- **D4 reco führt nichts aus (Hostile-Filename):** In `history/` eine Datei mit
  Newline + `$(touch <canary>)` im Namen mit kaputtem TOML anlegen. `doctor`
  ⇒ Exit `1`; Canary-Datei nirgends erzeugt; jede Zeile von `reco.sh` ist
  Kommentar oder in `{"", "#!/usr/bin/env bash", "set -euo pipefail"}`.
- **D5 Kein Überschreiben:** Zweimal `doctor` im selben Datenverzeichnis ⇒
  `recos/` enthält 2 Dateien, die erste ist byte-identisch wie vorher, beide
  Modus `0o700`.
- **D6 Kaputte History + gültiger Eintrag:** Erst echtes `lion scan` (schreibt
  einen Snapshot), dann `broken.toml` (`broken = [`) ablegen. `doctor` ⇒ Exit
  `1`; `history.entries ok` (1 gültig), `history.broken.toml error` mit Pfad;
  reco existiert.
- **D7 Legacy `scans/`:** `$XDG_DATA_HOME/lion/scans/` anlegen ⇒
  `history.scans warn`, Exit `0`.
- **D8 Datenverzeichnis nicht nutzbar (root-fest):** `XDG_DATA_HOME` auf einen
  **regulären Datei**-Pfad setzen ⇒ `storage.data_dir error`, Exit `1`
  (bewusst kein `chmod`-Test: root umgeht DAC, `os.access` lügt).

### E. `tests/e2e/test_doctor_shlib.py` (neu, `pytestmark = pytest.mark.e2e`)

Reale `lion shlib install`-Installation in temporärem `HOME`/`ZDOTDIR`.

- **S1 Konsistent installiert:** `lion shlib install`, dann `doctor --json` ⇒
  `shlib.installed ok`, **kein** `shlib.lock`-Befund, keine `shlib.warnings`.
- **S2 Referenzkopie fehlt:** `~/.zshrc.lock` löschen ⇒ `shlib.lock warn`
  („fehlt"), Exit `0`.
- **S3 `.zshrc` driftet:** `~/.zshrc` verändern ⇒ `shlib.lock warn` („changed").
- **S4 Verzeichnis fehlt:** `~/.shlib/exports/` entfernen ⇒ `shlib.warnings warn`.
- **S5 `dash/`-Defekte:** reguläre Datei + kaputter Symlink in `~/.shlib/dash/`
  ⇒ zwei `shlib.dash.* warn`.
- **S6 Mehrdeutige Marker ⇒ error:** `.zshrc` mit zwei `START`-Zeilen ⇒
  `shlib.status error`, Exit `1`.
- **S7 Fremdes `ZDOTDIR`:** `ZDOTDIR` ≠ `HOME`, ohne Installation ⇒
  `shlib.home skip`, Exit `0`; mit Installation (in `HOME`) ⇒ `shlib.home
  error`, Exit `1`.

### F. Doku

- `tests/e2e/README.md`:
  - neue Szenarien in die Tabelle aufnehmen (Umgebung/Storage/History,
    shlib-doctor);
  - Build-Beschleunigung + `e2e`-Group kurz erklären;
  - Grenze dokumentieren: „clean run writes nothing" ist nur Unit-getestet,
    weil das E2E-Image absichtlich kein `lspci` hat;
  - Hinweis, warum GPU-Tools bewusst fehlen (Determinismus).
- `AGENTS.md` (Testing): E2E-Auslöser um `doctor`-Checks/Reco-Pfade ergänzen.
- `docs/` nur anfassen, wenn eine Aussage inhaltlich veraltet ist (kein neuer
  Doc-Bedarf zu erwarten).

## Konkrete Testfälle (Kurzüberblick)

| ID | Szenario | Kernassertion |
| --- | --- | --- |
| D1 | Bare-Container `doctor` | Profil-Befunde wie oben, 1 reco, Exit 0 |
| D2 | `--json`/`--show` | JSON rein; `--show` nur Text |
| D3 | Read-only | kein `history/`, kein `.history.lock` |
| D4 | Hostile Dateiname | keine Ausführung, nur Kommentare |
| D5 | Doppellauf | 2 recos, erste unverändert |
| D6 | Kaputte History | Exit 1, `history.<name> error` |
| D7 | Legacy `scans/` | `history.scans warn` |
| D8 | Datenpfad = Datei | `storage.data_dir error`, Exit 1 |
| S1–S7 | reale shlib-Zustände | `shlib.*` ok/warn/error/skip korrekt |

## Validation

- `uv lock` sauber; `just check` grün (ruff, basedpyright, Docs-Check, pytest,
  Coverage ≥ 90 %).
- `just test-e2e` grün; alle neuen Tests laufen (kein Skip). Ergebnis im
  `E2E diagnostics`-Pfad prüfen (`junit.xml` test=… failures=0).
- Build-Zeitvergleich: kalter Cache vs. warmer Cache nach kleiner `src/`-
  Änderung — Erwartung: Dependency-Layer bleibt gecached, nur Projekt-Layer +
  Test-Layer bauen neu. Zahlen im PR dokumentieren.
- BuildKit-Fallback: einmal mit `DOCKER_BUILDKIT=0` verifizieren, dass der
  Build nicht hart bricht (Cache-Mounts werden dann ignoriert). Falls der Build
  ohne BuildKit nicht unterstützt werden soll: README-Anforderung „Docker mit
  BuildKit" ergänzen.
- Isolations-Checks der neuen Tests (Container-Guard) müssen erhalten bleiben;
  ein skipped Lifecycle-Test zählt nicht als E2E-Erfolg.

## Risiken / Edge Cases

- **Determinismus der Hardware:** hängt an „kein `lspci`"; Guard in D1 macht
  eine Regression laut sichtbar. `lspci` nicht ins Image aufnehmen.
- **Root-Permissions:** `chmod`-basierte Unschreibbarkeits-Tests sind als root
  wertlos → D8 nutzt „Pfad ist Datei" (unabhängig von DAC).
- **Zeitstempel:** reco-Kollisionen sind im E2E nicht erzwingbar; Suffix-Logik
  bleibt Unit-getestet. D5 prüft die Invariante „nie überschreiben".
- **`--no-dev` im Image:** verifiziert, dass `lion` und die E2E-Tests zur
  Laufzeit nur `typer`/`tomli-w`/`pytest`/stdlib brauchen (grep bestätigt).
- **BuildKit-Abhängigkeit:** Cache-Mounts erfordern BuildKit; CI/ubuntu-latest
  und moderne Docker sind Default-BuildKit. Fallback dokumentieren.
- **`uv.lock`-Änderung:** durch neue Gruppe; muss mitcommittet werden, sonst
  schlägt `--locked` fehl.

## Offene Punkte / Follow-up

- CI-Beschleunigung via `docker/buildx` + GHA-Cache (`type=gha`): bewusst nicht
  Teil dieses Plans; CI passt bereits in 15 min. Optionaler späterer Schritt.
- Konsolidierung der bestehenden zwei E2E-Dateien auf den neuen Runner: optional.
- `--strict`, Topic-Subkommandos und `reco`-Retention bleiben separate Features
  (siehe Roadmap Phase 1).
