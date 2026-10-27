# ADR-0001: `lion shlib` verwaltet die zsh-Konfiguration

- **Status:** accepted
- **Datum:** 2026-10-27

## Kontext

Die zsh-Konfiguration soll in kleine, sortiert geladene Dateien und exportierte
Werte aufgeteilt werden. Die bestehende `~/.zshrc` muss dabei erhalten bleiben,
Änderungen an der generierten Datei müssen auffallen, und ein Rückbau darf
weder Konfiguration verlieren noch eine alte Datei stillschweigend
überschreiben. Das Werkzeug läuft auf Linux, die Zielshell ist zsh; GNU
`diffutils` ist vorhanden. Der MCP-Server und der Befehl `hello` bleiben
unberührt.

## Entscheidung

`lion shlib install` legt `~/.shlib/{exports,locks,shlibs}` mit Modus `0700`
an, kopiert die bisherige `~/.zshrc` nach
`~/.shlib/shlibs/00-original-zshrc.sh` und ersetzt `~/.zshrc` durch einen
Bootstrap-Block. Der Bootstrap prüft zuerst die Lock-Datei, exportiert dann die
Dateien aus `exports/` und sourced anschließend alle `NN-*.sh` aus `shlibs/`
in sortierter Reihenfolge.

Die Lock-Datei `~/.shlib/locks/shlib-version-of.zshrc` ist eine exakte Kopie
der neuen `~/.zshrc`. Weicht die reale Datei ab, zeigt der Bootstrap einen
`diff`; so werden manuelle Änderungen sichtbar, ohne den Start zu blockieren.

Die Originaldatei liegt bewusst in `shlibs/`, nicht in `locks/`: Nur
`shlibs/` wird gesourct, in `locks/` würde die bisherige Konfiguration nie
geladen.

`lion shlib uninstall` liest alle Exporte und Shlib-Dateien vollständig ein,
validiert sie und schreibt daraus eine flache `~/.zshrc`. Danach wird der alte
Baum nach `~/.shlib.uninstalled-<UTC-Zeitstempel>` umbenannt. Der Rückbau
flacht ab und sichert; er stellt die frühere Datei nicht wieder her. Ein
vorhandener `shlibs/`-Ordner ist Pflicht. Weicht `~/.zshrc` von der Lock-Datei
ab oder fehlt die Lock-Datei, ist `--force` nötig.

Beide Befehle haben `--home` für Wegwerf-Homes und `--dry-run` für eine
vollständige, schreibfreie Preflight-Prüfung. Es gibt keine interaktiven
Dialoge und kein `--purge`.

## Konsequenzen

- Das Werkzeug verändert das Home-Verzeichnis. Preflight und atomare Writes
  verhindern halbe Dateien; Symlinks auf `~/.zshrc` und `~/.shlib` werden
  abgelehnt.
- `uninstall` ist kein Restore. Dokumentation und Ausgabe nennen den
  Backup-Pfad und sprechen ausdrücklich von „flatten“.
- Die Lösung ist zsh-only und benötigt GNU `diff`/`cmp`.
- Nicht-UTF-8-Bytes bleiben über `surrogateescape` erhalten; NUL-Bytes in
  Exportwerten sind nicht darstellbar und brechen ab.
- Es gibt kein Locking und keine automatische Migration eines vorhandenen
  `~/.shlib`-Baums.
- `dash` ist nur eine spätere Idee und nicht Teil dieser Entscheidung.

## Alternativen

- **Bootstrap-Block vor die bestehende `~/.zshrc` stellen:** Die alte Datei
  bliebe an Ort und Stelle, aber `uninstall` müsste sie aus einer gemischten
  Datei rekonstruieren, und die Ladereihenfolge wäre schwerer nachvollziehbar.
- **`~/.zshrc` durch einen Symlink ersetzen:** Dotfile-Manager und Editoren
  verhalten sich unterschiedlich; ein Symlink-Ziel könnte versehentlich
  überschrieben werden. Deshalb wird ein Symlink an dieser Stelle abgelehnt.
- **`~/.zshrc` in-place editieren:** Ein Fehler beim Schreiben hinterlässt eine
  halbe Konfiguration. Kopie plus atomarer Ersatz sind einfacher zu prüfen.
- **`~/.shlib` beim Uninstall löschen:** Das würde Exporte und Shlib-Dateien
  unwiederbringlich entfernen. Die Umbenennung kostet keinen zusätzlichen
  Code und erlaubt ein sofortiges erneutes `install`.
