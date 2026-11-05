# 2026-11-05 14:42 – shlib status und copy-only shlib it

## Stand

`lion shlib status` zeigt die Installation rein lesend; `lion shlib it` kopiert
angehängte `.zshrc`-Zeilen nach `~/.shlib/shlibs/99-unsorted.sh`, ohne die
`.zshrc` zu verändern. Bewusst nicht gebaut: `restore`, automatisches Entfernen
des Tails und Änderungen an `install`/`uninstall`. Unit-, CLI- und Docker-E2E-
Tests sind grün.

## Änderungen

- `reference.py` lädt die Lock-Datei oder fällt auf `BOOTSTRAP` zurück und
  schneidet nur angehängten Inhalt ab; andere Abweichungen brechen ab.
- `it.py` plant und schreibt den Tail mit Markern, SHA-256-Idempotenz und
  Modus `0600` für neue Ziele; bestehende reguläre Dateien behalten ihren
  Modus. `.zshrc` wird nie geschrieben.
- `status.py` zählt Exporte, owner-only Exporte, Shlibs und Backups, erkennt
  Drift und `99-unsorted.sh` und liefert Exitcodes `0`/`1`/`2`.
- CLI ergänzt `lion shlib status [--no-color]` und `lion shlib it [--dry-run]`
  samt Farbhelfern und Ausgabe ohne Tail-Inhalte, Exportnamen oder -werte.
- Tests decken Tail-Fälle, Idempotenz, Symlinks, fehlende Lock-Datei,
  Nicht-UTF-8/NUL, Statuszähler, Farbunterdrückung und Exitcodes ab.
- `docs/shlib.md`, README, `docs/reference/cli.md` und der neue AP sind im
  selben Changeset.

## ADRs

Keine inhaltlichen Änderungen. ADR-0001 und ADR-0002 bleiben unverändert und
wurden nur gelesen.

## Attention Points

Neu: „shlib it kopiert den .zshrc-Tail und ändert die .zshrc nicht“.

## Offen

- `--porcelain` ist nicht Teil dieses Schritts.
- `99-unsorted.sh` sortiert vor einem späteren `99-LAST.sh`; eine spätere
  Umbenennung oder neue Reihenfolge bleibt eine bewusste Entscheidung.
- Version, Tag, Push und Release bleiben beim Maintainer.
