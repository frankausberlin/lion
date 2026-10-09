# System-State Härtung: Profil, Tool-Fakten, Struktur-Diff

## Goal

Maschinenzustand so modellieren, dass ein Wechsel der Beschleunigungsplattform
(z. B. ROCm → CUDA durch neuen GPU-Einbau) und ein fehlendes Werkzeug
(z. B. `nvidia-smi`) als **aussagekräftige, stabile Fakten** erfasst und
unterschieden werden — statt als Rauschen vieler Einzelwerte. Zusätzlich:
Strukturänderungen (Collector/Key/Listeneintrag hinzugefügt/entfernt) werden im
Diff sichtbar von reinen Wertänderungen getrennt.

Grundlage für den späteren `doctor`-Plan (dessen Collector-/Tool-Checks auf
Profil und Tool-Fakten aufsetzen).

## Scope

**In Scope**
- GPU-Identität splitten (`pci_id`, `vendor`, `driver`, `driver_version`) — deckt
  Roadmap Phase 1 ab.
- Abgeleitetes Profil im `hardware`-Collector: `gpu_vendor`, `compute_platform`.
- Neuer `tools`-Collector: Verfügbarkeit der externen Werkzeuge als Fakten.
- Struktur-Kennzeichnung im Diff (`added`/`removed` = strukturell,
  `changed` = Wert) inkl. Listen-Identität, damit ein GPU-Tausch als
  entfernt+hinzugefügt erscheint.

**Out of Scope** (bewusst, Folgeschritte)
- `doctor` v1 komplett (eigener Plan) inkl. `--fix`-Entscheidung, Exitcodes, Topics.
- Profilabhängige Policy „erforderlich vs. optional" (gehört zu `doctor`; siehe
  Decision 4).
- `rocm-smi`-Nutzung zur Versionsermittlung in `hardware` (nur Präsenz-Fakt).
- `tests/e2e/` — separate Session (siehe Validation).

## Decisions

1. **Additiv-tolerant, `schema_version` bleibt 1.** Das Envelope
   (`erstscan`/`zuletzt_bestaetigt`/`collectors`) ändert sich nicht; neue
   Collector und neue Felder kommen hinzu. `Snapshot.from_toml_dict` akzeptiert
   unbekannte Felder bereits (`src/lion/state/model.py:142`), bestehende History
   bleibt ohne Migration lesbar. Keine Versionserhöhung (die würde alte Einträge
   hart ablehnen, `src/lion/state/model.py:211`).
2. **`compute_platform` = Fähigkeit aus Hardware/Treiber**, nicht
   Tool-Verfügbarkeit. `nvidia`-Kernel-Treiber ⇒ `cuda`, `amdgpu` ⇒ `rocm`. So
   bleibt „NVIDIA-Karte vorhanden, aber `nvidia-smi` fehlt" sichtbar. Die
   tatsächliche CUDA-Version bleibt separat in `cuda_version` (Verfügbarkeit).
3. **Profil im bestehenden `hardware`-Collector**, Tool-Fakten in einem neuen
   `tools`-Collector (eigene Sektion statt Felder in `hardware`, weil
   Werkzeuge querschnittlich sind und `doctor` sie eigenständig prüfen wird).
4. **Policy „erforderlich/optional" wird hier NICHT implementiert.** Sie ist
   profilabhängig und wird erst von `doctor` konsumiert; jetzt gebaut wäre sie
   ungenutzter Code. Dieser Plan liefert nur die Datengrundlage (Profil +
   `tools.available`).
5. **Strukturell = `added`/`removed` vorhanden** (Collector, Key oder
   Listeneintrag); `changed` = Wert. Kein neues Diff-Datenformat nötig: die
   Kategorien existieren schon (`src/lion/program/diff.py:43`); ergänzt wird
   eine Auswertungs-Hilfe plus Listen-Identität.
6. **Profil-Granularität:** `gpu_vendor` und `compute_platform` sind Skalare
   (`nvidia|amd|intel|mixed|none|unknown` bzw. `cuda|rocm|mixed|none`). Details
   je Karte stehen in den `gpu`-Einträgen.

## Datenmodell (neu)

`hardware` (erweitert):

