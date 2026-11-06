# 2026-11-06-1708 – Tab-Vervollständigung für `lion dash lock`

## Stand

`lion dash lock <TAB>` vervollständigt direkte Dash-Einträge, deren Ziel eine
reguläre Datei ist. Die Vervollständigung nutzt Typer/Click; sie ist rein
lesend, nennt nur Namen und bleibt bei fehlendem oder unlesbarem `dash/` ohne
Fehler.

## Änderungen

- `lockable_entry_names` in `dash/registry.py` liefert die Namen, die
  `lock_entry` akzeptiert (`kind == file`), sortiert und ohne Inhalte zu lesen.
- `dash lock` erhält eine `autocompletion`-Funktion. Sie berücksichtigt ein
  vorangestelltes `--home`, fängt `DashError` und `ShlibError` ab und gibt dann
  eine leere Liste zurück.
- README und `docs/dash.md` beschreiben Aktivierung über
  `lion --install-completion zsh` und den Geltungsbereich der Vervollständigung.
- Neue Registry- und CLI-Tests decken Kandidatenfilter, Präfix, fehlendes
  `dash/` und die Einbindung über `_TYPER_COMPLETE_ARGS` ab.

## Tests

- `just check` (manuell ohne `just`): ruff, Format, basedpyright, Docs-Check
  und pytest grün; 422 Tests, Coverage `92.59 %`.
- Simulierter zsh-Lauf mit `_LION_COMPLETE=complete_zsh` und
  `_TYPER_COMPLETE_ARGS`: `config-o` ergänzt `config-of-app.cfg`, Verzeichnis-
  und Broken-Links werden nicht vorgeschlagen.

## Entscheidungen

- Nur lockbare Einträge werden angeboten; Broken-Links, Verzeichnisse und
  Nicht-Links erscheinen nicht als Kandidaten.
- Die Vervollständigung gibt bei jedem Layout- oder Lesefehler still eine leere
  Liste zurück, statt die Shell mit einer Fehlermeldung zu belasten.
- Es wird ausschließlich `list_entries` (Metadaten) wiederverwendet; keine
  Dateiinhalte und keine Ziele gelangen in die Kandidatenliste.

## ADRs

Keine inhaltlichen Änderungen und keine neuen ADRs.

## Attention Points

Neu: „dash-Vervollständigung bleibt still und nennt nur Namen“. Keine
bestehenden Einträge wurden obsolet.

## Offen

`lion dash unlock <TAB>` könnte später vorhandene Lock-Namen vervollständigen;
der aktuelle Auftrag betraf nur `lock`.
