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

Architekturentscheidungen liegen in `docs/adr/`; die Konvention steht in
`docs/adr/README.md`. Der Agent liest ADRs nur: Er legt keine an, ändert sie
nicht und nummeriert sie nicht um.

## Attention Points

<!--
Format eines Eintrags:

    ### Kurztitel

    - **Situation**: Genau ein Satz; wiederholt die Überschrift nicht.
    - **Handhabung**: Kurzer Satz oder knappe Liste.

Attention Points sind gegenwartsbezogen: keine Nummern, keine Tombstones und
keine Felder für beteiligte ADRs oder Kontext. Obsolete Einträge werden
ersatzlos gelöscht; die Git-Historie ist das Archiv.
-->

### lion shlib verändert das Home-Verzeichnis

- **Situation**: `lion shlib install` und `uninstall` schreiben in `~/.zshrc`
  und legen beziehungsweise verschieben `~/.shlib`, sodass ein falscher Lauf
  die Shell-Konfiguration eines echten Nutzers treffen kann.
- **Handhabung**: Vor jeder Änderung läuft ein read-only-Preflight; Symlinks
  und beschädigte Bäume brechen ab. Tests und manuelle Läufe verwenden
  `--home` mit einem Wegwerf-Home und nie das echte Home. `--dry-run` vor
  einem echten Lauf nutzen und `--force` nur bewusst einsetzen, wenn ein
  abweichendes oder ungelocktes `~/.zshrc` akzeptiert ist. Ein Fehler vor dem
  finalen Ersetzen wird zurückgerollt.

### uninstall restauriert keine alte .zshrc

- **Situation**: `lion shlib uninstall` erzeugt eine flache `.zshrc` aus
  Exports und Shlib-Dateien und sichert den alten Baum nur als Backup, sodass
  ein erwartetes „Restore“ nicht existiert.
- **Handhabung**: Dokumentation und Erfolgsausgabe sprechen von „flatten“,
  nennen den Backup-Pfad `~/.shlib.uninstalled-<UTC-Zeitstempel>` und
  versprechen kein Wiederherstellen. Wer die alte Datei zurückwill, nutzt das
  Backup bewusst manuell.

### secrets.sh im Backup ist Teil der neuen .zshrc

- **Situation**: `lion shlib uninstall` schreibt owner-only Exporte nicht
  inline, sondern nach `~/.shlib.uninstalled-<Zeitstempel>/secrets.sh` und
  sourced die Datei aus der neuen `~/.zshrc`.
- **Handhabung**: Marker ist `mode & 0o077 == 0` (`chmod 600`, auch `400`);
  `0644`/`0640` sind öffentlich. Keine Werte in Ausgabe oder Tests. Keinen
  Guard, keinen stabilen Pfad und keine Migration ergänzen. Doku und Ausgabe
  müssen das Backup als Teil der Shell-Konfiguration benennen; das Löschen ist
  Sache des Nutzers.

### shlib-Cleanup-Dateien sind Nutzersache

- **Situation**: `install` legt `00-original-zshrc.sh` an, `it` legt bei Bedarf
  `99-unsorted.sh` an; solange eine der beiden Dateien existiert, meldet
  `status` gelb und `it` lässt den Tail zusätzlich in der `.zshrc` laufen.
- **Handhabung**: Beide Dateien nie automatisch löschen und die `.zshrc` im
  Rahmen von `it` nie bearbeiten; `status` und `it` nennen nur den
  Handlungsbedarf. Ziel ist ein Tree ohne beide Dateien: Der Nutzer löst die
  Inhalte in sortierte `NN-*.sh` auf und löscht die Cleanup-Datei dann selbst.
  Eine neue `99-unsorted.sh` bleibt `0600`, bei bestehenden Gruppe-/Fremdrechten
  nur warnen. Ausgaben nennen Zeilenbereiche, aber nie Tail-Inhalte oder
  Exportwerte.

## Herkunft

- lupysta_version: `0.15.0`
- contract_version: `4`
