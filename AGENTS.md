# AGENTS.md – lion

Diese Datei enthält die projektbezogenen Informationen und Regeln. Die
Arbeitsweisen selbst stehen im lupysta-Skill; hier steht nur, was für dieses
Projekt gilt.

Verwende für die Entwicklung immer den lupysta-Skill.

## Projektprofil

- Projektname: `lion`
- Paketname: `lion`
- Projekttyp: App (CLI mit MCP-Server)
- Python-Version: `3.12`
- Quellpfad: `src/lion/`
- Testpfad: `tests/`

## Testing

- `just test` und `just check` führen die Unit-/Smoke-Tests aus. Die opt-in
  Suiten laufen dort nie.
- `tests/e2e/` ist die Docker-Suite. Nur mit `just test-e2e` ausführen, nie mit
  einem direkten `pytest` und nie auf dem Host. Der CI-Workflow führt sie bei
  Push und Pull Request automatisch aus, sobald Testdateien vorhanden sind.
- `tests/agent/` ist die opt-in Provider-Suite. Nur mit `just test-agent`
  ausführen; sie ist beratend und nie Teil der CI.
- Beide Runner suchen rekursiv nach `test_*.py`, ohne Testmodule zu
  importieren. Suchfehler brechen laut ab; ein fehlendes Suite-Verzeichnis gilt
  als leere Suite. Bei leerer Suite gibt es einen Hinweis und Exit 0. Sobald
  Testdateien existieren, sind Fehler echte Fehler.
- Das Verzeichnis-Gate steht in `tests/conftest.py`; die Suite-Guards brechen
  ab, bevor Testmodule importiert werden, wenn eine Suite ohne Opt-in
  gesammelt wird.
- `tests/test_mcp.py` prüft Tool und Resource über den In-Memory-`Client` des
  SDK sowie die CLI-Weiterleitung an den Server. `tests/test_smoke.py` prüft die
  CLI und stellt sicher, dass `hello` den Server nicht startet.
- `just run` führt den Befehl `hello` aus. `just mcp-dev` öffnet den gepinnten
  MCP Inspector auf Loopback über `npx` und startet dort `lion mcp`;
  dafür braucht es Node.js. Beides ist nicht Teil von `just check`. Der
  Inspector nutzt denselben installierten Projekt-Entrypoint, damit relative
  Paketimporte funktionieren.

## ADRs

Architekturentscheidungen liegen als Dateien in `docs/adr/`. Verschaffe dir mit
`ls -1 docs/adr` einen Überblick, falls das Verzeichnis existiert.

* Dateiname: `NNNN-entscheidung.md`, zum Beispiel `0001-wir-verwenden-zsh.md`.
  Der Name nennt die Entscheidung, nicht nur das Thema.
* Ein `ADR`-Präfix im Dateinamen ist nicht nötig; das Verzeichnis benennt die
  Dokumentart.
* Der Name reicht für den Überblick; lies eine aktive ADR, wenn ihre
  Entscheidung die Aufgabe berührt.
* Revidierte Entscheidungen tragen ein `_`-Präfix, zum Beispiel
  `_0001-wir-verwenden-zsh.md`. Ihre Nummer bleibt vergeben.
* Der Status steht im Dokument: `accepted`, `superseded by ADR-NNNN` oder
  `deprecated`.

## Attention Points

<!--
Format eines Eintrags:

    ### AP-1: Kurztitel

    - **Situation**: ...
    - **Handhabung**: ...
    - **Involvierte ADRs**: ...
    - **Kontext**: ...

Entfernte APs bleiben als `### AP-N: entfernt (YYYY-MM-DD)` stehen; ihre
Nummer wird nicht neu vergeben.
-->

### AP-1: lion shlib verändert das Home-Verzeichnis

- **Situation**: `lion shlib install` und `uninstall` schreiben in
  `~/.zshrc` und legen beziehungsweise verschieben `~/.shlib`. Ein falscher
  Lauf kann die Shell-Konfiguration eines echten Nutzers treffen.
- **Handhabung**: Vor jeder Änderung läuft ein read-only-Preflight; Symlinks
  und beschädigte Bäume brechen ab. Tests und manuelle Läufe verwenden
  `--home` mit einem Wegwerf-Home und nie das echte Home. `--force` nur
  bewusst einsetzen, wenn ein abweichendes oder ungelocktes `~/.zshrc`
  akzeptiert ist. `--dry-run` vor einem echten Lauf nutzen.
- **Involvierte ADRs**: ADR-0001
- **Kontext**: Die Befehle sind explizit, nicht interaktiv; ein Fehler vor dem
  finalen Ersetzen wird zurückgerollt.

### AP-2: uninstall restauriert keine alte .zshrc

- **Situation**: `lion shlib uninstall` erzeugt eine flache `.zshrc` aus
  Exports und Shlib-Dateien und sichert den alten Baum nur als Backup. Ein
  erwartetes „Restore“ gibt es nicht.
- **Handhabung**: Dokumentation und Erfolgsausgabe sprechen von „flatten“,
  nennen den Backup-Pfad und versprechen kein Wiederherstellen. Wer die alte
  Datei zurückwill, nutzt das Backup bewusst manuell.
- **Involvierte ADRs**: ADR-0001
- **Kontext**: Das Backup heißt `~/.shlib.uninstalled-<UTC-Zeitstempel>` und
  bleibt unverändert liegen; `install` kann danach sofort erneut laufen.

## Herkunft

- lupysta_version: `0.14.0`
- contract_version: `4`
