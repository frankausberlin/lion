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
- `tests/redaction/` und `tests/doctor/` laufen in `just test` und verwenden nur
  Wegwerf-Homes. Die Doctor-Tests prüfen, dass keine Dateiinhalte in Ausgabe
  oder Findings landen und dass `--fix` ausschließlich lion-eigene Artefakte
  anfasst.
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
  `99-unsorted.sh` an und entfernt den kopierten Tail aus der `.zshrc`; solange
  eine der beiden Dateien existiert, meldet `status` gelb.
- **Handhabung**: `it` entfernt ausschließlich den erfassten Tail und schreibt
  die `.zshrc` atomar auf die Referenz zurück; jede weitere Bearbeitung ist
  verboten. `00-original-zshrc.sh` und `99-unsorted.sh` nie automatisch löschen;
  `status` und `it` nennen nur den Handlungsbedarf. Ziel ist ein Tree ohne beide
  Dateien: Der Nutzer löst die Inhalte in sortierte `NN-*.sh` auf und löscht die
  Cleanup-Datei dann selbst. Eine neue `99-unsorted.sh` bleibt `0600`, bei
  bestehenden Gruppe-/Fremdrechten nur warnen. Ausgaben nennen Zeilenbereiche,
  aber nie Tail-Inhalte oder Exportwerte.

### Dash-Links gehören dem Nutzer

- **Situation**: `~/.shlib/dash` ist ein vom Nutzer gepflegtes Register aus
  Symlinks; Lion liest nur Metadaten und schreibt ausschließlich Lock-Kopien
  unter `locks/<name>.lock`.
- **Handhabung**: Dash-Links nie anlegen, verschieben, retargeten oder löschen;
  `unlock` entfernt nur die Lock-Kopie. `dash lock` überschreibt vorhandene
  Locks ohne Rückfrage. Kaputte Links, Ordner und `not-a-link`-Einträge melden,
  aber nicht reparieren.

### 99-lion-watchdog.sh ist vollständig verwaltet

- **Situation**: `lion watch-dog on` schreibt
  `~/.shlib/shlibs/99-lion-watchdog.sh`, und dieses Skript läuft bei jedem
  Shellstart.
- **Handhabung**: `on` überschreibt die komplette Datei atomar, `off` löscht die
  komplette Datei; es gibt keinen Schutz vor manuellen Änderungen und keine
  Merge-Logik. `status` prüft nur die Existenz. Die Datei bleibt `0644`, und
  Ausgaben bleiben bei sauberem Zustand still.

### lion watch bleibt ohne Dash still

- **Situation**: Nach `lion shlib uninstall` kann eine geflachte `.zshrc`
  weiterhin `lion watch` enthalten, während `dash/` und `locks/` nur noch im
  Backup liegen.
- **Handhabung**: Fehlende `dash/`- oder `locks/`-Verzeichnisse sind still mit
  Exit 0; nur Drift, Lese- und Scanfehler erzeugen Ausgabe. Diese Stille nicht
  durch Warnungen oder Fehlermeldungen ersetzen.

### doctor löscht keine Dash-Links

- **Situation**: `lion doctor --fix` entfernt verwaiste Locks und legt fehlende
  Unterverzeichnisse an, während `dash/` ausschließlich aus Nutzer-Symlinks
  besteht.
- **Handhabung**: Nur `unlock_entry` für verwaiste reguläre Locks verwenden;
  Dash-Links nie anlegen, retargeten oder löschen. Nicht-reguläre Locks und
  kaputte Links nur melden, für Links höchstens einen `rm`-Hinweis ausgeben.
  Fixes verweigern, wenn `.shlib` fehlt, ein Symlink oder kein Verzeichnis ist.

### doctor gibt keine Dateiinhalte aus

- **Situation**: Doctor liest Exports, Shlib-Dateien und Lock-Metadaten, darf
  aber weder Werte noch Watch-Diffs noch Exportinhalte preisgeben.
- **Handhabung**: Findings und Ausgabe nennen nur Pfad, Zeile, Keyname, Art,
  Confidence und Modus. `collect_dash_findings` mit `render_diffs=False` nutzen;
  Tests sichern, dass kein Rohwert in Summary oder `repr` erscheint.

### Redaction-Fingerprints sind HMAC- und Key-gebunden

- **Situation**: Masken sollen dasselbe Geheimnis lokal wiedererkennen, ohne
  einen Wert oder einen stabilen globalen Hash zu verraten.
- **Handhabung**: HMAC-SHA256 mit dem 32-Byte-Key aus
  `~/.shlib/.redaction-key` (Modus `0600`, exklusiv erzeugt); kein
  SHA-256-Fallback. Fehlt der Key, nur `[REDACTED:<kind>]` ohne Fingerprint.
  Symlink, Verzeichnis, falsche Größe oder Lesefehler sind Fehler und werden
  nie automatisch ersetzt.

### MCP liefert nur Metadaten

- **Situation**: Künftige MCP-Ressourcen und das `diagnose`-Tool könnten Dateiinhalte oder maskierte Ausschnitte zugänglich machen.
- **Handhabung**: Nur Pfade, Zeilennummern, Namen, Arten, Zähler und Dateimodi liefern; `collect_diagnosis` unverändert wiederverwenden und keinen Wertezugriff ergänzen. Gitleaks bleibt Erkennungsstufe, keine Sicherheitsgrenze.

### Doctor-Token-Erkennung ist Best-Effort

- **Situation**: Ein leeres Security-Segment oder ein niedriger Confidence-Wert könnte als „sauber“ missverstanden werden.
- **Handhabung**: Ausgabe und Doku sagen `no suspected secrets found (best effort)`; keine Vollständigkeits- oder Garantieaussage ergänzen. Prefixe, sensible Keys, PEM, URL und JWT bleiben Heuristiken.

### Prefix-Overlay ist Nutzerkonfiguration

- **Situation**: `~/.shlib/secret-prefixes.txt` erweitert und deaktiviert Erkennungsregeln, wird aber nicht gesourct und wandert bei `uninstall` ins Backup.
- **Handhabung**: Nur über `load_prefixes` lesen und nie schreiben; Format `prefix [kind] [min_tail] [max_tail]`, `!prefix` deaktiviert; fehlende Datei heißt Defaults. Keine Werte in Meldungen.

## Herkunft

- lupysta_version: `0.15.0`
- contract_version: `4`

### dash-Vervollständigung bleibt still und nennt nur Namen

- **Situation**: `lion dash lock <TAB>` liest das Dash-Register, und Fehler
  oder Dateiinhalte in der Vervollständigung würden die Shell stören oder
  Werte preisgeben.
- **Handhabung**: Nur Namen lockbarer Einträge (`kind == file`) liefern;
  fehlendes, symlinktes oder unlesbares `dash/` und unbekanntes Home still als
  leere Liste behandeln; nie Inhalte, Ziele oder Fehlermeldungen ausgeben.