```toml
[collectors.hardware]
status = "ok"
error = ""
cpu_model = "..."
cpu_logical_cores = 8
memory_total_bytes = 16000000000
cuda_version = ""
gpu_vendor = "nvidia"          # nvidia|amd|intel|mixed|none|unknown
compute_platform = "cuda"      # cuda|rocm|mixed|none

[[collectors.hardware.gpu]]
pci_id = "0000:01:00.0"
name = "NVIDIA GeForce RTX 4090"
vendor = "nvidia"              # nvidia|amd|intel|unknown
driver = "nvidia"              # Kernel-Modul (lspci "Kernel driver in use")
driver_version = "550.54"      # aus nvidia-smi; "" bei Nicht-NVIDIA
memory_total_bytes = 25757220864
```

`tools` (neu; Modul `src/lion/state/tooling.py`, Collector-Name `"tools"`):

```toml
[collectors.tools]
status = "ok"
error = ""

[collectors.tools.available]
lspci = true
nvidia_smi = true
rocm_smi = false
apt_mark = true
zsh = true
```

Werkzeugliste (`name` normalisiert → Kommando): `lspci`, `nvidia_smi`,
`rocm_smi`, `apt_mark`, `zsh`. Ermittlung per `shutil.which` (billig); der
`tools`-Collector ist immer `status = "ok"` (er misst erfolgreich Fakten),
fehlende Tools sind Daten, keine Warnung.

## Ableitungsregeln

- `vendor` je GPU aus `driver`: `nvidia→nvidia`, `amdgpu|radeon→amd`,
  `i915|xe→intel`, sonst `unknown`.
- `gpu_vendor`: keine GPU → `none`; alle gleich → dieser Wert; nur `unknown` →
  `unknown`; mehrere verschiedene bekannte → `mixed`.
- `compute_platform`: aus Menge der Vendoren: `{nvidia}→cuda`, `{amd}→rocm`,
  beide → `mixed`, sonst `none`. (Approximation „amdgpu ⇒ ROCm-fähig";
  dokumentieren.)
- `gpu`-Liste nach `pci_id` sortieren, damit die Reihenfolge deterministisch ist.

## Tasks (geordnet)

