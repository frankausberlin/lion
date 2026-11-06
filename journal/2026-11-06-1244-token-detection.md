# 2026-11-06-1244 – Dreistufige Token-Erkennung

## Stand

`lion doctor` erkennt verdächtige Token in drei Stufen: eine
benutzerpflegbare Prefix-Liste, nachgeschärfte Kontextregeln und optional
Gitleaks über `--deep`. Die 12 bisherigen Fehlalarme aus der zshrc sind als
Fixture abgedeckt und erzeugen keine Treffer mehr. Die Erkennung bleibt
Best-Effort.

## Änderungen

- Neues Modul `secret_prefixes.py` mit 22 eingebauten Provider-Prefixen,
  Overlay in `~/.shlib/secret-prefixes.txt`, `!prefix` zum Deaktivieren,
  sicherem Datei-Loader und `lion list-prefixes`.
- Stufe 1 sucht Prefixe nur in Wertbereichen: Zuweisungswerte, Shell-Quotes und
  der gesamte Inhalt einer Export-Datei. Längster Prefix, Wortgrenze,
  Tail-Mindestlänge, Platzhalter- und Wiederholungsfilter.
- Stufe 2 verliert die freie Entropie-Erkennung. Nur sensible Key-Namen,
  Provider/JWT, PEM, URL-Passwort und Dateinamen bleiben; Referenzen, Pfade,
  URLs und Placeholder werden übersprungen.
- Neues Modul `doctor/gitleaks.py` für `--deep`: Versionsprüfung,
  Pro-Datei-Lauf, Timeout, `--redact`, JSON nur mit RuleID und Positionen,
  keine stderr-Übernahme. Fehlt Gitleaks, bleibt der Lauf bei Info.
- Doctor lädt die Prefix-Konfiguration einmal, meldet `prefixes.invalid` und
  `gitleaks.*` im Security-Abschnitt. Leeres Security-Segment heißt jetzt
  `no suspected secrets found (best effort)`.
- `docs/doctor.md`, `docs/shlib.md`, `docs/roadmap.md`, README und die
  generierte CLI-Referenz aktualisiert.

## Tests

- Neue Prefix-Parser-, Stufen-1/2-, Gitleaks-Fake-Binary-, CLI- und
  MCP-Invarianten-Tests; die 12 zshrc-Zeilen liegen als Fixture unter
  `tests/redaction/corpus/`.
- `just check`: 419 Tests grün, Coverage `92.56 %`; `just docs-check` grün.
- Manueller Wegwerf-Home-Lauf: Fixture ergibt 0 Secret-Treffer, Security zeigt
  den Best-Effort-Text, `--deep` ohne Gitleaks info, mit Fake-Gitleaks grün.

## Entscheidungen

- Export-Dateiinhalte gelten als Wertbereich; nackte Tokens in Shlib-Dateien
  bleiben Gitleaks vorbehalten.
- Prefix/Provider/URL schlagen Gitleaks, Gitleaks schlägt sensible Zuweisung
  und Dateiname; bei gleicher Priorität gewinnt der längere Span.
- Gitleaks ist optional, nie automatisch und keine Sicherheitsgrenze.
- MCP liefert nur Metadaten; `Finding` hat kein Wertfeld.

## ADRs

Keine inhaltlichen Änderungen und keine neuen ADRs. Der empfohlene
ADR-Kandidat aus dem Plan bleibt Nutzersache.

## Attention Points

Neu: „MCP liefert nur Metadaten“, „Doctor-Token-Erkennung ist Best-Effort“ und
„Prefix-Overlay ist Nutzerkonfiguration“. Keine bestehenden Einträge wurden
obsolet.

## Offen

MCP-Ressourcen und `diagnose` (Roadmap-Schritt 2), maskierte Locks- und
Dash-Ziele (Schritt 3) sowie echte Gitleaks-Läufe als manueller Test. Ein
installiertes Gitleaks ohne parsebare Versionsausgabe meldet
`gitleaks.unsupported`.
