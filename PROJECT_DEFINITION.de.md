# LION — Projektdefinition

> **Status:** lebendes Dokument (Entwurf v0.1, 2026-10-07).
>
> **Sprache:** Dieses Dokument und die Unterhaltung mit dem Maintainer sind
> deutsch. Code, Docstrings, README, `--help`-Texte und Commit-Messages bleiben
> englisch. Das ist die einzige bewusste Ausnahme zur Englisch-Regel des Projekts.
>
> **Verwandte Dokumente:** `README.md` (Überblick), `lion <cmd> --help`
> (kanonische Kommando-Tiefe), `ROADMAP.de.md` (Phasen und offene Entscheidungen).

## 1. Zweck, Geltung und Pflege

Diese Projektdefinition ist die einzige Quelle für **Vision, Scope,
Terminologie und Prinzipien**. Sie beschreibt das *Warum* und *Was*, nicht das
*Wie*.

- **README.md** beantwortet „Was ist LION, wie fange ich an?" auf Überblicksniveau.
- **`lion <cmd> --help`** ist die kanonische Tiefe je Kommando.
- **`ROADMAP.de.md`** beantwortet „in welcher Reihenfolge, mit welchem Stand".
- **Dieses Dokument** beantwortet „warum und was".

Bei Konflikt gewinnt die tiefere Quelle. Keine Aussage wird in mehreren
Dokumenten leicht abweichend wiederholt; die Zusage in Kapitel 4 ist kanonisch
und erscheint sonst nirgends neu formuliert.

**Pflege:** Änderungen an Vision, Scope, Terminologie oder Prinzipien werden
zuerst hier eingetragen, danach in der Roadmap und schließlich in README/
`--help` nachgezogen. Details werden hier nicht dupliziert.

## 2. Konzeptionelles Modell

Das konzeptionelle Modell steht bewusst am Anfang: Es definiert Rollen, Akteure,
Artefakte und Operationen, damit die folgenden Kapitel keine undefinierten
Begriffe verwenden.

### 2.1 Rollen und Akteure

| Rolle | Beschreibung |
|---|---|
| **Maintainer** | Mensch am Rechner; trifft Design-Entscheidungen, arbeitet deutsch mit dem Agenten. |
| **Agent** | KI- bzw. Web-Agent, der LION lokal oder über den MCP-Server (`serve`) nutzt. |
| **System** | Der Linux-Host, dessen Zustand beobachtet wird. |
| **LION** | Das Werkzeug selbst: lokal, ohne root, ohne Netzwerkdienst. |

### 2.2 Artefakte

| Artefakt | Beschreibung |
|---|---|
| **snapshot** | Persistierter Maschinen-State (TOML unter `$XDG_DATA_HOME/lion/history`). |
| **ref** | Stabile Referenz eines Snapshots (Dateiname ohne `.toml`). |
| **recording** | Eine `watch`-Aufzeichnung eines Start→Stop-Intervalls. |
| **Ledger** | Die Gesamtheit der Records (Snapshots und Recordings). |
| **shlib-Dateien** | Lion-eigene Dateien unter `~/.shlib/` (`exports/`, `shlibs/`, `dash/`). |
| **Zsh-Einschub** | Genau ein markierter Block in `~/.zshrc` bzw. ein Script unter `~/.shlib/`. |
| **Wiki** | OKF-v0.2-konforme Lese-Sicht über den Ledger (geplant, Phase 3). |
| **Config** | Lion-Konfiguration (geplant, Phase 1). |

### 2.3 Operationen

| Gruppe | Operationen |
|---|---|
| Erfassen | `scan` (Maschinen-State), `watch start`/`watch stop` (Recording) |
| Vergleichen/Lesen | `status`, `history`, `diff` |
| Shell-Integration | `shlib install`/`uninstall`/`status`, `shell insert`/`remove` |
| Diagnose | `doctor status`, `doctor <topic>`, `doctor --fix` |
| Lese-Sichten | `wiki sync`, `serve` |
| Guard | `watch dog` (Regex-Qualifikation kritischer Kommandos) |

### 2.4 Beziehungen

Der Ledger ist die zentrale Abstraktion: Er sammelt **Records**, die jeweils von
einer Erfassung erzeugt werden. Collector erzeugen **snapshots**, `watch` erzeugt
**recordings**. Alles, was liest, tut dies über den Ledger: `status`/`history`/
`diff` direkt, `wiki` und `serve` als Lese-Sichten. `doctor` steht quer dazu und
prüft den Zustand aller Subsysteme, ohne selbst zu erfassen.

