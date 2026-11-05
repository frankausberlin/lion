# 2026-11-05 15:13 – Review-Fixes für shlib status und it

## Stand

Der lokale Review des shlib-Changes fand drei blockierende Punkte: eine leere
oder abgeschnittene Lock-Datei konnte den Bootstrap in `99-unsorted.sh`
kopieren, `status` verschwieg Drift bei inkonsistenter Installation, und die
`it`-Ausgabe nannte keine Byte-Zahl. Alle drei sind behoben; Gate und
Docker-E2E sind erneut grün.

## Änderungen

- `reference.py` validiert die Lock-Datei vor der Nutzung: nicht leer, mit
  Newline abgeschlossen und mit vollständigem Bootstrap-Block. Sonst bricht
  `it` ab, statt Bootstrap-Inhalte als Tail zu kopieren.
- `status.py` klassifiziert `.zshrc`-Drift auch dann, wenn die Installation
  inkonsistent ist, sofern eine Referenz verfügbar ist. Der Bootstrap-Fallback
  ohne Lock zeigt damit denselben Tail wie `it`.
- `ItPlan`/`ItReport` tragen `tail_bytes`; die CLI-Ausgabe nennt jetzt
  Zeilenbereich, Byte-Zahl und SHA-256.
- Tests ergänzen leere/abgeschnittene Lock-Dateien, Drift ohne Lock und die
  Byte-Zahl in Dry-Run und Erfolgsausgabe.

## ADRs

Keine inhaltlichen Änderungen. ADR-0001 und ADR-0002 bleiben unverändert.

## Attention Points

Keine neuen. Der bestehende AP „shlib it kopiert den .zshrc-Tail und ändert die
.zshrc nicht“ bleibt gültig.

## Offen

Die Review-Vorschläge zu Zielprüfung bei leerem Tail, Exportnamen-Validierung,
`zshrc: inconsistent` in der Doku und der zusammengeführten Zeilen-Zählung
bleiben bewusst offen.
