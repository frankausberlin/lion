# 2026-11-05 16:28 – shlib it entfernt den Tail aus der .zshrc

## Stand

`lion shlib it` kopiert angehängten Inhalt nach `99-unsorted.sh` und entfernt
ihn anschließend aus `~/.zshrc`. Nach einem erfolgreichen Lauf ist die Datei
byte-identisch zur Lock-Datei; der Tail läuft nur noch einmal. `99-unsorted.sh`
bleibt als Cleanup-Datei bestehen und ist weiterhin Nutzersache.

## Änderungen

- `it.py` plant den Rückschrieb der `.zshrc` auf die Referenz und schreibt sie
  nach dem Ziel atomar mit ihrem bisherigen Modus. Scheitert der Ziel-Write,
  bleibt die `.zshrc` unverändert; scheitert der Rückschrieb, nennt der Fehler
  den doppelten Lauf.
- `ItPlan`/`ItReport` tragen `zshrc_content`, `zshrc_mode` und `wrote_zshrc`.
- Die CLI meldet „Removed lines …; it now matches the lock file“ und behält den
  Cleanup-Hinweis für `99-unsorted.sh`. Die frühere Doppellauf-Warnung entfällt.
- Tests decken den exakten Lock-Zustand, den Modus-Erhalt, einen erneuten Lauf,
  bereits erfassten Tail, reine Whitespace-Tails und beide Schreibfehler ab. Der
  E2E-Test sourct die `.zshrc` nach `it` und erwartet genau einen Tail-Lauf.
- README, `docs/shlib.md`, CLI-Doku und der Attention Point beschreiben das neue
  Verhalten.

## ADRs

Keine inhaltlichen Änderungen. ADR-0001 und ADR-0002 bleiben unverändert.

## Attention Points

„shlib-Cleanup-Dateien sind Nutzersache“ bleibt; die Handhabung hält jetzt
fest, dass `it` ausschließlich den erfassten Tail entfernt.

## Offen

`00-original-zshrc.sh` und `99-unsorted.sh` werden weiterhin nicht automatisch
gelöscht; `status` bleibt bis zum Auflösen gelb.