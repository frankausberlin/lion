# LION — Zustands-History + Collector-Framework (v1)

## Ziel

LION erfasst den Betriebszustand über Collector-Module, speichert eine **History
distinkter Zustände** und vergleicht beim `status`-Aufruf den frisch erfassten
Zustand mit dem jüngsten gespeicherten Zustand. `scan` schreibt, `status` liest
nur.

## Festgelegte Entscheidungen

- **Vergleichsmodell A**: nur mit dem jüngsten Zustand vergleichen. Gleich →
  `zuletzt_bestaetigt` aktualisieren; anders → neuen History-Eintrag anhängen.
  Wiederkehrende Zustände werden **nicht** reaktiviert.
- **Zwei Zeitfelder** pro Eintrag: `erstscan` (erste Beobachtung) und
  `zuletzt_bestaetigt` (letzte unveränderte Bestätigung).
- **`scan` schreibt** (anlegen / `zuletzt_bestaetigt` aktualisieren / anhängen);
  **`status` schreibt nie** und vergleicht nur. Der frühere Überschreib-Prompt entfällt.
- **Collector-Status zählt als Änderung** (ok / unavailable / error ist Teil des Vergleichs).
- **Ausgabe**: gruppierter Diff (`+`/`-`/`~`) pro Collector im Terminal;
  `status --json` liefert ein strukturiertes Objekt mit `geaendert`, `seit`, `unterschiede`.
- **Neues Verzeichnis** `$XDG_DATA_HOME/lion/history/` (Default `~/.local/share/lion/history`).
  Alte `scans/` werden **nicht mehr gelesen** und bleiben liegen; kein Migrationscode.
- **Je Collector-Datei**: Dataclass und Collector-Funktion liegen zusammen in
  `src/repolion/state/<collector>.py`.
- **v1-Collectors**: `host`, `hardware`, `packages`.

## Außerhalb des Umfangs (Roadmap)

- Collectors `network`, `services`, `toolchain`, `containers`, `observation`.
- Migration der alten `scans/`.
- Locking für gleichzeitige `lion`-Prozesse.
- Konfigurationsdatei (aktivierte Collectors, Toolchain-Liste, Observation-Watchlist).

## Datei-Layout

```
src/repolion/
  main.py            # unverändert
  cli.py             # scan/status neu, Diff-Rendering, --json
  paths.py           # get_history_dir(); get_scans_dir() entfernen
  storage.py         # History laden/schreiben, Dedup, strikte Validierung
  diff.py            # rekursiver Mapping-Diff + Terminal-Rendering
  scan.py            # ENTFERNEN (aufgeteilt in state/host.py + state/hardware.py)
  state/
    __init__.py
    base.py          # CollectorStatus, CollectorResult, Collector, collect_state()
    registry.py      # COLLECTORS = (host.COLLECTOR, hardware.COLLECTOR, packages.COLLECTOR)
    model.py         # Snapshot (schema_version, erstscan, zuletzt_bestaetigt, collectors)
    host.py          # HostState + collect()
    hardware.py      # HardwareState + collect()
    packages.py      # PackagesState + collect()
```

`state/base.py` hält nur die gemeinsamen Typen (keine Collector-Imports), damit
keine Importzyklen entstehen. `registry.py` importiert die Collector-Module und
stellt die geordnete Registry bereit.

Alle Collector-Module folgen demselben Muster:

```python
from dataclasses import dataclass
from repolion.state.base import Collector, CollectorResult, CollectorStatus

@dataclass(frozen=True)
class HostState:
    hostname: str
    ...

def _collect() -> CollectorResult:
    ...
    return CollectorResult(status=CollectorStatus.OK, data=asdict(HostState(...)))

COLLECTOR = Collector(name="host", collect=_collect)
```

## Zustandsformat (TOML)

Pfad: `~/.local/share/lion/history/<erstscan-utc>.toml` (z. B.
`2026-10-05T20-00-00.123456Z.toml`).

```toml
schema_version = 1
erstscan = "2026-10-05T20:00:00.123456+00:00"
zuletzt_bestaetigt = "2026-10-05T20:30:00.654321+00:00"

[collectors.host]
status = "ok"                       # ok | unavailable | error
error = ""
hostname = "workstation"
distribution = "Example Linux"
distribution_version = "1.0"
kernel = "6.0.0"
architecture = "x86_64"

[collectors.hardware]
status = "ok"
error = ""
cpu_model = "Example CPU"
cpu_logical_cores = 8
memory_total_bytes = 17179869184
cuda_version = "12.6"               # "" wenn unbekannt
gpu = [ { name = "NVIDIA ...", driver_version = "560.1", memory_total_bytes = 17179869184 } ]

[collectors.packages]
status = "ok"
error = ""
installed = { bash = "5.2.21-2", libfoo = "1.0-1" }  # vollständige Dpkg-DB, Name -> Version
manual = ["bash"]                    # apt-mark showmanual
auto = ["libfoo"]                    # apt-mark showauto
held = []                            # apt-mark showhold
```

