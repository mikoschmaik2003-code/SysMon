"""Terminal-Ansicht: gleiche Daten, aber als farbige ASCII-Balken.

Der Text wird komplett als ein String eingefuegt und danach pro Farbe *einmal*
getaggt (tag_add nimmt beliebig viele Indexpaare) - so sind es rund 15 Tcl-Aufrufe
pro Bild statt mehrerer hundert.
"""

import socket
import tkinter as tk
import tkinter.font as tkfont

import privacy

from theme import THEME, heat, human_bytes, mbps
from views import fmt_uptime, nice_max, pct_str

FULL, EMPTY = "█", "░"
COLW = 40          # Spaltenbreite der Prozessliste


def bar_segs(pct, width, tag, off="off"):
    """Balken als zwei Segmente: gefuellt in der Metrikfarbe, Rest gedaempft."""
    pct = 0.0 if pct < 0.0 else (100.0 if pct > 100.0 else pct)
    n = int(round(width * pct / 100.0))
    return [(FULL * n, tag), (EMPTY * (width - n), off)]


def heat_tag(pct):
    col = heat(pct)
    return {THEME["ok"]: "ok", THEME["warn"]: "warn"}.get(col, "crit")


class TextView:
    """Basis fuer alle Text-Ansichten: Zeilen sammeln, dann in einem Rutsch zeigen."""

    TAGS = {
        "dim": THEME["dim"], "hi": THEME["text_hi"], "txt": THEME["text"],
        "cpu": THEME["cpu"], "gpu": THEME["gpu"], "mem": THEME["mem"],
        "ecore": THEME["ecore"],
        "disk": THEME["disk"], "net": THEME["net"], "vram": THEME["vram"],
        "wr": "#7ad2f0", "up": "#f2a0c0",
        "ok": THEME["ok"], "warn": THEME["warn"], "crit": THEME["crit"],
        "off": "#233240",
    }

    def __init__(self, master):
        self.frame = tk.Frame(master, bg="#05080b")
        self.font = tkfont.Font(family="Consolas", size=11)
        self.txt = tk.Text(self.frame, bg="#05080b", fg=THEME["text"], bd=0,
                           highlightthickness=0, font=self.font, wrap="none",
                           padx=14, pady=10, insertwidth=0, cursor="arrow")
        self.txt.pack(side="bottom", fill="both", expand=True)
        for name, col in self.TAGS.items():
            self.txt.tag_configure(name, foreground=col)
        self.txt.bind("<Key>", lambda e: "break")
        self.chw = max(self.font.measure("0"), 6)
        self.host = socket.gethostname()
        self._last = None

    # ------------------------------------------------------------------ util
    def cols(self):
        return max(60, int((self.txt.winfo_width() - 30) / self.chw))

    def _line(self, *segs):
        self.buf.append(segs)

    def _rule(self, title=""):
        t = ("── " + title + " ") if title else ""
        self._line((t, "dim"), ("─" * max(0, self.c - len(t)), "dim"))

    def _begin(self):
        self.buf = []
        self.c = self.cols()

    def _flush(self):
        """ein String + je Farbe ein tag_add"""
        parts, ranges = [], {}
        for ln, segs in enumerate(self.buf, 1):
            col = 0
            for text, tag in segs:
                if not text:
                    continue
                if tag:
                    r = ranges.setdefault(tag, [])
                    r.append("%d.%d" % (ln, col))
                    r.append("%d.%d" % (ln, col + len(text)))
                col += len(text)
                parts.append(text)
            parts.append("\n")
        full = "".join(parts)

        key = (full, tuple(sorted((k, tuple(v)) for k, v in ranges.items())))
        if key == self._last:      # nichts hat sich geaendert
            return
        self._last = key

        t = self.txt
        yview = t.yview()[0]
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", full)
        for tag, idx in ranges.items():
            t.tag_add(tag, *idx)
        t.configure(state="disabled")
        t.yview_moveto(yview)