### 2.5 Abgrenzung (Disambiguierung)

| Begriffspaar | Regel |
|---|---|
| **state** vs. **status** | `state` = interne Repräsentation; `status` = read-only Kommando, das den aktuellen mit dem letzten State vergleicht. |
| **shell session** vs. **recording** | „shell session" = die zsh-Prozesssitzung (PID, Login→Logout); `recording` = ein `watch`-Start/Stop-Intervall. |
| **observation** vs. **recording** | `observation` ist als künftiger *Collector* reserviert; `recording` ist das `watch`-Artefakt. |
| **scan** vs. **watch** | `scan` erfasst den Maschinenzustand zu einem Zeitpunkt; `watch` zeichnet Kommandos über ein Intervall auf. |
| **doctor status** vs. `lion status` | `doctor status` = Zustandsbericht aller Subsysteme; `lion status` = State-Vergleich. |

## 3. Vision

LION macht einen Linux-Arbeitsplatz für Menschen und KI-Agenten **beobachtbar**:
Es erfasst den Maschinenzustand als Historie vergleichbarer Snapshots, zeichnet
Shell-Aufzeichnungen auf, qualifiziert kritische Kommandos, verwaltet die
Zsh-Konfiguration und stellt die gesammelten Fakten als Wiki und über einen
MCP-Server bereit. Alles lokal, ohne root.

**Gemeinsamer Nenner:** LION ist ein lokales, append-only *Beobachtungs-Ledger*.
Collector erzeugen Maschinen-State, `watch` erzeugt Recordings; beide sind
validiert, referenzierbar und vergleichbar. `shlib`/`shell` bilden die
Shell-Integrationsschicht, `wiki`/`serve` sind Lese-Sichten darüber.

## 4. Produktprinzipien

- **Kein root, keine Systemdateien.** Alle Schreibzugriffe bleiben innerhalb der
  Lion-eigenen Verzeichnisse und der von LION verwalteten Shell-Startdateien.
- **Goldene Regel (Schreiben):** Jedes schreibende Kommando schreibt nur in
  eigene Verzeichnisse. Reparaturen berühren niemals fremde Dateien.
- **Genau ein Shell-Einschub:** LION fügt genau einen markierten Block in die
  Shell-Startdatei ein — oder, wenn die Shell-Library aktiv ist, ein Script unter
  `~/.shlib/`. Damit bleibt es bei einem einzigen Writer pro Datei.
- **Fail loud:** Keine stillen Auslassungen. Jede gespeicherte Datei wird
  validiert; beschädigte Einträge stoppen das Kommando mit Pfad statt übersprungen
  zu werden.
- **Read/Write-Trennung:** Reine Lese-Kommandos schreiben nie.
- **Deterministisch:** Keine volatilen Felder (Uhren, Temperaturen, Uptime) in
  Snapshots.
- **Keine Secrets in Ausgaben:** Redaktion und restriktive Rechte, wo nötig.

**Zusage (kanonisch):**

> Lion requires no root and modifies no system files. Its writes are confined to
> Lion's own directories and the shell startup files it manages.

## 5. Architekturüberblick

| Schicht | Inhalt |
|---|---|
| **Collector/State** | Erfassung des Maschinenzustands, Snapshot-Modell, Vergleich, Storage (`state/`, `program/`). |
| **Shell-Integration** | `shlib` (Config-Organisation) und `shell` (Hook) — die einzigen Schreiber der Shell-Startdatei. |
| **Lese-Sichten** | `wiki` und `serve` rendern den Ledger, ohne zu erfassen. |
| **Querschnitt `doctor`** | Registry von read-only Checks aus den besitzenden Modulen; aggregiert nur. |

Prinzip: Jede Schicht kennt nur ihre eigene Verantwortung. Collector wissen
nichts von Lese-Sichten; Lese-Sichten kennen nur das Ledger.

## 6. Was heute existiert (Bestand)

- **Collector-Framework:** `host`, `hardware`, `packages`; je `status`
  (`ok`/`unavailable`/`error`) plus `error`-Meldung. Ein fehlschlagender Collector
  bricht die Erfassung nie ab; keine volatilen Felder.
- **State-Modell:** TOML-Snapshots, `schema_version = 1`, `erstscan` /
  `zuletzt_bestaetigt`; strikte Validierung; kanonischer Vergleich mit 1-MiB-
  Toleranz nur auf `hardware.memory_total_bytes`; Referenzauflösung über Alias,
  Index, Präfix und ISO-Zeit.
