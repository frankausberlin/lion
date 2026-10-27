# 2026-10-27 21:36 – shlib install und uninstall

## Stand

`lion shlib install` und `lion shlib uninstall` sind implementiert und über
`src/lion/cli.py` registriert. Unit- und Smoke-Tests laufen unter
`tests/shlib/`; die Docker-Suite `tests/e2e/test_shlib.py` prüft den Ablauf mit
echtem zsh. CLI-Referenz, README, Detaildoku, ADR, Attention Points und dieser
Eintrag gehören zum selben Changeset.

## Änderungen

- Neues Paket `src/lion/shlib/` mit Bootstrap-Text, Layout, atomarem Schreiben,
  Install- und Uninstall-Plan sowie Typer-Sub-App.
- Installation kopiert die Original-`.zshrc` nach
  `~/.shlib/shlibs/00-original-zshrc.sh`, schreibt die Lock-Datei als Kopie der
  neuen `.zshrc` und ersetzt die Konfiguration atomar.
- Uninstall validiert Exports und Shlibs vollständig, schreibt eine flache
  `.zshrc` und benennt den alten Baum nach
  `~/.shlib.uninstalled-<UTC>` um. Kein Restore, kein `--purge`.
- `--home` und `--dry-run` für beide Befehle; `--force` nur bei `uninstall`
  für Drift oder eine fehlende Lock-Datei.
- Dockerfile installiert `zsh` und `diffutils`; E2E-Tests decken Laden,
  Reihenfolge, Drift-Diff, Force-Uninstall und erneutes Install ab.
- `docs/reference/cli.md` mit `just docs` neu erzeugt; Header-Quelle auf die
  Typer-App samt Sub-Apps erweitert.
- Neue Nutzerdoku `docs/shlib.md`, README-Abschnitt `## shlib`.
- `ADR-0001` und `AP-1`/`AP-2` dokumentieren Layout, Flatten-Semantik und den
  Umgang mit dem Home-Verzeichnis.

## Entscheidungen

- Original liegt in `shlibs/`, weil nur dieser Ordner gesourct wird.
- Lock-Datei ist eine Kopie der neuen `.zshrc`, nicht der alten.
- Uninstall flacht ab und sichert; die alte Datei wird nicht wiederhergestellt.
- Nur zsh und GNU `diffutils`; keine interaktiven Dialoge, keine Migration.

## Offen

- `dash` bleibt eine Idee für einen späteren Schritt.
- Eine Erweiterung des MCP-Servers ist nicht Teil dieses Changesets.
- Ein echter Push und ein Release bleiben beim Maintainer.
