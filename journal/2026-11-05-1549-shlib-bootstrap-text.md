# 2026-11-05 15:49 – shlib-Bootstrap-Text angepasst

## Stand

Der in `~/.zshrc` und in die Lock-Datei geschriebene Bootstrap-Block entspricht
jetzt der vorgegebenen Fassung: gekürzter Lock-Kommentar, zweizeiliger
Lock-Check mit Backslash-Fortsetzung und zweizeilig umbrochener Exports-Loop.
Die Mechanik aus Pfaden, `cmp`, `diff`, Exporten und Shlibs ist unverändert.

## Änderungen

- `bootstrap.py` kürzt den Kommentar auf `# Shlib lock check` und teilt
  Lock-Check und Exports-Loop auf die vorgegebenen Zeilen auf.
- `tests/shlib/test_bootstrap.py` hält den exakten Block inklusive genau eines
  abschließenden Newlines fest und prüft ihn mit `zsh -n`.
- `zsh -n` liefert für syntaktisch gültige Blöcke mit `!` den Status 1 ohne
  Diagnose; der Test wertet daher leeres `stderr` als fehlerfrei und akzeptiert
  die Statuswerte 0 und 1.

## ADRs

Keine inhaltlichen Änderungen. ADR-0001 und ADR-0002 bleiben unverändert.

## Attention Points

Keine neuen. Der bestehende AP „shlib it kopiert den .zshrc-Tail und ändert die
.zshrc nicht“ bleibt gültig.

## Offen

Bestehende Installationen behalten ihre Lock-Datei mit dem alten Text; eine
Migration ist bewusst nicht Teil der Aufgabe.
