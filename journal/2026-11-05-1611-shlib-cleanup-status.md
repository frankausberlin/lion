# 2026-11-05 16:11 – Cleanup-Dateien machen shlib gelb

## Stand

`00-original-zshrc.sh` und `99-unsorted.sh` sind jetzt sichtbar als
temporäre Cleanup-Dateien behandelt. `lion shlib status` ist gelb und endet mit
Exit 1, solange eine der beiden existiert; eine Hinweiszeile nennt die noch
vorhandenen Dateien. `lion shlib` löscht oder verändert sie nicht, sondern
überlässt das Auflösen und Entfernen dem Nutzer.

## Änderungen

- `status.py` prüft beide Cleanup-Namen mit derselben Zustandslogik, meldet sie
  als `original`/`unsorted` und zählt sie zum Warn-Exitcode.
- `cli.py` rendert die neue `original`-Zeile gelb und ergänzt bei Bedarf eine
  gelbe `hint:`-Zeile mit den aufzulösenden Dateien; `it` ergänzt denselben
  Hinweis für `99-unsorted.sh`.
- Tests decken den gelben Zustand nach dem Install, nach `it` und den grünen
  Zustand nach dem Entfernen beider Dateien ab; der Docker-E2E-Test prüft
  zusätzlich den Hinweis.
- README, `docs/shlib.md` und die generierte CLI-Doku beschreiben die
  Cleanup-Dateien und die Verantwortung des Nutzers.
- Der Attention Point heißt jetzt „shlib-Cleanup-Dateien sind Nutzersache“;
  `it` bearbeitet die `.zshrc` weiterhin nicht.

## ADRs

Keine inhaltlichen Änderungen. ADR-0001 und ADR-0002 bleiben unverändert.

## Attention Points

„shlib-Cleanup-Dateien sind Nutzersache“ ersetzt den bisherigen AP zu `it`.

## Offen

Nach dem Entfernen der Cleanup-Dateien bleibt eine `.zshrc`-Drift gelb, bis der
Nutzer den Tail dort ebenfalls entfernt. Das ist beabsichtigt und dokumentiert.
