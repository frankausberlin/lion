# Architecture Decision Records

Dieses Verzeichnis enthält Architekturentscheidungen als Dateien.

* Dateiname: `NNNN-entscheidung.md`, zum Beispiel `0001-wir-verwenden-zsh.md`.
  Der Name nennt die Entscheidung, nicht nur das Thema.
* Der Status steht im Dokument: `accepted`, `superseded by ADR-NNNN` oder
  `deprecated`.
* Revidierte Entscheidungen tragen ein `_`-Präfix; ihre Nummer bleibt vergeben.
* Akzeptierte ADRs sind inhaltlich unveränderlich. Änderungen entstehen als
  neue ADR, die die alte ersetzt oder ergänzt.

Der Projektnutzer legt ADRs an und pflegt sie; der Agent liest sie nur und
ändert sie nicht. `pyinit` erzeugt keine ADR; die erste ADR legt der Nutzer an.
