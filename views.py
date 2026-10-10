"""Die grafische Ansicht (Task-Manager-Optik) - ein Panel pro Bereich."""

import tkinter as tk

import draw
from theme import THEME, heat, human_bytes, mbps

MB = 1024.0 * 1024.0
NICE = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 4000, 8000, 16000]


def nice_max(v_mb):
    for n in NICE:
        if v_mb <= n * 0.92:
            return float(n)
    return float(NICE[-1])


def pct_str(v):
    return ("%.1f%%" % v) if v < 10 else ("%.0f%%" % v)


def fmt_uptime(sec):
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return (str(d) + "d " if d else "") + "%d:%02d:%02d" % (h, m, s)


class Panel:
    """Basis: eine Canvas mit Scene, die nur Geaendertes an Tk schickt."""

    def __init__(self, master):
        self.cv = tk.Canvas(master, bg=THEME["bg"], highlightthickness=0, bd=0)
        self.sc = draw.Scene(self.cv)

    def render(self, s):
        w, h = self.cv.winfo_width(), self.cv.winfo_height()
        if w < 40 or h < 40:
            return
        try:
            self.draw(self.sc, w, h, s)
        except Exception as exc:  # nie das ganze UI wegen eines Panels verlieren
            self.cv.delete("all")
            self.sc.sig = None
            self.cv.create_text(12, 16, text="Fehler: " + str(exc), anchor="w",
                                fill=THEME["crit"], font=draw.F(9))


# ---------------------------------------------------------------------- CPU
class CpuPanel(Panel):
    def draw(self, sc, w, h, s):
        cpu, pr = s["cpu"], s["procs"]
        n = len(cpu["per_core"])
        sc.begin(("cpu", w, h, n))
        x0, y0, x1, y1 = sc.panel("p", 0, 0, w - 1, h - 1, "CPU",
                                  THEME["cpu"], s["cpu_name"])

        # Kopfzeile
        sc.text("tot", x0, y0 + 20, "%.0f%%" % cpu["total"], THEME["cpu"], 26, True)
        sc.text("ghz", x0 + 84, y0 + 12, "%.2f GHz" % (cpu["freq"] or s["cpu_base"]),
                THEME["text_hi"], 12, True)
        sc.text("base", x0 + 84, y0 + 30, "Basis %.2f GHz" % s["cpu_base"],
                THEME["dim"], 8)

        cols = (("Kerne", "%d / %d" % (s["cores_phys"], s["cores_log"])),
                ("Prozesse", str(pr["processes"])),
                ("Threads", str(pr["threads"])),
                ("Handles", format(pr["handles"], ",").replace(",", ".")),
                ("Betrieb", fmt_uptime(s["uptime"])))
        cw = max(86, (x1 - (x0 + 190)) / len(cols))
        for i, (k, v) in enumerate(cols):
            cx = x0 + 190 + i * cw
            if cx + 60 > x1:
                break
            sc.text("k%d" % i, cx, y0 + 12, k, THEME["dim"], 8)
            sc.text("v%d" % i, cx, y0 + 28, v, THEME["text_hi"], 10, True)

        # Gesamtverlauf
        gy0 = y0 + 44
        gy1 = gy0 + max(52, (y1 - gy0) * 0.26)
        sc.grid("g", x0, gy0, x1, gy1, (12, 4))
        sc.area("ga", x0, gy0, x1, gy1, s["h_cpu"], THEME["cpu"], THEME["cpu_fill"])
        sc.text("g100", x1 - 4, gy0 + 8, "100%", THEME["dim"], 7, anchor="e")
        sc.text("g60", x0 + 4, gy1 - 8, "60 s", THEME["dim"], 7)

        # Kerne einzeln
        top = gy1 + 16
        sc.text("hdr", x0, top - 8, "LOGISCHE PROZESSOREN  (%d)" % n,
                THEME["dim"], 8, True)
        top += 4
        gw, gh = x1 - x0, y1 - top
        cols_n, rows_n = draw.best_grid(n, gw, gh)
        cwid, chei = gw / cols_n, gh / rows_n
        ghz = s.get("core_ghz")
        wide = cwid > 46
        for i in range(n):
            r, c = divmod(i, cols_n)
            ax0, ay0 = x0 + c * cwid, top + r * chei
            ax1, ay1 = ax0 + cwid - 3, ay0 + chei - 3
            val = cpu["per_core"][i]
            sc.grid("cg%d" % i, ax0, ay0, ax1, ay1, (6, 3))
            sc.area("ca%d" % i, ax0, ay0, ax1, ay1, s["h_core"][i],
                    THEME["cpu"], THEME["cpu_fill"], 100, 1.0)
            if wide:
                name = "CPU%d" % i
                if ghz and cwid > 96:
                    name += "   %.2f GHz" % ghz[i]
                sc.text("cl%d" % i, ax0 + 3, ay0 + 8, name, THEME["dim"], 7)
                sc.text("cp%d" % i, ax1 - 3, ay0 + 8, "%.0f%%" % val,
                        heat(val), 7, True, anchor="e")


