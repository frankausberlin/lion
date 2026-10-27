# ADR-0002: Owner-only Exporte werden beim Uninstall abgespalten

- **Status:** accepted
- **Datum:** 2026-10-27

## Kontext

`lion shlib uninstall` schreibt heute alle Exporte aus `~/.shlib/exports/`
inline in die neue `~/.zshrc`. Die neue Datei behält den Modus der alten
Datei, ist in der Praxis also oft `0644` und damit für andere lesbar. Ein
Export, der nur für den Eigentümer lesbar ist, ist ein Nutzersignal für einen
Secret-Wert. Ihn beim Rückbau stillschweigend zu veröffentlichen, widerspricht
diesem Signal.

ADR-0001 regelt Layout, Bootstrap, Lock-Datei und die Flatten-Semantik. Diese
Entscheidung ergänzt sie nur für den Uninstall-Pfad; Installation, Bootstrap
und Lock-Verhalten bleiben unverändert.

## Entscheidung

Ein Export gilt als owner-only, wenn seine Berechtigungsbits keinen Zugriff für
Gruppe oder Andere erlauben (`mode & 0o077 == 0`, also `0600` oder `0400`).
`0644` und `0640` gelten als normale Exporte und bleiben inline.

Owner-only Exporte werden beim Uninstall nicht in `~/.zshrc` geschrieben.
Ihre Werte landen in `~/.shlib.uninstalled-<YYYYMMDDTHHMMSSZ>/secrets.sh` mit
Modus `0600`. Die neue `~/.zshrc` enthält nach dem öffentlichen Export-Block
einen Hinweis und eine aktive `source "$HOME/<backup>/secrets.sh"`-Zeile. Es
gibt keinen Guard, keinen `else`-Zweig, keinen stabilen Secret-Pfad und keine
Migration.

`secrets.sh` wird vor der neuen `.zshrc` innerhalb von `~/.shlib` geschrieben
und wandert mit dem abschließenden Rename in das Backup. Scheitert der Rename,
bleibt die Datei unter `~/.shlib/`, während die neue `.zshrc` bereits den
geplanten Backup-Pfad sourced; die Fehlermeldung nennt beide Orte.

Die Ausgabe von `uninstall` und `uninstall --dry-run` nennt Anzahl, Pfad,
Modus und die Marker-Regel, aber weder Exportnamen noch Exportwerte. Sobald
Secrets existieren, ist das Backup Teil der Shell-Konfiguration; Löschen oder
Verschieben ist Sache des Nutzers, und eine fehlende Datei meldet zsh beim
`source` selbst.

Ohne owner-only Exporte bleibt die erzeugte `.zshrc` byte-identisch zum
bisherigen Verhalten; `install` und der Bootstrap werden nicht angepasst.

## Konsequenzen

- Mit Secrets ist das Backup nicht mehr entbehrlich. Wer es löscht, bekommt
  bei jedem Shellstart eine zsh-Fehlermeldung; das Werkzeug fängt sie nicht ab.
- `0640` ist öffentlich. Die Erkennung hängt allein an den Modusbits; ein
  Werkzeug, das Modi auf `0644` normalisiert, macht aus einem Secret wieder
  einen Inline-Export.
- Exportwerte und -namen erscheinen nicht in der Ausgabe. Tests prüfen
  ausdrücklich, dass Secret-Werte weder in der `.zshrc` noch in der
  CLI-Ausgabe stehen.
- Die Originaldateien bleiben zusätzlich in `backup/exports/` erhalten.
- `install`, Bootstrap und Lock-Datei bleiben unverändert; die Regel gilt nur
  beim Uninstall.
- Es gibt keinen stabilen Secret-Pfad, keine Rotation, keine Verschlüsselung,
  kein `--purge` und keine interaktive Rückfrage.

## Alternativen

- **Weiterhin alle Exporte inline schreiben:** einfach, aber ein owner-only
  markierter Wert würde in einer oft `0644` lesbaren Datei veröffentlicht.
- **Stabiler Secret-Pfad außerhalb des Backups:** Das Werkzeug übernähme
  dauerhaft die Verwaltung der Secrets und müsste Rotation, Migration und
  Konflikte lösen; das ist nicht Teil dieses Werkzeugs.
- **Guard mit Fallback oder Warnung im `source`-Block:** Ein fehlendes Backup
  würde verdeckt. Die zsh-Fehlermeldung ist die gewünschte Sichtbarkeit.
- **Eigener `secrets/`-Ordner im aktiven Baum:** Das ändert den
  Nutzer-Workflow und die Installationsstruktur, obwohl der Uninstall nur den
  Rückbau betrifft.
- **Strikte Gleichheit mit `0600`:** `0400` ist ebenfalls nur für den
  Eigentümer lesbar und wäre sonst fälschlich öffentlich.
- **Abbrechen oder `--force` bei Secrets:** Passt nicht zum Modell eines
  expliziten, nicht interaktiven Rückbaus; der Nutzer behält die Kontrolle über
  das Backup.

## Verweis

Diese Entscheidung ergänzt ADR-0001. Dessen Installations-, Layout- und
Flatten-Entscheidung bleibt gültig; ADR-0001 wird nicht ersetzt.
