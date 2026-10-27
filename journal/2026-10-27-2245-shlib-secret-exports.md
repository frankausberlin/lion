# 2026-10-27 22:45 – owner-only Exporte beim shlib uninstall

## Stand

`lion shlib uninstall` spaltet Exporte mit Modus `0600`/`0400` in eine eigene
`secrets.sh` im Backup ab und sourced sie aus der flachen `~/.zshrc`. Ohne
Secrets bleibt das Ausgabeformat byte-identisch. Unit-, CLI- und Docker-E2E-Tests
decken Split, Quotierung, Fehlerpfade und die Ausgabe ab. ADR-0002, AP-3, Doku
und dieser Eintrag gehören zum selben Changeset.

## Änderungen

- `flatten(layout, backup)` trennt öffentliche und owner-only Exporte; der
  gemeinsame Renderer behält `shlex.quote`, `surrogateescape` und die
  NUL-Prüfung für beide Gruppen.
- `secrets.sh` wird mit Modus `0600` vor der neuen `.zshrc` innerhalb von
  `~/.shlib` geschrieben und wandert mit dem Rename ins Backup.
- Die neue `.zshrc` enthält einen Hinweisblock und eine aktive
  `source "$HOME/<backup>/secrets.sh"`-Zeile ohne Guard, vor den Shlibs.
- CLI nennt bei Secrets Anzahl, Pfad, Modus `0600` und die Marker-Regel, aber
  keine Namen oder Werte; `--dry-run` nutzt die „would“-Form.
- Tests setzen Exportmodi explizit und prüfen Split, Symlink-Zielmodus,
  Backup-Suffix, Schreib-/Rename-Fehler und das Fehlen von Secret-Werten in
  Ausgabe und `.zshrc`.
- `docs/shlib.md`, README, ADR-0001-Verweis, ADR-0002 und AP-3 ergänzt;
  `docs/reference/cli.md` mit `just docs` neu erzeugt.

## Entscheidungen

- Marker ist `mode & 0o077 == 0`; `0640` ist öffentlich.
- Ablage ist der Backup-Ordner, kein stabiler Pfad; keine Rotation, keine
  Verschlüsselung und keine Migration.
- Mit Secrets ist das Backup Teil der Shell-Konfiguration; das Werkzeug
  informiert nur und fängt eine fehlende Datei nicht ab.

## Offen

- `dash` bleibt eine spätere Idee.
- Push, Tag und Release bleiben beim Maintainer.