- `schema_version = 1` ist Pflicht; unbekannte Version → Ladefehler.
- `erstscan`/`zuletzt_bestaetigt` müssen einen UTC-Offset tragen (auch Alt-Werte
  mit lokalem Offset bleiben lesbar).
- **Dedup**: kanonische Serialisierung von `[collectors]` (JSON, `sort_keys`,
  kompakte Trenner). `erstscan`/`zuletzt_bestaetigt` zählen nicht; `status`/`error`
  zählen mit. Zwei Zustände sind gleich, wenn die kanonischen Strings identisch sind.

## Speicher-Semantik (`storage.py`)

- `get_history_dir()` aus `paths.py`; Verzeichnis bei Bedarf anlegen.
- `load_latest() -> Snapshot | None`: alle `*.toml` laden, strikt validieren,
  jüngster Eintrag anhand `zuletzt_bestaetigt`, Tie-Break über Dateiname.
  Ein ungültiger Eintrag lässt `load_latest` mit Pfad in der Fehlermeldung
  scheitern (kein stilles Überspringen).
- `save_state(collectors) -> SaveOutcome`:
  - Kein Eintrag → neue Datei, `erstscan = zuletzt_bestaetigt = jetzt` → `CREATED`.
  - Kanonisch gleich dem jüngsten Eintrag → jüngste Datei **atomar ersetzen**
    (temporär schreiben, `os.replace`), nur `zuletzt_bestaetigt` = jetzt → `CONFIRMED`.
  - Sonst → neue Datei, beide Felder = jetzt → `APPENDED`.
  - Neue Einträge werden über `os.link` (kein Überschreiben) publiziert; die
    Head-Aktualisierung nutzt bewusst `os.replace` (einzige erlaubte Mutation).
- `SaveOutcome` trägt `event`, `path`, `snapshot`.

## Diff (`diff.py`)

- `diff_collectors(old: Mapping, new: Mapping) -> dict` — rekursiv über
  verschachtelte Mappings:
  - `added` (in new, nicht in old), `removed`, `changed` (gleicher Schlüssel,
    anderer Wert).
  - Blatt-Mappings (z. B. `packages.installed`) liefern `+`/`-`/`~`-Einträge.
  - `collectors`-Sektionen im Ergebnis nach Collector gruppiert.
- `render(diff) -> str` erzeugt gruppierte Terminal-Ausgabe (`+`, `-`, `~`).
- Kein Diff → leeres Ergebnis; CLI zeigt dann die Unverändert-Meldung.

## CLI (`cli.py`)

- `lion scan [--json]`:
  - Collector-Registry ausführen → Zustand erfassen → `save_state`.
  - Terminal: kurze Zeile je Ereignis („Zustand angelegt", „Zeitstempel
    aktualisiert", „Neuer Zustand gespeichert") plus Pfad.
  - `--json`: `{"ereignis": "created|confirmed|appended", "pfad": "...", "zustand": {…}}`.
- `lion status [--json]`:
  - Zustand erfassen, `load_latest()`, `diff_collectors`; **kein Schreiben**.
  - Keine History: Terminal „Kein Zustand gespeichert. Führe 'lion scan' aus.";
    `--json` → `null`, Exit 0.
  - Gleich: „Seit dem letzten Scan am `<zuletzt_bestaetigt>` hat sich nichts geändert."
  - Anders: gruppierter Diff.
  - `--json`: `{"geaendert": bool, "seit": "<zuletzt_bestaetigt>", "unterschiede": {...}}`.
- Fehler (OSError/ValueError) → stderr mit Exit 1, kein JSON auf stdout.
- Nicht-interaktiv: da kein Prompt mehr existiert, ist CI/Skript-Betrieb ohne
  TTY unkritisch.

## Collector-Spezifikationen (v1)

**host** — `hostname`, `distribution`, `distribution_version`, `kernel`,
`architecture`. Wiederverwendung von `platform.freedesktop_os_release`,
`platform.release/machine/node`; fehlende Werte → `"Unknown"`. Status immer `ok`.