1. **`src/lion/state/hardware.py`**
   - `GpuState` erweitern: `pci_id`, `vendor`, `driver`, `driver_version`
     (Semantik korrigieren: `driver` = Kernel-Modul, `driver_version` = aus
     `nvidia-smi`). `_PciGpu` und `_NvidiaGpu` entsprechend anpassen.
   - `_PciGpu`-Parsing: `driver` aus „Kernel driver in use", nicht als Version.
   - Vendor-Ableitung + `gpu_vendor`/`compute_platform` in `HardwareState`
     aufnehmen; `gpu` nach `pci_id` sortieren.
   - `_collect()`: Profil aus der GPU-Liste ableiten; `status`-Logik unverändert
     lassen (fehlendes optionales Tool ist kein `unavailable`).
   - Bestehende Failure-Unterdrückung (`hardware.py:230-236`, „nvidia-smi weg,
     aber lspci sieht nvidia") zunächst unverändert lassen; Kommentar um Bezug
     auf den neuen `tools`-Collector ergänzen.
2. **`src/lion/state/tooling.py` (neu)**
   - Werkzeugtabelle + `_collect()` mit `shutil.which`; exportiert
     `COLLECTOR = Collector(name="tools", collect=_collect)`.
3. **`src/lion/state/registry.py`**
   - `tools.COLLECTOR` an `COLLECTORS` anhängen.
4. **`src/lion/program/diff.py`**
   - Listen-Diff mit Identität: Listen von Tabellen mit gemeinsamem `pci_id`
     elementweise matchen (entfernt/hinzugefügt/geändert je Element, Keys wie
     `gpu[0000:01:00.0]`); skalare Listen (z. B. `manual`) per Wert-Menge
     behandeln; sonst wie bisher atomar. `value_equal` pro Elementfeld gilt
     weiter (RAM-Toleranz nur für `memory_total_bytes`).
   - Hilfsfunktion `has_structural_change(diff) -> bool` (irgendein
     `added`/`removed`).
5. **`src/lion/command/status.py` und `src/lion/command/diff.py`**
   - `--json`-Payload um `"struktur_geaendert": bool` ergänzen (bestehende
     deutsche Keys beibehalten).
   - Textausgabe: bei struktureller Änderung eine führende Zeile
     „Struktur geändert." ergänzen (restliche Ausgabe unverändert).
   - `scan` unverändert: Strukturänderung ist bereits ≠ gleich und führt
     automatisch zu `appended` (`src/lion/state/model.py:115`).
6. **Tests** (siehe unten).
7. **Doku** (siehe unten).
8. **Session-Doku**: `SESSION.md` überschreiben, `JOURNAL.md` datiert ergänzen.

## Tests

Deterministisch, Fixtures/Mocks statt echter Tools (Vorgabe aus `AGENTS.md`:
keine Abhängigkeit von Entwickler-GPUs).

- `tests/test_hardware.py`: Vendor-Ableitung; `gpu_vendor`/`compute_platform`
  für none/einzeln/gemischt/unknown; `pci_id`-Sortierung; `driver` vs.
  `driver_version` (nvidia-smi gemockt); fehlendes `nvidia-smi` ⇒
  `compute_platform = "cuda"` bleibt, `cuda_version = ""`.
- `tests/test_tooling.py` (neu): `shutil.which` gemockt, `available`-Mapping und
  `status = "ok"`.
- `tests/test_diff.py`: GPU-Tausch (andere `pci_id`) ⇒ `removed`+`added`, nicht
  ein atomares `changed`; gleiche `pci_id`, geänderte `memory_total_bytes` ⇒
  `changed`; `has_structural_change` für Key-Add/Remove vs. reiner Wertwechsel.
- `tests/test_smoke.py`: `status --json`/`diff --json` enthalten
  `struktur_geaendert`; alte Snapshot-Form (ohne neue Felder/Tools) lädt weiter
  und erzeugt beim `scan` genau einen neuen Eintrag.
- Regressionstest: bestehende History im alten Format bleibt validierbar
  (additive Toleranz).

## Doku

- `docs/reference/collectors.md`: neue `hardware`-Felder + `tools`-Collector.
- `docs/reference/data-structures.md`: neue JSON-Felder, `struktur_geaendert`.
- `docs/explanation/collectors.md`: Fähigkeit vs. Verfügbarkeit (Profil vs.
  Tool-Fakt).
- `docs/explanation/comparison-model.md`: strukturell vs. Wert.
- Optional ADR `docs/decisions/0006-system-profile-and-structural-diff.md`.
- `docs/reference/cli.md`: **keine** CLI-Änderung ⇒ generierte Datei bleibt;
  `just docs-check` bestätigt das.

## Validation

- `just check` grün (ruff, basedpyright, Docs-Check, pytest, Coverage ≥ 90%).
- Gezielt: `uv run pytest tests/test_hardware.py tests/test_diff.py tests/test_tooling.py -s`.
- `just test` für Coverage.
- **`just test-e2e`: separate Session** (Änderung betrifft Snapshot-Form/CLI-
  Lifecycle, daher dort nachziehen; nicht Teil dieses Plans).
- Manuell/„trocken": `uv run lion scan` in einem isolierten `XDG_DATA_HOME`
  gegen einen Altbestand prüfen (kein Schreiben an echten Daten).

## Risks / Edge Cases

- **Diff-Format-Änderung für Listen**: `added`/`removed` enthalten jetzt
  Objekte statt atomarer Werte; bestehende Tests/Annahmen anpassen.
- **Namenskollision**: neues Modul `state/tooling.py` neben dem Runner
  `state/tools.py` — bewusst getrennt halten (Runner ≠ Collector).
- **Approximation** `amdgpu ⇒ rocm`: als Fähigkeit dokumentiert; nicht als
  „ROCm installiert" interpretieren.
- **Altbestand**: fehlende neue Felder dürfen die Validierung nicht brechen
  (additiv). Neue Felder erscheinen beim ersten Scan nach Upgrade einmalig als
  strukturelle Ergänzung.
- **Determinismus**: `gpu` sortiert, sonst künstliche Struktur-Diffs.

## Follow-up (nicht Teil dieses Plans)

- `doctor` v1: Registry read-only Checks, `ok/warn/error`, `--json`, Exitcodes,
  Topics, `--fix`-Entscheidung; nutzt `gpu_vendor`/`compute_platform` +
  `tools.available` für die profilabhängige „erforderlich/optional"-Policy.