class TerminalView(TextView):
    def render(self, s):
        self._begin()
        self._header(s)
        self._cpu(s)
        self._gpu(s)
        self._mem(s)
        self._disk(s)
        self._net(s)
        self._top(s)
        self._flush()

    # ------------------------------------------------------------ Abschnitte
    def _header(self, s):
        left = " SYSMON  " + privacy.host(self.host)
        right = "uptime " + fmt_uptime(s["uptime"]) + " "
        pad = max(1, self.c - len(left) - len(right) - 2)
        self._line(("┌" + left, "cpu"), ("─" * pad, "dim"), (right + "┐", "dim"))

    def _cpu(self, s):
        cpu, pr = s["cpu"], s["procs"]
        ghz = s.get("core_ghz")
        self._rule("CPU")
        self._line(("  " + s["cpu_name"], "hi"),
                   ("   %.2f GHz" % (cpu["freq"] or s["cpu_base"]), "cpu"),
                   ("   %d Kerne / %d Threads" % (s["cores_phys"], s["cores_log"]),
                    "dim"))
        self._line(("  Prozesse ", "dim"), ("%d" % pr["processes"], "hi"),
                   ("   Threads ", "dim"), ("%d" % pr["threads"], "hi"),
                   ("   Handles ", "dim"), ("%d" % pr["handles"], "hi"))
        bw = max(20, self.c - 26)
        self._line(*([("  gesamt [", "dim")] + bar_segs(cpu["total"], bw, "cpu") +
                     [("] ", "dim"), ("%5.1f%%" % cpu["total"], heat_tag(cpu["total"]))]))
        self._line()

        cores = cpu["per_core"]
        types = s.get("core_types")
        if types:
            self._line(("  ████ ", "cpu"), ("P-Kerne (Performance) %d" % types.count("P"), "dim"),
                       ("     ████ ", "ecore"),
                       ("E-Kerne (Effizienz) %d" % types.count("E"), "dim"))
        width = 33 if ghz else 26
        per = max(1, (self.c - 2) // width)
        for i in range(0, len(cores), per):
            segs = []
            for j in range(i, min(i + per, len(cores))):
                v = cores[j]
                kind = types[j] if types else ""
                tag = "ecore" if kind == "E" else "cpu"
                segs += ([("  %s%02d [" % (kind or "c", j),
                           "dim")] + bar_segs(v, 12, tag) +
                         [("] ", "dim"), ("%3.0f%%" % v, heat_tag(v))])
                if ghz:
                    segs.append((" %5.2fGHz" % ghz[j], "dim"))
            self._line(*segs)
        self._line()

    def _gpu(self, s):
        g = s["gpu"]
        self._rule("GPU")
        if not g:
            self._line(("  keine NVIDIA-GPU gefunden", "dim"))
            self._line()
            return
        self._line(("  " + g["name"], "hi"),
                   ("   %.0f C" % g["temp"], heat_tag(g["temp"] * 1.1)),
                   ("   %.0f/%.0f W" % (g["power"], g["power_limit"]), "dim"),
                   ("   %.0f MHz" % g["clk_sm"], "dim"),
                   ("   Fan %.0f%%" % g["fan"], "dim"))
        bw = max(20, self.c - 26)
        for name, val, tag in (("3D   ", g["util"], "gpu"),
                               ("encod", g["enc"], "gpu"),
                               ("decod", g["dec"], "gpu"),
                               ("vram ", g["vram_pct"], "vram")):
            self._line(*([("  " + name + " [", "dim")] + bar_segs(val, bw, tag) +
                         [("] ", "dim"), ("%5.1f%%" % val, heat_tag(val))]))
        self._line(("        %.2f / %.1f GB VRAM" % (g["vram_used"] / 1024.0,
                                                     g["vram_total"] / 1024.0), "dim"))
        self._line()

    def _mem(self, s):
        m = s["mem"]
        if not m:
            return
        self._rule("RAM")
        bw = max(20, self.c - 26)
        self._line(*([("  ram    [", "dim")] + bar_segs(m["percent"], bw, "mem") +
                     [("] ", "dim"),
                      ("%5.1f%%" % m["percent"], heat_tag(m["percent"]))]))
        self._line(("        %.1f / %.1f GB belegt" % (m["used"] / 1024 ** 3,
                                                       m["total"] / 1024 ** 3), "hi"),
                   ("   frei %.1f GB" % (m["available"] / 1024 ** 3), "dim"),
                   ("   swap %s / %s" % (human_bytes(m["swap_used"]),
                                         human_bytes(m["swap_total"])), "dim"))
        self._line()

    def _disk(self, s):
        self._rule("DATENTRAEGER")
        bw = max(14, min(30, self.c - 62))
        for d in s["disks"]:
            self._line(*([("  %-18s [" % d["label"][:18], "dim")] +
                         bar_segs(d["active"], bw, "disk") +
                         [("] ", "dim"),
                       ("%5s" % pct_str(d["active"]), heat_tag(d["active"])),
                       ("  R ", "disk"), ("%8.2f MB/s" % mbps(d["read"]), "hi"),
                       ("  W ", "wr"), ("%8.2f MB/s" % mbps(d["write"]), "hi"),
                       ("  %5.0f IO/s" % d["iops"], "dim")]))
        self._line()

    def _net(self, s):
        n = s["net"]
        self._rule("NETZWERK")
        peak = max(nice_max(s["net_peak"] * 8 / 1e6), 1.0)
        bw = max(20, self.c - 34)
        dn_m, up_m = n["down"] * 8 / 1e6, n["up"] * 8 / 1e6
        self._line(("  " + n["name"], "hi"), ("   Skala %.0f Mbit/s" % peak, "dim"))
        self._line(*([("  down   [", "dim")] +
                     bar_segs(dn_m / peak * 100, bw, "net") +
                     [("] ", "dim"), ("%8.2f Mbit/s" % dn_m, "hi")]))
        self._line(*([("  up     [", "dim")] +
                     bar_segs(up_m / peak * 100, bw, "up") +
                     [("] ", "dim"), ("%8.2f Mbit/s" % up_m, "hi")]))
        self._line(("        gesamt  ↓ %s   ↑ %s"
                    % (human_bytes(n["dn_total"]), human_bytes(n["up_total"])), "dim"))

    # ------------------------------------- Top-Prozesse (CPU / RAM / GPU / VRAM)
    def _top(self, s):
        rows_cpu = s.get("top") or []
        if not rows_cpu:
            return
        rows_mem = s.get("top_mem") or []
        rows_gpu = s.get("top_gpu") or []
        rows_vram = s.get("top_vram") or []

        def pct_col(rows, field, tag):
            mx = max([r[field] for r in rows] + [1.0])

            def fmt(r):
                v = r[field]
                return ([("  %5.1f%% " % v, heat_tag(v * 4)), ("[", "dim")] +
                        bar_segs(v / mx * 100, 8, tag) +
                        [("] ", "dim"), ("%-19s " % r["name"][:19], "hi")])
            return fmt

        def ram_col(rows):
            mx = max([r["rss"] for r in rows] + [1.0])

            def fmt(r):
                return ([("  %8s " % human_bytes(r["rss"]), "mem"), ("[", "dim")] +
                        bar_segs(r["rss"] / mx * 100, 8, "mem") +
                        [("] ", "dim"), ("%-17s " % r["name"][:17], "hi")])
            return fmt

        def vram_col(rows):
            mx = max([r["vram"] for r in rows] + [1.0])

            def fmt(r):
                name = r["name"][:-4] if r["name"].lower().endswith(".exe") else r["name"]
                return ([("  %5.0f MB " % (r["vram"] / 1048576.0), "vram"),
                         ("[", "dim")] +
                        bar_segs(r["vram"] / mx * 100, 8, "vram") +
                        [("] ", "dim"), ("%-17s " % name[:17], "hi")])
            return fmt

        g = s.get("gpu")
        vram_title = "nach VRAM"
        if g:
            vram_title += "  (%.0f / %.0f MB)" % (g["vram_used"], g["vram_total"])
        cols = [("nach CPU", rows_cpu, pct_col(rows_cpu, "cpu", "cpu")),
                ("nach RAM", rows_mem, ram_col(rows_mem)),
                ("nach GPU", rows_gpu, pct_col(rows_gpu, "gpu", "gpu")),
                (vram_title, rows_vram, vram_col(rows_vram))]

        # passen nicht alle vier nebeneinander, werden sie umgebrochen
        # (z. B. 2 x 2) statt Spalten wegzulassen
        ncol = max(1, min(len(cols), (self.c - 2) // COLW))
        depth = 12 if ncol > 1 else 8

        self._line()
        self._rule("TOP PROZESSE")
        for start in range(0, len(cols), ncol):
            group = cols[start:start + ncol]
            if start:
                self._line()
            self._line(*[("  %-*s" % (COLW - 2, t), "dim") for t, _, _ in group])
            n = min(depth, max([len(r) for _, r, _ in group] + [0]))
            for i in range(n):
                segs = []
                for _, rows, fmt in group:
                    segs += fmt(rows[i]) if i < len(rows) else [(" " * COLW, "dim")]
                self._line(*segs)
