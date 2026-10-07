# LION — Roadmap

> **Status:** lebendes Dokument (Entwurf v0.1, 2026-10-07).
>
> **Sprache:** Deutsch — siehe Sprachregel in
> [PROJECT_DEFINITION.de.md](PROJECT_DEFINITION.de.md).
>
> **Verwandt:** [PROJECT_DEFINITION.de.md](PROJECT_DEFINITION.de.md) (Vision,
> Scope, Terminologie, Prinzipien), `README.md` (Überblick),
> `lion <cmd> --help` (Kommando-Tiefe).

## 1. Zweck, Legende und Definition of Done

Diese Roadmap beschreibt **Reihenfolge und Stand** der Entwicklung. Sie
beantwortet *wann* und *was*, nicht *warum* (das steht in der Projektdefinition).

**Legende:**

| Zeichen | Bedeutung |
|---|---|
| ✅ | erledigt |
| 🔄 | läuft |
| 🧭 | geplant |
| ❓ | offen (Entscheidung erforderlich) |

**Definition of Done je Phase:** Feature **+** zugehörige `doctor`-Checks **+**
Tests **+** Doku (README/`--help`). `doctor` wächst also mit jedem Kommando mit;
seine Checks sind Teil der DoD, nicht ein Nachtrag.

## 2. Phasen

### Phase 0 — Fundament ✅

- Collector v1: `host`, `hardware`, `packages`.
- Kommandos `scan`, `status`, `history`, `diff` inkl. `--json`.
- Snapshot-Modell mit strikter Validierung und Referenzauflösung.
- Storage: `os.link`-Publikation, atomarer `os.replace`, `.history.lock`.
- `shlib` (`install`/`uninstall`/`status`), `--version`, e2e-Suite.

### Phase 1 — `doctor` v1 + Kern härten 🧭

- **`doctor` v1:** read-only Check-Framework (Registry, Status
  `ok`/`warn`/`error`, `--json`, `--fix` nach goldener Regel) inklusive Checks für
  den Bestand: Umgebung, Collector-Tools, `shlib`-Zustand, History-Integrität.
- **Kern härten:**
  - GPU-Identität splitten (`pci_id` / Treibername / Treiberversion).
  - `shlib`-Semantik Install→Uninstall→Install festlegen und umsetzen.
  - Config-Datei (`$XDG_CONFIG_HOME/lion/config.toml`) einführen.

### Phase 2 — Doku & Definition ✅/🔄

- `PROJECT_DEFINITION.de.md` und `ROADMAP.de.md` (dieses Dokument).
- Sprachregel in `AGENTS.md`, Verlinkung beider Dokumente.
- README restrukturieren: Detailprosa (shlib, Collector, Storage) nach
  `--help`/`docs/` verschieben; Überblick kürzen.
- Write-Boundary-Konzept-Doku.

### Phase 3 — Wiki v1 („dumm") 🧭

- `wiki status` / `wiki sync`; Ausgabe nur aus **aktuellem** State + `shlib`.
- Noch **keine** State-History, keine Recordings.
- Generisch als Record-Renderer entworfen, damit History und Recordings später
  als neue Record-Typen hinzukommen, ohne das Layout neu zu bauen.
- Voraussetzung: **OKF v0.2 definieren** (siehe Abschnitt 4, Frage 1).
- `doctor wiki` als Check.

### Phase 4 — Shell-Integration & Recordings 🧭

- `shell insert` / `shell remove`: genau ein markierter Einschub (bzw. ein
  Script unter `~/.shlib/`, wenn die Shell-Library aktiv ist).
- `watch start` / `watch stop`: erzeugt **Recordings** (Start→Stop-Intervall,
  Kommandoliste, Zeiten, optionale Beschreibung).
- Recording-Datenmodell und Lese-Sicht darauf (eigene Ablage
  `$XDG_DATA_HOME/lion/recordings/`).
- Danach **Wiki v2**: Recording-Abschnitt ergänzen.
- `doctor shell` und `doctor watch` als Checks.

### Phase 5 — Guard 🧭

- `watch dog`: Regex-Qualifikation kritischer Kommandos mit
  Bestätigungsanforderung.
- Herkunft und Format der Regexe, Config-Anbindung.
- Ehrliches Guardrail-Framing (keine Sicherheitsgrenze, siehe Non-Goals).
- `doctor watch --fix` prüft/liefert Guard-Konfiguration.

### Phase 6 — MCP 🧭

- `serve`: MCP-Server mit `tools`, `resources`, `prompts`.
- Secret-Redaktion für exponierte Ledger-Inhalte.
- `doctor serve` als Check.

## 3. Querschnitt

- **Tests:** jede Phase mit Unit-Tests; e2e wo sinnvoll.
- **Doku:** README/`--help` je Phase nachziehen; Definition nur bei
  Vision-/Scope-Änderungen.
- **CI:** `just check` bleibt grün; e2e-Job unverändert.
- **`doctor`-Checks:** je Phase mitgeliefert (DoD).

## 4. Offene Entscheidungen

Diese Fragen sind bewusst noch nicht entschieden; sie werden vor der jeweiligen
Phase geklärt.

1. ❓ **OKF v0.2:** externer Standard oder eigenes Format? — blockiert Phase 3.
2. ❓ **Recording-State:** Geschwister von Maschinen-State (gleiches Ledger,
   gleiche Validierung/Referenzen) oder getrennter Datenraum?
3. ❓ **`shell insert`-Ziel** bei aktivem `shlib`: bevorzugt `~/.shlib/shlibs/`,
   sonst markierter `.zshrc`-Block?
4. ❓ **Recordings lesen:** eigenes Kommando (`lion recordings`) oder
   `watch --list`?
5. ❓ **`dog`-Regexe:** Herkunft, Format, Ablage in der Config.
6. ❓ **Config-Datei:** Umfang, Ort, Format (`$XDG_CONFIG_HOME/lion/config.toml`).
7. ❓ **Verb-Vokabular:** `install`/`uninstall` (persistent) vs.
   `start`/`stop` (Laufzeit) vs. `insert`/`remove` vereinheitlichen.
8. ❓ **Deutsche JSON-Feldnamen** (`erstscan`, `zuletzt_bestaetigt`, `ereignis`):
   beibehalten oder migrieren?

## 5. Pflege

- Phasen-Status hier aktualisieren, sobald sich etwas ändert.
- Wird eine offene Frage entschieden, wandert die Entscheidung in
  `PROJECT_DEFINITION.de.md` (Terminologie/Scope) und die Frage wird hier
  entfernt.
- Diese Roadmap ist keine Zusage an Termine, sondern eine Reihenfolge.