# ---------------------------------------------------------------------- GPU
class GpuPanel(Panel):
    def draw(self, sc, w, h, s):
        g = s["gpu"]
        sc.begin(("gpu", w, h, bool(g)))
        x0, y0, x1, y1 = sc.panel("p", 0, 0, w - 1, h - 1, "GPU", THEME["gpu"],
                                  g["name"] if g else "-")
        if not g:
            sc.text("wait", x0, y0 + 20, "keine NVIDIA-GPU gefunden", THEME["dim"], 10)
            return

        sc.text("util", x0, y0 + 20, "%.0f%%" % g["util"], THEME["gpu"], 26, True)
        stats = (("Temp", "%.0f C" % g["temp"], heat(g["temp"] * 1.1)),
                 ("Power", "%.0f / %.0f W" % (g["power"], g["power_limit"]),
                  THEME["text_hi"]),
                 ("Takt", "%.0f MHz" % g["clk_sm"], THEME["text_hi"]),
                 ("VRAM-Takt", "%.0f MHz" % g["clk_mem"], THEME["text_hi"]),
                 ("Luefter", "%.0f%%" % g["fan"], THEME["text_hi"]))
        cw = max(78, (x1 - (x0 + 88)) / len(stats))
        for i, (k, v, col) in enumerate(stats):
            cx = x0 + 88 + i * cw
            if cx + 50 > x1:
                break
            sc.text("k%d" % i, cx, y0 + 12, k, THEME["dim"], 8)
            sc.text("v%d" % i, cx, y0 + 28, v, col, 10, True)

        gy0 = y0 + 44
        rest = y1 - gy0
        gy1 = gy0 + rest * 0.40
        sc.grid("g3d", x0, gy0, x1, gy1, (12, 4))
        sc.area("a3d", x0, gy0, x1, gy1, s["h_gpu"], THEME["gpu"], THEME["gpu_fill"])
        sc.text("l3d", x0 + 5, gy0 + 9, "3D / Compute", THEME["dim"], 8, True)
        sc.text("m3d", x1 - 4, gy0 + 9, "100%", THEME["dim"], 7, anchor="e")

        my0 = gy1 + 8
        my1 = my0 + rest * 0.26
        mid = (x0 + x1) / 2 - 4
        for n, (bx0, bx1, hist, name, val) in enumerate((
                (x0, mid, s["h_gpu_enc"], "Video Encode", g["enc"]),
                (mid + 8, x1, s["h_gpu_dec"], "Video Decode", g["dec"]))):
            sc.grid("eg%d" % n, bx0, my0, bx1, my1, (8, 3))
            sc.area("ea%d" % n, bx0, my0, bx1, my1, hist, THEME["gpu"],
                    THEME["gpu_fill"])
            sc.text("el%d" % n, bx0 + 5, my0 + 9, name, THEME["dim"], 8, True)
            sc.text("ev%d" % n, bx1 - 4, my0 + 9, "%.0f%%" % val, THEME["text_hi"],
                    8, True, anchor="e")

        vy = my1 + 12
        sc.text("vl", x0, vy, "Dedizierter GPU-Speicher", THEME["dim"], 8, True)
        sc.text("vv", x1, vy, "%.2f / %.1f GB" % (g["vram_used"] / 1024.0,
                                                  g["vram_total"] / 1024.0),
                THEME["text_hi"], 9, True, anchor="e")
        sc.bar("vb", x0, vy + 8, x1, vy + 22, g["vram_pct"], THEME["vram"],
               THEME["vram_fill"], "%.0f%%" % g["vram_pct"], THEME["text_hi"])
        if vy + 60 < y1:
            sc.grid("vg", x0, vy + 28, x1, y1, (12, 2))
            sc.area("va", x0, vy + 28, x1, y1, s["h_vram"], THEME["vram"],
                    THEME["vram_fill"])