**hardware** — `cpu_model` aus `/proc/cpuinfo`, `cpu_logical_cores` aus
`os.cpu_count() or 0`, `memory_total_bytes` aus `/proc/meminfo` (bestehende,
getestete Logik übernehmen), `gpu` als Liste
`{name, driver_version, memory_total_bytes}` per
`nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader`,
`cuda_version` best-effort aus derselben Ausgabe. Fehlende `/proc`-Dateien →
`"Unknown"`/`0`, Status `ok`. Fehlt `nvidia-smi` → `gpu = []`, `cuda_version = ""`,
Status bleibt `ok`. Keine flüchtigen Felder (Temperaturen, Clocks, Uptime).

**packages** — `installed` aus `/var/lib/dpkg/status` (nur `Status: install ok
installed`, Name → Version), `manual`/`auto`/`held` als sortierte Namenslisten
aus `apt-mark showmanual|showauto|showhold`. Fehlt Dpkg/apt-mark → Status
`unavailable`. Subprozesse mit `LC_ALL=C` und Timeout. Keine Duplizierung der
Versionen in den Listen.

## Aufgaben (Reihenfolge)

1. `state/base.py`: `CollectorStatus`, `CollectorResult`, `Collector`, `collect_state()`.
2. `state/model.py`: `Snapshot` mit `to_toml_dict()` / `from_toml_dict()`
   (Schema-Prüfung, UTC-Offset-Prüfung).
3. `state/host.py`, `state/hardware.py`, `state/packages.py` nach obigem Muster
   inkl. Dataclasses; `state/registry.py`.
4. `paths.py`: `get_history_dir()` ergänzen, `get_scans_dir()` entfernen.
5. `storage.py` neu: `load_latest`, `save_state`, `SaveOutcome`, strikte Validierung.
6. `diff.py`: `diff_collectors` + `render`.
7. `cli.py` neu: `scan`/`status` inkl. `--json` und Fehlerbehandlung.
8. `scan.py` entfernen; Referenzen in `cli.py`/Tests auflösen.
9. Tests umstellen/ergänzen:
   - `test_host.py`, `test_hardware.py` (Fixture-basiert via `monkeypatch`, wie
     bisherige `test_scan.py`), `test_packages.py` (Fixture `/var/lib/dpkg/status`,
     gemockte `apt-mark`/`nvidia-smi`).
   - `test_storage.py`: anlegen / `zuletzt_bestaetigt` aktualisieren ohne neue
     Datei / ändern → neue Datei / Dedup bei Statuswechsel / ungültige Datei
     nennt Pfad / leeres Verzeichnis.
   - `test_smoke.py`: `scan`, `status` (kein Diff, Diff), `--json`, Fehlerpfade.
10. `README.md` aktualisieren: neues Verzeichnis `history/`, Collector-Konzept,
    Vergleichsmodell, Hinweis „alte `scans/` werden nicht mehr gelesen".
11. `just check` grün stellen (ruff, basedpyright strict, pytest, Coverage ≥ 90 %).

## Validierung

- `just check` (ruff + Format + basedpyright + pytest mit Coverage ≥ 90 %).
- Manuell: `uv run lion scan` (zweimal → zweiter Aufruf bestätigt), Zustand
  künstlich ändern → `uv run lion scan` hängt an, `uv run lion status` zeigt Diff,
  `uv run lion status --json` liefert `geaendert: true`.
- Fehlerfall: ungültige Datei in `history/` → `status` bricht mit Pfad und Exit 1 ab.
- `uv run pip-audit`.

## Risiken / Hinweise

- `nvidia-smi`, `apt-mark`, `dpkg` sind umgebungsabhängig; Collectors dürfen nie
  die gesamte Erfassung abbrechen, Tests laufen über Fixtures/Mocks (CI hat kein
  nvidia-smi).
- Vollständige Dpkg-DB kann die Zustandsdatei groß machen und Diffs unübersichtlich;
  bei Bedarf später auf `manual`/`held` eingrenzen.
- `Collector-Status zählt` bedeutet: flüchtige Tool-Fehler (`error`) erzeugen neue
  History-Einträge. Bewusster Trade-off; bei Rauschen später `error` vom Vergleich
  ausnehmen.
- Head-Aktualisierung per `os.replace` ist die einzige bewusste Abweichung von der
  bisherigen „nie überschreiben"-Regel.

## Offene Punkte (nicht v1-blockierend)

- `network`, `services`, `toolchain`, `containers`, `observation` als Folgeschritte.
- Observation-Mechanik (Symlink-Watchlist, SHA-256, Fehlerfälle).
- Toolchain-Werkzeugliste (28 Tools) und wo Konfiguration liegt.
- Konfigurationsdatei für aktivierte Collectors.
- Ob `scan --json` dauerhaft das umhüllende Ereignis-Objekt statt nur den Zustand
  ausgeben soll.
