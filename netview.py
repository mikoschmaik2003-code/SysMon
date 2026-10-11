"""NETZ-Ansicht: IP-Konfiguration, Ping, offene Ports, Laufwerke, System - plus
eine Leiste mit nuetzlichen Windows-Werkzeugen. Gleiche Optik wie TERMINAL.
"""

import datetime
import os
import tkinter as tk

from terminal import TextView, bar_segs, heat_tag
from theme import THEME, font, human_bytes
from views import fmt_uptime

SPARK = "▁▂▃▄▅▆▇█"

# (Beschriftung, was gestartet wird) - alles ohne Adminrechte
TOOLS = (
    ("NETZWERKVERBINDUNGEN", "ncpa.cpl"),
    ("GERAETE-MANAGER", "devmgmt.msc"),
    ("TASK-MANAGER", "taskmgr.exe"),
    ("RESSOURCENMONITOR", "resmon.exe"),
    ("EREIGNISANZEIGE", "eventvwr.msc"),
    ("SYSTEMINFO", "msinfo32.exe"),
)


def _rate(bps):
    mbit = bps * 8 / 1e6
    return ("%7.2f Mbit/s" % mbit) if mbit < 1000 else ("%7.2f Gbit/s" % (mbit / 1000))


def _speed(bits):
    if not bits:
        return ""
    return ("%g Gbit/s" % (bits / 1e9)) if bits >= 1e9 else ("%g Mbit/s" % (bits / 1e6))


def _ping_tag(ms):
    if ms is None:
        return "crit"
    return "ok" if ms < 30 else ("warn" if ms < 100 else "crit")


