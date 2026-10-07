# LION — Projektdefinition + Roadmap (Plan)

Ziel: zwei deutschsprachige, **versionierte** Dokumente im Repo-Root anlegen und
sie in `AGENTS.md` verlinken. Dieses Plan-File ist die umsetzungsreife Vorlage;
ein Implementierungs-Agent legt die beiden Zieldateien an.

## 1. Entscheidungen (bestätigt)

- **Ort:** Repo-Root, versioniert (nicht `ignore/`).
- **Sprache:** Deutsch; einzige bewusste Ausnahme zur Englisch-Regel.
- **Naming:** `PROJECT_DEFINITION.de.md`, `ROADMAP.de.md` (BCP-47).
- **Trennung:** Definition = *warum/was*; Roadmap = *wann/Reihenfolge*.
- **Agenten-Einstieg:** `AGENTS.md` (englisch) verlinkt beide mit je einem
  englischen Ein-Zeiler.
- **Konzeptionelles Modell zuerst:** Terminologie (Rollen, Akteure, Artefakte,
  Operationen, Beziehungen) steht am Anfang der Definition, nicht als Anhang.
- **Begriff `recording`:** `watch` = Mechanismus, `recording` = Artefakt
  (Start→Stop); Ablage `$XDG_DATA_HOME/lion/recordings/`. Reserviert bleiben:
  „shell session" (zsh-PID), „observation" (geplanter Collector).
- **`doctor` zuerst:** erste Entwicklungsphase; wächst mit jedem Kommando mit
  (Checks sind Teil der DoD).
- **Wiki v1 = „dumm":** nur aktueller State + `shlib`; keine History; generisch
  als Record-Renderer.
- **Goldene Regel:** Schreiben nur in Lion-eigene Verzeichnisse; `--fix` opt-in,
  `.zshrc` nie, nie destruktiv (höchstens `*.corrupt`).

## 2. Deliverable A — `PROJECT_DEFINITION.de.md`

### Kapitelstruktur

1. **Zweck, Geltung, Pflege** — Verhältnis zu README/`--help`; Sprachregel
   (Projekt englisch; Definition + Maintainer-Kommunikation deutsch).
2. **Konzeptionelles Modell** (Terminologie am Anfang):
   - 2.1 Rollen/Akteure: Maintainer (Mensch), Agent (KI/Web), System (Linux-Host).
   - 2.2 Artefakte: `snapshot`, `ref`, `recording`, `shlib`-Dateien,
     `.zshrc`-Einschub, Wiki, Config.
   - 2.3 Operationen: `scan`/`status`/`history`/`diff`, `watch start/stop`,
     `dog`-Guard, `doctor status`/Themen/`--fix`, `shlib install/uninstall`,
     `wiki sync`, `serve`.
   - 2.4 Beziehungen: Ledger aus Records; Collector → snapshot; watch →
     recording; Lese-Sichten (wiki/serve).
   - 2.5 Abgrenzung: `state` vs. `status`; shell session vs. recording;
     `observation` (Collector) vs. `recording`.
3. **Vision**
4. **Produktprinzipien** (inkl. goldene Regel, fail-loud, read/write-Trennung,
   deterministisch, keine Secrets)
5. **Architekturüberblick** (Collector/State – Shell-Integration – Lese-Sichten;
   querschnittlich `doctor`)
6. **Was heute existiert (Bestand)** — siehe 2.1 unten
7. **Kommandolandkarte** (Zielbild + Status: existiert/geplant)
8. **Non-Goals**
9. **Dokumentations-Schichtung**
10. **Offene Fragen** (Verweis auf ROADMAP)

### 2.1 Bestandsaufnahme (Kapitel 6, faktisch, aus Code/README)

- **Collector-Framework:** `host`, `hardware`, `packages`; je `status`
  (`ok`/`unavailable`/`error`) + `error`; ein Fehler stoppt nie die Erfassung;
  keine volatilen Felder.
- **State-Modell:** TOML-Snapshots, `schema_version = 1`, `erstscan` /
  `zuletzt_bestaetigt`; strikte Validierung; kanonischer Vergleich + 1-MiB-Toleranz
  nur auf `hardware.memory_total_bytes`; Referenzauflösung (Alias, Index,
  Präfix, ISO).
- **Kommandos:** `scan` (schreibt), `status`/`history`/`diff` (read-only),
  `--json`, `--version`, `history --limit N`.
- **Storage:** XDG, `os.link`-Publikation, atomarer `os.replace` nur beim
  Bestätigen, `.history.lock`; beschädigte Einträge stoppen fail-loud mit Pfad.
