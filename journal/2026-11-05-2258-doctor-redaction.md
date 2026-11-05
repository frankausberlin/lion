# 2026-11-05 22:58 – `lion doctor` und Redaction-Kern

## Stand

`lion doctor` diagnostiziert shlib, Dash-Register, Locks und verdächtige
Geheimnisse read-only. `--dry-run` zeigt den Fix-Plan, `--fix` legt fehlende
lion-eigene Verzeichnisse und den Redaction-Key an und entfernt verwaiste
Locks. Dash-Links, geänderte Ziele, ungültige Namen und Geheimnisse werden nie
automatisch repariert. Ein neuer Redaction-Kern erkennt und maskiert
Geheimnisse ohne Laufzeitabhängigkeit.

## Änderungen

- Neues Paket `src/lion/doctor/`: Modell, Checks, Rendering und Typer-Kommando.
  `collect_diagnosis` bleibt Typer-frei und ist die Basis für einen späteren
  MCP-`diagnose`-Tool.
- Neues Modul `src/lion/redaction.py`: Provider-Regeln (AWS, GitHub, GitLab,
  OpenAI/Anthropic, Slack, Stripe, Google, PyPI, npm, JWT, PEM),
  Key-Namen-Denylist, Shell-/INI-Zuweisungen, URL-Credentials, Shannon-Entropie,
  Platzhalter-Allowlist, Maskierung und HMAC-Fingerprints.
- `ShlibLayout.redaction_key` und `~/.shlib/.redaction-key`: 32 zufällige Bytes,
  Modus `0600`, exklusiv erzeugt. `shlib install` legt den Key im bestehenden
  Rollback-Bereich an; `doctor --fix` erzeugt ihn nur bei fehlendem Key in einem
  echten, nicht symlinkten `.shlib`.
- `WatchFinding` trägt `kind`; `collect_dash_findings` und `collect_findings`
  akzeptieren `render_diffs=False`. `lion watch` bleibt byte-identisch.
- Doctor-Fixes sind auf fehlende Unterverzeichnisse (`0700`), den Key und
  verwaiste reguläre Locks über `unlock_entry` begrenzt. Fehlt `dash/`, wird es
  angelegt und die Orphan-Klassifikation auf einen späteren expliziten Lauf
  verschoben.
- `docs/doctor.md`, `docs/roadmap.md`, README-Abschnitt, aktualisierte
  `docs/dash.md`/`docs/shlib.md`, generierte CLI-Referenz und drei neue
  Attention Points.

## Tests

- Neue Suiten `tests/redaction/` und `tests/doctor/`; Install-Tests prüfen den
  Key und den Rollback bei Key-Fehlern; Watch-Tests prüfen `kind` und
  `render_diffs=False`.
- `just check`: 330 Tests grün, Coverage `92.10 %` (Gate `>= 90`).
- `just audit`: keine bekannten Schwachstellen.
- Manueller Wegwerf-Home-Smoke-Test: kaputter Dash-Link überlebt, verwaister
  Lock wird entfernt, gepaarter Lock bleibt, keine Dateiinhalte in der Ausgabe,
  zweiter `--fix`-Lauf ohne Aktion.

## Entscheidungen

- Doctor gibt niemals Werte, Diffs oder Exportinhalte aus; Findings nennen nur
  Pfad, Zeile, Keyname, Art, Confidence und Modus.
- Confidence ist ein heuristischer Prozentwert, keine kalibrierte
  Wahrscheinlichkeit; niedrige Werte bedeuten „nicht erkannt“, nie „sauber“.
- Fingerprints sind HMAC-SHA256 über den installationsgebundenen Key; es gibt
  keinen SHA-256-Fallback und keine Vergleichbarkeit über Reinstall, Maschine
  oder Key-Rotation hinweg.
- Ein fehlendes `shlibs/` wird als Fehler gemeldet, weil `--fix` das Verzeichnis
  anlegen kann, aber keinen Inhalt wiederherstellt.

## ADRs

Keine inhaltlichen Änderungen und keine neuen ADRs. Die bestehenden ADRs
bleiben unverändert.

## Attention Points

Neu: „doctor löscht keine Dash-Links“, „doctor gibt keine Dateiinhalte aus“ und
„Redaction-Fingerprints sind HMAC- und Key-gebunden“. Keine bestehenden
Einträge wurden obsolet.

## Offen

MCP-Ressourcen und das `diagnose`-Tool, maskierte Dash-Inhalte/-Diffs und
optionale Schreibaktionen samt `lion mcp --home` sind die nächsten
Roadmap-Schritte.