class NetView(TextView):
    TAGS = dict(TextView.TAGS, ip=THEME["cpu"])

    def __init__(self, master, info):
        super().__init__(master)
        self.info = info
        self.txt.tag_configure("ip", underline=True)
        self.txt.tag_bind("ip", "<Button-1>", self._copy_clicked)
        self.txt.tag_bind("ip", "<Enter>", lambda e: self.txt.configure(cursor="hand2"))
        self.txt.tag_bind("ip", "<Leave>", lambda e: self.txt.configure(cursor="arrow"))
        self._toolbar()

    # ------------------------------------------------------------- Werkzeuge
    def _toolbar(self):
        bar = tk.Frame(self.frame, bg=THEME["panel"])
        bar.pack(side="top", fill="x")
        self._btn(bar, "IP KOPIEREN", self._copy_primary)
        self._btn(bar, "OEFFENTLICHE IP", self.info.fetch_public_ip)
        self._btn(bar, "DNS-CACHE LEEREN", self.info.flush_dns)
        tk.Label(bar, text="  |  ", bg=THEME["panel"], fg=THEME["dim"],
                 font=font(9)).pack(side="left")
        for label, target in TOOLS:
            self._btn(bar, label, lambda t=target, l=label: self._launch(t, l))

    def _btn(self, parent, text, cmd):
        b = tk.Label(parent, text=" " + text + " ", bg=THEME["panel_hi"],
                     fg=THEME["text"], font=font(9, True), padx=6, pady=3,
                     cursor="hand2")
        b.pack(side="left", padx=3, pady=5)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(fg=THEME["text_hi"]))
        b.bind("<Leave>", lambda e: b.configure(fg=THEME["text"]))

    def _launch(self, target, label):
        try:
            os.startfile(target)
            self.info.note(label.lower().capitalize() + " geoeffnet")
        except OSError:
            self.info.note(label.lower().capitalize() + " liess sich nicht oeffnen")

    def _clip(self, text):
        self.txt.clipboard_clear()
        self.txt.clipboard_append(text)
        self.info.note("kopiert: " + text)

    def _copy_primary(self):
        ip = self.info.primary_ip()
        if ip:
            self._clip(ip)
        else:
            self.info.note("keine aktive IPv4-Adresse")

    def _copy_clicked(self, event):
        idx = self.txt.index("@%d,%d" % (event.x, event.y))
        rng = self.txt.tag_prevrange("ip", idx + "+1c")
        if rng:
            self._clip(self.txt.get(*rng).strip())

    # ---------------------------------------------------------------- render
    def render(self, s):
        n = self.info.snapshot()
        self._begin()
        self._header(s, n)
        self._adapters(n)
        self._ping(n)
        self._ports(n)
        self._volumes(n)
        self._system(s, n)
        self._flush()

    def _header(self, s, n):
        left = " NETZ & SYSTEM  " + n["system"]["host"]
        right = "uptime " + fmt_uptime(s["uptime"]) + " "
        pad = max(1, self.c - len(left) - len(right) - 2)
        self._line(("┌" + left, "net"), ("─" * pad, "dim"), (right + "┐", "dim"))
        msg = n["message"]
        self._line(("  " + (msg or "Klick auf eine Adresse kopiert sie."),
                    "warn" if msg else "dim"))

    def _adapters(self, n):
        self._rule("IP-KONFIGURATION")
        pub = n["public_ip"]
        self._line(("  oeffentliche IP   ", "dim"),
                   (pub, "ip") if pub else ("-  (Knopf OEFFENTLICHE IP)", "dim"))
        self._line()
        down = []
        for a in n["adapters"]:
            if not a["up"]:
                down.append(a)
                continue
            rate = n["rates"].get(a["name"])
            head = [("  " + a["name"], "hi"), ("   " + a["desc"], "dim")]
            if a["speed"]:
                head.append(("   " + _speed(a["speed"]), "net"))
            self._line(*head)
            for ip, pre, mask in a["v4"]:
                self._line(("    IPv4            ", "dim"), (ip, "ip"),
                           ("/%d   Maske %s" % (pre, mask), "dim"))
            glob = [v for v in a["v6"] if not v[0].startswith("fe80")]
            link = [v for v in a["v6"] if v[0].startswith("fe80")]
            for ip, pre in (glob[:2] + link[:1]):
                self._line(("    IPv6            ", "dim"), (ip, "ip"),
                           ("/%d" % pre, "dim"))
            if len(glob) > 2:
                self._line(("                    + %d weitere IPv6" % (len(glob) - 2),
                            "dim"))
            if a["gw"]:
                segs = [("    Gateway         ", "dim")]
                for g in a["gw"]:
                    segs += [(g, "ip"), ("   ", None)]
                self._line(*segs)
            if a["dns"]:
                segs = [("    DNS             ", "dim")]
                for d in a["dns"][:3]:
                    segs += [(d, "ip"), ("   ", None)]
                self._line(*segs)
            dh = ("DHCP an" + (" (Server %s)" % a["dhcp_server"]
                               if a["dhcp_server"] else "")) if a["dhcp"] else "statisch"
            self._line(("    MAC             ", "dim"), (a["mac"] or "-", "txt"),
                       ("   MTU %d   %s" % (a["mtu"], dh), "dim"),
                       (("   Suffix " + a["suffix"]) if a["suffix"] else "", "dim"))
            if rate:
                self._line(("    Durchsatz       ", "dim"), ("↓ " + _rate(rate[0]), "net"),
                           ("   ↑ " + _rate(rate[1]), "up"))
            self._line()
        if down:
            self._line(("  getrennt: ", "dim"),
                       (",  ".join(a["name"] for a in down), "dim"))
            self._line()

    def _ping(self, n):
        self._rule("PING")
        width = max(10, min(40, self.c - 70))
        for ip, label in n["targets"]:
            vals = n["pings"].get(ip, [])
            ok = [v for v in vals if v is not None]
            last = vals[-1] if vals else None
            loss = (100.0 * (len(vals) - len(ok)) / len(vals)) if vals else 0.0
            hist = vals[-width:]
            mx = max(ok + [10.0])
            spark = "".join(" " if v is None else
                            SPARK[min(7, int(v / mx * 7.999))] for v in hist)
            segs = [("  %-24s" % label[:24], "hi"),
                    (("%6.0f ms" % last) if last is not None else "  ---   ",
                     _ping_tag(last) if vals else "dim"),
                    ("  " + spark.ljust(width) + "  ", "net")]
            if ok:
                segs.append(("min %3.0f  avg %3.0f  max %3.0f ms" %
                             (min(ok), sum(ok) / len(ok), max(ok)), "dim"))
            segs.append(("   Verlust %3.0f%%" % loss,
                         "ok" if loss == 0 else ("warn" if loss < 10 else "crit")))
            self._line(*segs)
        self._line()

    def _ports(self, n):
        c = n["conn"]
        self._rule("OFFENE PORTS")
        self._line(("  TCP verbunden ", "dim"), ("%d" % c["est"], "hi"),
                   ("   lauschend ", "dim"), ("%d" % c["listen"], "hi"),
                   ("   UDP ", "dim"), ("%d" % c["udp"], "hi"))
        rows = n["ports"]
        cw = 38
        per = max(1, (self.c - 2) // cw)
        shown = rows[: per * 4]
        for i in range(0, len(shown), per):
            segs = []
            for port, scope, name in shown[i:i + per]:
                segs += [("  %5d " % port, "net"),
                         ("%-6s " % scope, "warn" if scope == "alle" else "dim"),
                         ("%-*s" % (cw - 16, name[: cw - 16]), "txt")]
            self._line(*segs)
        if len(rows) > len(shown):
            self._line(("  + %d weitere" % (len(rows) - len(shown)), "dim"))
        self._line()

    def _volumes(self, n):
        self._rule("LAUFWERKE")
        bw = max(14, min(40, self.c - 70))
        for v in n["volumes"]:
            name = "%s %s" % (v["root"], v["label"])
            self._line(*([("  %-22s [" % name[:22], "dim")] +
                         bar_segs(v["pct"], bw, "disk") +
                         [("] ", "dim"), ("%5.1f%%" % v["pct"], heat_tag(v["pct"])),
                          ("   %s frei" % human_bytes(v["free"]), "hi"),
                          ("  von %s   %s" % (human_bytes(v["total"]), v["fs"]),
                           "dim")]))
        self._line()

    def _system(self, s, n):
        y = n["system"]
        self._rule("SYSTEM")
        boot = datetime.datetime.fromtimestamp(y["boot"]).strftime("%d.%m.%Y %H:%M")
        rows = [
            ("Rechner", "%s   (Benutzer %s)" % (y["host"], y["user"])),
            ("Windows", "%s   %s" % (y["windows"], y["winver"])),
            ("Gestartet", boot),
            ("CPU", s["cpu_name"]),
        ]
        if s["mem"]:
            rows.append(("RAM", "%.1f GB" % (s["mem"]["total"] / 1024 ** 3)))
        if s["gpu"]:
            rows.append(("GPU", s["gpu"]["name"] + (
                "   Treiber " + y["gpu_driver"] if y["gpu_driver"] else "")))
        if y["board"]:
            rows.append(("Mainboard", y["board"]))
        if y["bios"]:
            rows.append(("BIOS", y["bios"]))
        for k, v in rows:
            self._line(("  %-14s" % k, "dim"), (v, "hi"))