- **Kommandos:** `scan` (schreibt), `status`/`history`/`diff` (read-only),
  überall `--json`, `--version`, `history --limit N`.
- **Storage:** XDG-Datenverzeichnis, Publikation via `os.link`, atomarer
  `os.replace` nur beim Bestätigen, `.history.lock`.
- **`shlib`:** `install`/`uninstall`/`status`; `~/.shlib/` mit `exports/`,
  `shlibs/`, `dash/`; Backups, `.zshrc.lock`-Referenz, `mutation_lock`, atomare
  Writes, Zsh-Syntaxprüfung, Secret-Rechte `600`.
- **CLI-Architektur:** `cli.py` verdrahtet nur Typer-Dekoratoren; die
  Kommandos liegen als `run` in `command/<cmd>.py`; Hilfetexte in `main.py`;
  Gruppen mit `status`-Subkommando führen es ohne Argument aus.
- **Qualität:** ruff, basedpyright, Coverage ≥ 90 %, e2e-Suite (Docker),
  Version `0.1.0`.

## 7. Kommandolandkarte

| Säule | Kommandos | Status | Zweck |
|---|---|---|---|
| Maschinen-State | `scan`, `status`, `history`, `diff` | existiert | Maschinenzustand erfassen, speichern, vergleichen |
| Shell-Config | `shlib` | existiert | `~/.zshrc` klein halten, Konfiguration nach `~/.shlib/` |
| Diagnose | `doctor` | geplant (Phase 1) | read-only Zustands- und Integritätsbericht |
| Wiki | `wiki` | geplant (Phase 3) | OKF-v0.2-Lese-Sicht über den Ledger |
| Shell-Integration | `shell` | geplant (Phase 4) | LION-Hook in Zsh einhängen/entfernen |
| Aufzeichnung + Guard | `watch` | geplant (Phase 4/5) | Recordings; `dog`-Modus als Guardrail |
| Agenten-Zugang | `serve` | geplant (Phase 6) | MCP-Server: tools/resources/prompts |

## 8. Non-Goals

- **Kein Server/Remote:** Alles läuft lokal; `serve` ist stdio-MCP, kein
  Netzwerkdienst.
- **Keine Sicherheitsgrenze:** `watch dog` ist ein Guardrail (Hinweis/
  Bestätigung), kein Sandboxing oder Enforcement.
- **Kein Dotfile-/System-Config-Manager:** nur Zsh, nur der eine markierte
  Einschub.
- **Kein Paket-/Systemmanager:** LION installiert oder verändert keine Pakete
  und keine Systemdateien.

## 9. Dokumentations-Schichtung

| Ebene | Ort | Inhalt |
|---|---|---|
| Überblick | `README.md` | Pitch, Getting-Started-Tabelle, Zusage-Zeile, Verweis auf `--help` |
| Kommando-Tiefe | `main.py` (Typer-Epilogs) | Optionen, Beispiele, Details — eine Quelle, kein Drift |
| Konzepte | `docs/` (nur bei Bedarf) | übergreifend: Vergleichsmodell, Storage-Format, Write-Boundary |
| Warum/Was | dieses Dokument | Vision, Scope, Terminologie, Prinzipien |
| Wann | `ROADMAP.de.md` | Phasen, Stand, offene Entscheidungen |

## 10. Technischer Rahmen

Die Entwicklung folgt dem **Luxurious Python Stack** (UV, direnv, ruff,
basedpyright). Workflow-Details und der `luxuspythonstack`-Skill liegen in
`AGENTS.md`; hier wird nur referenziert, nicht dupliziert.

## 11. Offene Fragen

Die offenen Design-Entscheidungen werden in `ROADMAP.de.md` geführt und dort pro
Phase als „offen" markiert. Sie werden hier nicht vorweggenommen, damit es genau
eine Pflegestelle gibt.



# Apendix A - Kommandos & Parameter
## Mechaniken
### Status-Defaul
§1. 'lion' ohne paramter führt 'lion status' aus.
§2. 'lion kommando' führt 'lion kommando status' aus, wenn das kommando einen status-sub-befehl besitzt
### Hilfe verhalten
§1. 'lion --help' zeigt hilfe zu lion an.
§2. 'lion kommando --help' zeigt die hilfe zu dem vorgestellen kommando
§3. 'lion kommando' zeigt die hilfe zu dem kommando an, wenn es kein subkommando status besitzt
## Kommando 'scan'
...


# Apendix B - Datenstrukturen
