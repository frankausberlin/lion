# E2E-Tests (Docker)

Dieses Verzeichnis ist das Gerüst für die Docker-Suite. Opt-in bedeutet hier:
Die Suite ist nicht Teil von `just check` und wird lokal nur über
`just test-e2e` gestartet; einen zusätzlichen Freigabeschritt pro Lauf gibt es
nicht. Der Workflow `.github/workflows/e2e.yml` läuft bei jedem Push und Pull
Request und führt automatisch Docker-Build und Suite aus, sobald Testdateien
vorhanden sind. Solange keine `test_*.py`-Datei existiert, meldet
`just test-e2e` einen Hinweis und beendet sich mit Exitcode 0, ohne Docker
aufzurufen.

## Zweck

E2E-Tests prüfen die Anwendung in einem Wegwerf-Container: das installierte
Paket, echte externe Werkzeuge und Seiteneffekte, die sich nicht glaubwürdig
mocken lassen. Der Container liefert Isolation und reproduzierbare
Abhängigkeiten aus `uv.lock`.

## Abgrenzung

- Unit- und Smoke-Tests liegen unter `tests/` und laufen in `just check`.
- `tests/e2e/` wird von `tests/conftest.py` aus dem normalen pytest-Lauf
  ausgeschlossen, solange `LUPYSTA_E2E` nicht `1` ist.
- E2E-Tests sind langsam und brauchen Docker. Sie sind kein Ersatz für schnelle
  deterministische Tests.

## Aufbau

```text
tests/e2e/
├── README.md
├── conftest.py
├── Dockerfile
├── test_*.py        # fügst du hinzu
└── cli/
    └── test_*.py    # verschachtelt ebenfalls erlaubt
scripts/test-e2e.sh
```

- `Dockerfile` baut das Image aus `pyproject.toml`, `uv.lock`, `src/` und
  `tests/`. Die Abhängigkeiten werden vor dem Quellcode installiert. Das
  Projekt selbst wird mit `--no-editable` als Wheel installiert; die Suite
  prüft damit die Paketierung statt nur den kopierten Quellbaum.
- `conftest.py` bricht ab, bevor pytest die Testmodule importiert, wenn die
  Suite ohne Opt-in, außerhalb des Containers oder ohne `/.dockerenv` läuft.
  Skips – auch Collection-Skips – werden im Container zu Fehlern.
- `scripts/test-e2e.sh` sucht rekursiv nach `test_*.py` unter `tests/e2e/`,
  ohne Testmodule zu importieren. Suchfehler brechen laut ab; ein fehlendes
  Verzeichnis gilt als leere Suite. Erst danach prüft er Docker, baut das Image
  mit `--file tests/e2e/Dockerfile`, startet den Container und sammelt die
  Artefakte ein.
- `scripts/test-e2e.sh` verwendet BuildKit (`--mount=type=cache`).

## Leerlauf-Semantik

| Zustand | Verhalten |
| --- | --- |
| Keine `test_*.py`-Dateien (auch verschachtelt) | Hinweis, Exitcode 0, kein Docker. |
| Suite-Verzeichnis nicht durchsuchbar | Laute Fehlermeldung, Exitcode 1. |
| Dateien vorhanden, Docker fehlt | Laute Fehlermeldung, Exitcode 1. |
| Dateien vorhanden, Test schlägt fehl | Exitcode 1 mit Log-Auszug. |
| Test oder Modul wird übersprungen | Fehler im Container, kein stiller grüner Lauf. |

## Erster Test

1. Docker und BuildKit bereitstellen: `docker info`, `docker buildx version`.
2. Eine Datei `tests/e2e/test_demo.py` – oder verschachtelt, etwa
   `tests/e2e/cli/test_export.py` – mit einer normalen `test_`-Funktion
   anlegen. Der Collection-Hook in `conftest.py` schützt den Lauf, bevor
   Testmodule importiert werden.
3. `just test-e2e` ausführen. Der Runner setzt `LUPYSTA_E2E=1` und
   `LUPYSTA_E2E_CONTAINER=1`, baut das Image und startet den Container.
4. Fehler zuerst im Log unter `e2e-artifacts.*/run.log` suchen.

## Isolation und Artefakte

- Kein Host-Netzwerk (`--network none`), keine Host-Mounts für veränderbare
  Daten.
- Unprivilegierter Nutzer `e2e` (uid 10001), Arbeitsverzeichnis `/app`.
- JUnit-Bericht und Logs landen unter `/artifacts` und werden nach
  `e2e-artifacts.*` kopiert; der Pfad wird am Ende ausgegeben. Schlägt das
  Kopieren fehl, meldet der Runner das als Fehler, statt es zu verschlucken.
- Der CI-Workflow lädt Logs und JUnit-Bericht aus `e2e-artifacts.*/` mit
  `if: always()` hoch, also auch bei fehlgeschlagenen Tests. Pytest-Tempdaten
  unter `work/` sind ausgeschlossen; bei leerer Suite gibt es nichts
  hochzuladen.
- `just clean` entfernt diese Artefakte.

## Grenzen

- Kein Sharding, kein Attach-Modus und keine vorgefertigten Szenarien.
- Das Gerüst ist minimal; projekt- und werkzeugspezifische Fixtures gehören in
  dieses Verzeichnis.
- `just check` führt keine E2E-Tests aus. Details stehen in `AGENTS.md` und im
  lupysta-Rezept `e2e-tests`.
