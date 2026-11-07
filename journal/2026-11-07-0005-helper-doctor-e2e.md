# 2026-11-07-0005 – Helper-Konsolidierung und `doctor`-E2E

## Stand

CLI-, Farb-, Hash- und FS-Helfer stammen aus je einer Quelle:
`lion/output.py`, das neue `lion/cli_common.py` und `lion/shlib/files.py`.
`lion doctor` ist jetzt über den installierten Entrypoint end-to-end getestet
(Install, Read-only, `--dry-run`, `--fix`, Exit-Codes, Secret-Rohwert-Abwesenheit).

## Änderungen

- `output.py`: `short_digest(digest)` liefert die ersten 12 Hex-Zeichen ohne
  Ellipse; `short_hash` baut darauf auf. `color_enabled(no_color)` übernimmt
  die TTY-/`NO_COLOR`-Logik aus `doctor/render.py`.
- Neu `cli_common.py`: `fail(error, home=None, *, exit_code=2)` und
  `layout_or_exit(home, *, exit_code=2)`; `shlib` ruft beide mit `exit_code=1`.
- `dash`, `watch`, `doctor` und `shlib` nutzen die zentralen Helfer; lokale
  `_display`, `_display_text`, `_short_hash`, `_fail`, `_fail_message`,
  `_layout` und `status_color_enabled` sind entfernt.
- `shlib/files.py`: `exists`, `link_target` und `line_count` als gemeinsame
  FS-Helfer; `dash/registry.py` behält einen dünnen `DashError`-Adapter und
  `watch/dash.py` fängt `OSError` weiterhin zu `None` ab.
- `secret_prefixes._display_path` stützt sich auf `output.display_path`.
- Neu `tests/shlib/test_files.py`; `tests/shlib/test_status.py` prüft
  `output.color_enabled`.
- Neu `tests/e2e/test_doctor.py` mit drei Fällen: Install/dry-run/fix-Roundtrip,
  Secret-Rohwert-Abwesenheit und Exit-Codes für fehlende bzw. unbrauchbare
  Layouts.

## Tests

- `just check` grün: ruff, Format, basedpyright, Docs-Check und 432 Tests,
  Coverage `93.18 %`.
- `just audit`: keine bekannten Schwachstellen.
- `just test-e2e` (Docker): 13 Tests grün, darunter die drei neuen
  `doctor`-Fälle.
- Manuell: `lion shlib it` zeigt `sha256:<12 hex>` ohne `…`; `docs` unverändert.

## Entscheidungen

- Exit-Codes bleiben wie dokumentiert: `shlib` 1, alle anderen 2; der
  gemeinsame Helfer hat dafür einen `exit_code`-Parameter.
- `link_target` wirft `OSError`; jeder Aufrufer behält seine bisherige
  Fehlersemantik (`DashError` bzw. `None`).
- Der Quote-Scanner, `_complete_lock_name` und die Install-Meldung bleiben
  unverändert; `--deep`/Gitleaks-E2E bleibt mangels Gitleaks im Image offen.

## ADRs

Keine inhaltlichen Änderungen und keine neuen ADRs.

## Attention Points

Keine neuen Einträge; bestehende Einträge bleiben gültig.

## Offen

Echte Gitleaks-Läufe und der MCP-Stdio-Pfad bleiben manuell geprüft.
