# Plan: `docs/` nach Diátaxis + englischer Public-Repo

## Ziel

1. Eine leichtgewichtige, englische `docs/`-Struktur nach **Diátaxis** (tutorials /
   how-to / reference / explanation) plus **ADRs** (`docs/decisions/`) einführen.
2. Die Detailprosa aus dem README in `docs/` migrieren und das README auf einen
   schlanken Einstieg kürzen.
3. Die beiden deutschen Planungsdokumente aus dem öffentlichen Repo nehmen
   (nach `ignore/`, untracked) und die neuen Anhänge A/B auflösen.
4. Die Regel „jede Aussage genau einmal" durchsetzen.

## Getroffene Entscheidungen

- Umfang: Struktur + Anhänge auflösen + README-Inhalte migrieren (README schlank).
- Sprache: `docs/` **englisch**; Deutsch bleibt allein die Sprache der Unterhaltung
  und der beiden privaten Planungsdokumente.
- ADRs: Prozess + Template + Seed-ADRs für bereits getroffene, tragende
  Entscheidungen. Offene Roadmap-Fragen bleiben Fragen, werden erst bei
  Entscheidung zu einem ADR.
- Deutsche Dokumente wandern nach `ignore/` (gitignored, untracked). Der
  öffentliche Repo ist damit vollständig englisch.
- Öffentliche Vision/Scope: README + `docs/` tragen sie allein; **keine** separate
  englische Vision-Datei (kein zweiter Kanon → kein Drift).

## Zielstruktur (öffentlich, englisch)

```
README.md                       # schlanke Einstiegstür: Pitch, Zusage, Quickstart, Links
CONTRIBUTING.md                 # + kurzer Docs-Abschnitt
AGENTS.md                       # Links auf deutsche Docs entfernt; docs/-Konventionen ergänzt
docs/
  index.md                      # Hub/Navigation (Quadranten + Einstieg)
  tutorials/
    getting-started.md
  how-to/
    manage-shell-config.md      # shlib-Aufgaben
    compare-states.md           # history/diff-Aufgaben
  reference/
    cli.md                      # Kommando-Verzeichnis; verlinkt `lion <cmd> --help`
    data-structures.md          # STATE, STATE_SHLIB, JSON-Formen (scan/status/history/diff)
    storage.md                  # Pfade, Dateien, Locks, Kompatibilität
    collectors.md               # Felder je Collector (host/hardware/packages)
  explanation/
    cli-conventions.md          # Mechaniken: Status-Default, Hilfe-Verhalten
    architecture.md             # Schichtung, CLI-Struktur, Terminology status/state
    collectors.md               # Collector-Modell (nie abbrechen, keine volatilen Felder)
    comparison-model.md         # created/confirmed/appended, RAM-Toleranz
    write-boundary.md           # kein root, Schreibgrenzen
  decisions/
    0000-template.md            # MADR-Mini
    0001-no-root-write-boundary.md
    0002-status-read-only-scan-writes.md
    0003-history-publication-os-link.md
    0004-memory-total-tolerance.md
ignore/                         # untracked (siehe .gitignore) — deutsche Planungsdocs
  PROJECT_DEFINITION.de.md
  ROADMAP.de.md
```

## Aufgaben (in Reihenfolge)

1. **Deutsche Docs aus dem Repo nehmen.**
   - `PROJECT_DEFINITION.de.md` und `ROADMAP.de.md` per Dateisystem-`mv` nach
     `ignore/` verschieben; die Löschung im Index stagen. Zielpfad ist
     gitignored → Dateien bleiben lokal, sind aber nicht mehr getrackt.
   - Interne Querverweise der beiden Dokumente (gleicher Ordner) bleiben gültig;
     gebrochene relative Links prüfen/korrigieren.
   - In `PROJECT_DEFINITION.de.md`: §9 Dokumentations-Schichtung auf die neue
     Realität aktualisieren (englische `docs/` Diátaxis + `docs/decisions/`,
     Definition/Roadmap nicht mehr im Repo) und beide Anhänge A/B entfernen
     (Inhalt wandert nach Aufgabe 3/4).

2. **`AGENTS.md` bereinigen.**
   - Abschnitt „Language and Project Documents": Markdown-Links auf
     `PROJECT_DEFINITION.de.md`/`ROADMAP.de.md` entfernen. Stattdessen: Die
     deutschen Planungsdokumente sind maintainer-lokale, **untracked** Dateien
     unter `ignore/` (im Fresh-Clone nicht vorhanden); lokal lesen, wenn präsent.
   - Kurzer `docs/`-Abschnitt: Diátaxis-Quadranten + wo neue Inhalte hingehören
     (Konzept→explanation, Aufgabe→how-to, Nachschlagen→reference,
     Entscheidung→decisions); Kommando-Tiefe bleibt kanonisch in `--help`.

3. **`docs/`-Gerüst anlegen** (englisch): `index.md` + leere Quadranten-Seiten aus
   der Zielstruktur mit Kurzintro und Links.