# -------------------------------------------------------------------- MEMORY
class MemPanel(Panel):
    def draw(self, sc, w, h, s):
        m = s["mem"]
        if not m:
            return
        sc.begin(("mem", w, h))
        tot = m["total"]
        x0, y0, x1, y1 = sc.panel("p", 0, 0, w - 1, h - 1, "ARBEITSSPEICHER",
                                  THEME["mem"], human_bytes(tot))
        sc.text("used", x0, y0 + 18, "%.1f GB" % (m["used"] / 1024 ** 3),
                THEME["mem"], 18, True)
        sc.text("tot", x0 + 96, y0 + 11, "von %.1f GB" % (tot / 1024 ** 3),
                THEME["text_hi"], 9, True)
        sc.text("pct", x0 + 96, y0 + 25, "%.0f%% belegt" % m["percent"],
                heat(m["percent"]), 9)
        sc.text("free", x1, y0 + 11, "frei  " + human_bytes(m["available"]),
                THEME["text_hi"], 9, True, anchor="e")
        sc.text("swap", x1, y0 + 25, "Auslagerung  %s / %s" % (
            human_bytes(m["swap_used"]), human_bytes(m["swap_total"])),
                THEME["dim"], 8, anchor="e")

        gy0, gy1 = y0 + 36, y1 - 26
        sc.grid("g", x0, gy0, x1, gy1, (12, 4))
        sc.area("a", x0, gy0, x1, gy1, s["h_mem"], THEME["mem"], THEME["mem_fill"])

        by0, by1 = y1 - 20, y1 - 4
        used_f = m["used"] / tot
        cached_f = min(max(m.get("cached", 0) / tot, 0.0), 1.0 - used_f)
        sx = x0
        for i, (frac, col) in enumerate(((used_f, THEME["mem"]),
                                         (cached_f, "#2f4a86"),
                                         (max(0.0, 1 - used_f - cached_f),
                                          THEME["grid"]))):
            ex = sx + (x1 - x0) * frac
            sc.rect("c%d" % i, sx, by0, ex, by1, fill=col, outline=THEME["bg"])
            sx = ex
        sc.text("cl", x0 + 4, (by0 + by1) / 2, "in Verwendung", THEME["text_hi"],
                7, True)