- **`shlib`:** install/uninstall/status, `~/.shlib/` (`exports/`, `shlibs/`,
  `dash/`), Backups, `.zshrc.lock`, `mutation_lock`, atomare Writes,
  Zsh-Syntaxprüfung, Secret-Rechte `600`.
- **CLI-Architektur:** `cli.py` = Typer-Wiring; `command/<cmd>.py` = `run`;
  Hilfetexte in `main.py`; `status`-Default bei Gruppen ohne Argument.
- **Qualität:** ruff, basedpyright, Coverage ≥ 90 %, e2e-Suite (Docker), `0.1.0`.

### 2.2 Feste Inhalte

- **Zusage-Zeile (README):** „Lion requires no root and modifies no system files.
  Its only writes are inside Lion's own directories and one marker-delimited
  block in your shell startup file."
- **Non-Goals:** kein Server/Remote (MCP lokal/stdio); keine Sicherheitsgrenze
  (`dog` = Guardrail); kein Dotfile-/System-Config-Manager (nur Zsh, ein
  markierter Einschub); kein Paket-/Systemmanager.

## 3. Deliverable B — `ROADMAP.de.md`

**Legende:** erledigt / läuft / geplant / offen.
**DoD je Phase:** Feature + zugehörige `doctor`-Checks + Tests + Doku.

- **Phase 0 — Fundament (erledigt):** Collector v1, `scan`/`status`/`history`/
  `diff`, Storage-Validierung, JSON, `shlib`, `--version`, e2e.
- **Phase 1 — `doctor` v1 + Kern härten:** Check-Framework (Registry, Status,
  `--json`, `--fix` golden-rule) + Bestands-Checks; dazu GPU-Identität splitten,
  `shlib` Install→Uninstall→Install-Semantik, Config-Datei.
- **Phase 2 — Doku & Definition:** beide Dokumente, README restrukturieren,
  Sprachregel in `AGENTS.md`, Write-Boundary-Konzept-Doku.
- **Phase 3 — Wiki v1 („dumm"):** `wiki status/sync`; aktueller State + `shlib`;
  **OKF v0.2 zuerst definieren**; generisch gegen das Ledger-Modell.
- **Phase 4 — Shell-Integration & Recordings:** `shell` (Hook ein/aus),
  `watch start/stop`, Recording-Datenmodell, Lese-Sicht; danach Wiki v2.
- **Phase 5 — Guard:** `watch dog` (Regex-Qualifikation), Regex-Herkunft/Config.
- **Phase 6 — MCP:** `serve` (tools/resources/prompts), Secret-Redaktion.

## 4. Implementierungs-Tasks (Reihenfolge)

1. `PROJECT_DEFINITION.de.md` nach Abschnitt 2 anlegen (deutsch).
2. `ROADMAP.de.md` nach Abschnitt 3 anlegen (deutsch).
3. `AGENTS.md` (Projekt-spezifische Regeln) ergänzen: Sprachregel-Ausnahme +
   englische Ein-Zeiler-Links auf beide Dokumente.
4. Widerspruchsfreiheit prüfen: README, `--help`, Definition — Zusage-Zeile nur
   einmal kanonisch; keine konkurrierenden Begriffsdefinitionen.
5. Offene Design-Fragen in der Roadmap als „offen" markieren, nicht entscheiden.

## 5. Validierung

- Beide Dateien im Root, deutsch, versioniert, nicht in `.gitignore`.
- Keine inhaltlichen Widersprüche README/`--help`/Definition.
- `AGENTS.md` verlinkt beide Dokumente mit englischem Ein-Zeiler.
- `just check` bleibt grün (nur Doku; ruff schließt `.kilo` aus, Root-Markdown
  wird nicht gelintet).

## 6. Offene Design-Fragen (in der Roadmap als „offen" führen)

1. **OKF v0.2:** externer Standard oder eigenes Format? — blockiert Phase 3.
2. Recording-State: Geschwister von Maschinen-State oder getrennter Datenraum?
3. `shell insert`-Ziel bei aktivem `shlib` (Vorschlag: `~/.shlib/shlibs/`).
4. Wie liest man Recordings (z. B. `lion recordings` / `watch --list`)?
5. `dog`-Regexe: Herkunft, Format, Config.
6. Config-Datei: Umfang/Ort/Format.
7. Verb-Vokabular vereinheitlichen (`install/uninstall` vs. `start/stop` vs.
   `insert/remove`).
8. Deutsche JSON-Feldnamen (`erstscan`, `zuletzt_bestaetigt`, `ereignis`)
   beibehalten oder migrieren?

## 7. Nicht in diesem Plan

- Umsetzung von `doctor`/`wiki`/`shell`/`watch`/`serve`.
- Entscheidungen zu Abschnitt 6 — trifft der Maintainer vor der jeweiligen Phase.