4. **README migrieren und kürzen.** Mapping:

   | README-Abschnitt heute | neuer Ort |
   |---|---|
   | CLI structure (Terminology status/state) | `docs/explanation/architecture.md` |
   | Zsh shell library | `docs/how-to/manage-shell-config.md` + `docs/explanation/write-boundary.md` |
   | Collectors (Prosa) | `docs/explanation/collectors.md` + `docs/reference/collectors.md` |
   | Comparison model | `docs/explanation/comparison-model.md` |
   | Stored states and `lion diff` | `docs/how-to/compare-states.md` + `docs/reference/cli.md` |
   | JSON output | `docs/reference/data-structures.md` |
   | Storage and compatibility | `docs/reference/storage.md` |
   | Development / e2e | kurzer README-Hinweis + Verweis auf CONTRIBUTING / E2E-README |

   README behält: Logo, WIP-Banner, 1-Zeilen-Pitch, **kanonische Zusage-Zeile**,
   knappe Getting-started-Tabelle, Quickstart, Link auf `docs/`, Lizenz,
   Dev-Kurzblock. Keine Detailprosa mehr.

5. **Anhänge A/B auflösen.**
   - *Mechaniken* (Status-Default, Hilfe-Verhalten) → `docs/explanation/cli-conventions.md`.
   - *Per-Kommando-Parameter* → **nicht duplizieren**; `docs/reference/cli.md`
     ist ein kuratiertes Verzeichnis (Kommando, Zweck, eine Zeile), das je Befehl
     auf `lion <cmd> --help` verlinkt (eine Quelle, kein Drift).
   - *Datenstrukturen* → `docs/reference/data-structures.md`; kaputter Code-Fence
     korrigieren (```` ```jsonc ```` o. Ä.), `STATE`, `STATE_SHLIB` und die
     scan/status/history/diff-JSON-Formen aufnehmen; Config als „geplant" markieren.
   - **Widerspruch auflösen:** Die Anhänge behaupteten, `status` schreibe. Es gilt
     das Ist-Verhalten: `status` ist strikt read-only; Schreiben
     (created/confirmed/appended) ist `scan` (`src/lion/command/status.py`,
     `src/lion/program/storage.py:329`). So in `comparison-model.md` und
     `cli-conventions.md` dokumentieren.

6. **ADRs einführen.** `docs/decisions/0000-template.md` (MADR-Mini: Titel,
   Status, Datum, Kontext, Entscheidung, Konsequenzen, Alternativen) plus
   Seed-ADRs: `0001` no-root/Schreibgrenzen, `0002` status read-only/scan schreibt,
   `0003` Historie via `os.link`, `os.replace` nur zur Bestätigung, `0004` einzelne
   RAM-Toleranz (1 MiB auf `hardware.memory_total_bytes`). Jeweils aus
   AGENTS.md-Invarianten abgeleitet, kurz und faktisch.

7. **`CONTRIBUTING.md`** um einen kurzen Docs-Abschnitt ergänzen (Diátaxis-Quadranten,
   ADRs, Deutsch/Englisch-Regel).

8. **Querverweise & Entdopplung.** Alle internen Links auf die neuen Orte
   umbiegen; doppelte Aussagen zwischen README/docs/AGENTS.md entfernen (je Aussage
   genau eine Pflegestelle).

## Nicht im Scope

- MkDocs/Website-Build, arc42-Canvas, C4/Structurizr.
- `CHANGELOG.md`/Keep a Changelog, separates Glossar.
- Inhaltliche Übersetzung der deutschen Dokumente.
- Änderungen an `--help`-Texten oder am Quellcode.

## Validierung

1. `git ls-files '*.md'` listet ausschließlich englische Dateien
   (README, CONTRIBUTING, AGENTS, `docs/**`).
2. `rg -n 'PROJECT_DEFINITION|ROADMAP'` außerhalb `ignore/` liefert **keine**
   Markdown-Links auf die verschobenen Dateien.
3. `git status --porcelain`: die zwei Löschungen gestaged, unter `ignore/` nichts
   getrackt.
4. README und `docs/index.md` rendern; relative Links auflösen (manueller Klick).
5. `just check` bleibt grün (nur Doku betroffen, keine Quelldateien).

## Risiken / Hinweise

- **Verlust der Git-Historie** für die zwei deutschen Docs (akzeptiert; v0.1).
- **Remote-Web-Agenten** verlieren den Repo-Zugriff auf die Planungsdocs
  (akzeptiert; der Maintainer reicht sie bei Bedarf manuell weiter).
- **Drift-Gefahr**: öffentliche Doku darf keine Scope-Zusagen enthalten, die nur
  in den privaten Docs stehen; die kanonische öffentliche Zusage bleibt die
  README-Zeile.

## Ausführung

Erfordert Datei-/Git-Operationen → mit einem implementierungsfähigen Agenten
ausführen. Dies ist ein reiner Planning-Agent.