# --------------------------------------------------------------------- DISKS
class DiskPanel(Panel):
    def draw(self, sc, w, h, s):
        disks = s["disks"]
        sc.begin(("disk", w, h, len(disks)))
        x0, y0, x1, y1 = sc.panel("p", 0, 0, w - 1, h - 1, "DATENTRAEGER",
                                  THEME["disk"], "%d Laufwerke" % len(disks))
        if not disks:
            return
        rh = (y1 - y0) / len(disks)
        for i, d in enumerate(disks):
            self._row(sc, i, x0, y0 + i * rh, x1, y0 + i * rh + rh - 6, d,
                      s["h_disk"].get(d["key"]))

    def _row(self, sc, i, x0, y0, x1, y1, d, hist):
        k = "d%d" % i
        sc.text(k + "n", x0, y0 + 9, d["label"], THEME["text_hi"], 9, True)
        sc.text(k + "a", x0, y0 + 24, "Aktiv " + pct_str(d["active"]),
                heat(d["active"]), 8)
        sc.bar(k + "b", x0, y0 + 30, x0 + 128, y0 + 40, d["active"],
               THEME["disk"], THEME["disk_fill"])

        ix = x0 + 140
        sc.text(k + "rl", ix, y0 + 9, "R", THEME["disk"], 9, True)
        sc.text(k + "rv", ix + 14, y0 + 9, "%7.2f MB/s" % mbps(d["read"]),
                THEME["text_hi"], 9, True)
        sc.text(k + "wl", ix, y0 + 24, "W", "#7ad2f0", 9, True)
        sc.text(k + "wv", ix + 14, y0 + 24, "%7.2f MB/s" % mbps(d["write"]),
                THEME["text_hi"], 9, True)
        sc.text(k + "io", ix, y0 + 38, "%.0f IO/s" % d["iops"], THEME["dim"], 8)

        gx0 = ix + 108
        if gx0 + 60 > x1 or not hist:
            return
        rd, wr, _ = hist
        peak = nice_max(max(max(rd), max(wr)) / MB)
        sc.grid(k + "g", gx0, y0, x1, y1, (12, 3))
        sc.area(k + "aw", gx0, y0, x1, y1, wr, "#7ad2f0", "#123a4a", peak * MB, 1.2)
        sc.area(k + "ar", gx0, y0, x1, y1, rd, THEME["disk"], "", peak * MB, 1.4)
        sc.text(k + "pk", x1 - 4, y0 + 8, "%g MB/s" % peak, THEME["dim"], 7,
                anchor="e")


# ------------------------------------------------------------------- NETWORK
class NetPanel(Panel):
    def draw(self, sc, w, h, s):
        n = s["net"]
        sc.begin(("net", w, h))
        x0, y0, x1, y1 = sc.panel("p", 0, 0, w - 1, h - 1, "NETZWERK",
                                  THEME["net"], n["name"])
        dn_m, up_m = n["down"] * 8 / 1e6, n["up"] * 8 / 1e6
        sc.text("dl", x0, y0 + 12, "Empf.", THEME["net"], 8, True)
        sc.text("dv", x0 + 40, y0 + 12, "%.2f Mbit/s" % dn_m, THEME["text_hi"], 10, True)
        sc.text("ul", x0, y0 + 28, "Send.", "#f2a0c0", 8, True)
        sc.text("uv", x0 + 40, y0 + 28, "%.2f Mbit/s" % up_m, THEME["text_hi"], 10, True)
        sc.text("dt", x1, y0 + 12, "gesamt runter " + human_bytes(n["dn_total"]),
                THEME["dim"], 8, anchor="e")
        sc.text("ut", x1, y0 + 28, "gesamt hoch " + human_bytes(n["up_total"]),
                THEME["dim"], 8, anchor="e")

        gy0 = y0 + 40
        peak = nice_max(s["net_peak"] * 8 / 1e6) * 1e6 / 8
        sc.grid("g", x0, gy0, x1, y1, (12, 4))
        sc.area("ad", x0, gy0, x1, y1, s["h_net_dn"], THEME["net"],
                THEME["net_fill"], peak)
        sc.area("au", x0, gy0, x1, y1, s["h_net_up"], "#f2a0c0", "", peak)
        sc.text("pk", x1 - 4, gy0 + 8, "%.0f Mbit/s" % (peak * 8 / 1e6),
                THEME["dim"], 7, anchor="e")
