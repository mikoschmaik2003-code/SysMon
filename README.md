# SysMon — Multimonitor (App 1)

Task-Manager-Ersatz auf **einem** Bildschirm: alle 24 logischen CPU-Kerne mit
Takt pro Kern, die komplette GPU-Sektion (3D, Encode, Decode, VRAM, Temp, Power,
Takt, Lüfter), RAM, alle Datenträger mit MB/s und IO/s sowie Netzwerk — plus eine
zweite, komplett andere Optik im Linux-Terminal-Stil.

## Installation

Läuft nur unter **Windows 10/11**. Gebraucht wird
[Python 3.10 oder neuer](https://www.python.org/downloads/) — beim Installieren
„Add python.exe to PATH" anhaken. Danach einmalig:

```bash
pip install -r requirements.txt
```

## Starten

```bash
SysMon.bat
```

(oder `python app.py` — braucht nur `psutil`, GUI ist tkinter, alles andere direkt
über Windows-/NVIDIA-DLLs)

## Zwei Ansichten

| | |
|---|---|
| **GRAFIK** | Task-Manager-Optik: gefüllte Verlaufskurven, 60-Sekunden-Fenster, ein Mini-Graph pro logischem Prozessor inkl. dessen aktuellem Takt |
| **TERMINAL** | monospace ASCII-Balken (`████░░░░`) mit denselben Metrikfarben, dazu Top-Prozesse nach CPU, RAM, GPU und VRAM (MB je App) nebeneinander |

## Tasten

| Taste | Funktion |
|---|---|
| `G` / `T` / `Leertaste` | Grafik / Terminal / umschalten |
| `1` `2` `3` | Intervall 0,5 s / 1 s / 2 s |
| `O` | Fenster immer im Vordergrund |
| `F11` / `Esc` | Vollbild an/aus |
| `Strg+Q` | beenden |

Fenstergröße, Ansicht, Intervall und „im Vordergrund" werden beim Beenden in
`sysmon.json` gespeichert.

## Farben

Wie im Task-Manager: CPU blau, RAM blau-violett, Datenträger grün (Lesen) / hellblau
(Schreiben), Netzwerk pink, GPU violett, VRAM dunkelviolett. Prozentzahlen werden
zusätzlich grün → gelb → rot eingefärbt.

## Aufbau

| Datei | Inhalt |
|---|---|
| `app.py` | Fenster, Kopfleiste, Layout, Tastenkürzel |
| `sampler.py` | drei Hintergrund-Threads: schnelle Werte (CPU/RAM/Disk/Netz/Takt), GPU, Prozessliste |
| `winapi.py` | direkte Windows-/NVIDIA-Schnittstellen (PDH, NVML, ntdll) — siehe Performance |
| `views.py` | die fünf Grafik-Panels |
| `terminal.py` | die Terminal-Ansicht |
| `draw.py` | Zeichen-Helfer im Retained-Mode (`Scene`) |
| `theme.py` | Farbschema |

## Performance

Ein Bild kostet **~6 ms** (Grafik) bzw. **~8 ms** (Terminal) — vorher waren es über
200 ms, was als Ruckeln sichtbar war. Vier Dinge waren dafür nötig:

| Problem | vorher | jetzt |
|---|---:|---|
| `psutil.process_iter` mit Attributen (macht pro Prozess einen kompletten System-Snapshot) | 2 500 ms | **7 ms** — alles aus einem `NtQuerySystemInformation` (`winapi.ProcTable`) |
| Canvas-Items bei jedem Bild neu erzeugen (~0,2 ms pro Item) | 215 ms | **1,5 ms** — `draw.Scene` legt jedes Item einmal an und schickt danach nur Änderungen |
| GPU-Werte über den Subprozess `nvidia-smi` | ~150 ms | **5 ms** — `nvml.dll` direkt (`winapi.Nvml`) |
| Terminal-Text Segment für Segment einfügen | ~250 Tcl-Aufrufe | **~15** — ein String, dann pro Farbe ein `tag_add` |

Der Takt je Kern kommt aus dem PDH-Zähler `% Processor Performance` mal Basistakt —
dieselbe Quelle wie im Task-Manager, deshalb stimmen die GHz-Werte exakt überein.
Die GPU-Last je Prozess kommt aus dem Zähler `GPU Engine(*)`, ebenfalls genau wie dort.
Der VRAM je Prozess kommt aus `GPU Process Memory(*)\Dedicated Usage` — das ist die
Spalte „Dedizierter GPU-Speicher" im Task-Manager (NVML kann das unter Windows nicht).

## Hinweise

- Der Takt pro Kern steht neben dem Kernnamen in jeder Mini-Kachel (Grafik) bzw.
  hinter jedem Balken (Terminal).
- „Aktiv %" der Datenträger kommt aus `read_time`/`write_time` — bei NVMe sind das
  realistisch < 1 %, auch wenn viele MB/s fließen. Deshalb stehen MB/s und IO/s daneben.
- GPU-Werte brauchen eine NVIDIA-Karte (`nvml.dll` kommt mit dem Treiber); fehlt sie,
  wird auf `nvidia-smi` zurückgefallen, ohne Karte bleibt nur das GPU-Panel leer.
- Die Prozessliste ist auf die Gesamt-CPU normiert (wie im Task-Manager), nicht auf
  einen Kern; der RAM-Wert ist der private Arbeitssatz — ebenfalls wie dort.
- Ist das Fenster zu schmal für vier Top-Prozess-Spalten nebeneinander, werden sie
  umgebrochen (z. B. 2 × 2) statt abgeschnitten — die VRAM-Spalte bleibt immer sichtbar.
- Bei minimiertem Fenster wird gar nicht gezeichnet.
