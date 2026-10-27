# Agent-Tests (opt-in)

Dieses Verzeichnis ist das Skelett für die Provider-Suite. Opt-in bedeutet
hier: Sie ist nicht Teil von `just check` und läuft ausschließlich manuell über
`just test-agent`; sie ist nie Teil der CI. Es enthält bewusst noch keine
`test_*.py`-Dateien und keinen Provider-Adapter. `just test-agent` meldet in
diesem Zustand einen Hinweis und beendet sich mit Exitcode 0, ohne `uv` oder
einen Provider aufzurufen.

## Zweck

Agent-Tests prüfen Verhalten, das von einem Modell oder Provider abhängt. Sie
ergänzen das deterministische Gate, ersetzen es aber nicht. Modellabhängige
Qualität ist keine stabile Release-Bedingung.

## Status

Das Gerüst ist ein Skelett, kein funktionierender Harness. Vor dem ersten
echten Lauf müssen Projekt und Szenarien den Harness implementieren:

- Provider-Client, Modell und Endpoint,
- Zugangsdaten aus der Umgebung,
- Zeit-, Schritt- und Kostenlimits,
- Report- und Transkriptablage.

Diese Teile sind projektspezifisch und gehören nicht in ein generisches
Template.

## Gate und Aufruf

- `tests/conftest.py` schließt `agent` aus dem normalen pytest-Lauf aus,
  solange `LUPYSTA_AGENT` nicht `1` ist.
- `tests/agent/conftest.py` bricht ab, bevor pytest die Testmodule importiert,
  wenn die Suite ohne Opt-in gesammelt wird. Bei einem opt-in-Lauf werden
  Skips – auch Collection-Skips – zu Fehlern.
- `just test-agent` ruft `LUPYSTA_AGENT=1 uv run pytest tests/agent -v -s` auf.
- Der Runner sucht rekursiv nach `test_*.py` unter `tests/agent/`, ohne
  Testmodule zu importieren. Suchfehler brechen laut ab; ein fehlendes
  Verzeichnis gilt als leere Suite. Verschachtelte Ablagen wie
  `tests/agent/providers/test_live.py` sind erlaubt.
- Agent-Tests laufen nie in der CI.

## Leerlauf-Semantik

| Zustand | Verhalten |
| --- | --- |
| Keine `test_*.py`-Dateien (auch verschachtelt) | Hinweis, Exitcode 0, kein `uv`, kein Provider. |
| Suite-Verzeichnis nicht durchsuchbar | Laute Fehlermeldung, Exitcode 1. |
| Dateien vorhanden, `uv` fehlt | Laute Fehlermeldung, Exitcode 1. |
| Test schlägt fehl | Exitcode 1. |
| Test oder Modul wird übersprungen | Fehler im opt-in-Lauf, kein stiller grüner Lauf. |

## Erster Test

1. Harness und Provider-Preflight im Projekt implementieren. Fehlende
   Voraussetzungen müssen laut fehlschlagen, nicht skippen.
2. Ein Szenario unter `tests/agent/test_*.py` – auch verschachtelt möglich –
   ergänzen.
3. `just test-agent` ausführen und Kosten, Laufzeit und Ergebnis dokumentieren.
4. Report und Transkript unter einem gitignored Artefaktpfad ablegen.

## Grenzen

- Kein generischer Harness, keine `agent.toml` und kein Report-Schema.
- Der Live-Lauf ist beratend und kein Release-Gate.
- Details stehen in `AGENTS.md` und im lupysta-Rezept `agent-tests`.
