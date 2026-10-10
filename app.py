"""SysMon - Multimonitor fuer CPU (alle Kerne), GPU, RAM, Datentraeger und Netzwerk.

Tasten:  G Grafik   T Terminal   Leertaste umschalten
         1/2/3 Intervall 0.5s / 1s / 2s
         O immer im Vordergrund   F11 Vollbild   Strg+Q beenden
"""

import json
import os
import tkinter as tk

import terminal
import views
from sampler import Sampler
from theme import THEME, font

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "sysmon.json")
INTERVALS = (0.5, 1.0, 2.0)


def load_cfg():
    try:
        with open(CFG, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return {}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.title("SysMon")
        self.configure(bg=THEME["bg"])
        self.geometry(self.cfg.get("geometry", "1600x950"))
        self.minsize(900, 600)

        self.mode = self.cfg.get("mode", "graph")
        self.interval = float(self.cfg.get("interval", 1.0))
        self.topmost = bool(self.cfg.get("topmost", False))
        self.attributes("-topmost", self.topmost)
        self._full = False

        self.sampler = Sampler(self.interval)
        self.sampler.start()

        self._build_topbar()
        self._build_body()
        self._bind_keys()
        self._apply_mode()

        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.after(300, self._tick)

    # ---------------------------------------------------------------- topbar
    def _build_topbar(self):
        bar = tk.Frame(self, bg=THEME["panel"], height=38)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        tk.Label(bar, text="  SYSMON", bg=THEME["panel"], fg=THEME["cpu"],
                 font=font(13, True)).pack(side="left")
        tk.Label(bar, text="  multimonitor", bg=THEME["panel"], fg=THEME["dim"],
                 font=font(9)).pack(side="left")

        self.btn_graph = self._button(bar, "GRAFIK", lambda: self.set_mode("graph"))
        self.btn_term = self._button(bar, "TERMINAL", lambda: self.set_mode("term"))

        tk.Label(bar, text="   Intervall ", bg=THEME["panel"], fg=THEME["dim"],
                 font=font(9)).pack(side="left")
        self.btn_iv = []
        for iv in INTERVALS:
            b = self._button(bar, ("%gs" % iv), lambda v=iv: self.set_interval(v))
            self.btn_iv.append((iv, b))

        self.btn_top = self._button(bar, "IM VORDERGRUND", self.toggle_topmost)
        self._button(bar, "VOLLBILD", self.toggle_full)

        self.status = tk.Label(bar, text="", bg=THEME["panel"], fg=THEME["dim"],
                               font=font(9))
        self.status.pack(side="right", padx=10)

    def _button(self, parent, text, cmd):
        b = tk.Label(parent, text=" " + text + " ", bg=THEME["panel_hi"],
                     fg=THEME["text"], font=font(9, True), padx=6, pady=3,
                     cursor="hand2")
        b.pack(side="left", padx=3, pady=6)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(fg=THEME["text_hi"]))
        b.bind("<Leave>", lambda e: b.configure(
            fg=THEME["text_hi"] if b.cget("bg") == THEME["cpu_fill"] else THEME["text"]))
        return b

    def _mark(self, btn, active):
        btn.configure(bg=THEME["cpu_fill"] if active else THEME["panel_hi"],
                      fg=THEME["text_hi"] if active else THEME["text"])

    # ------------------------------------------------------------------ body
    def _build_body(self):
        self.body = tk.Frame(self, bg=THEME["bg"])
        self.body.pack(fill="both", expand=True)

        self.graph = tk.Frame(self.body, bg=THEME["bg"])
        self.graph.columnconfigure(0, weight=55, uniform="c")
        self.graph.columnconfigure(1, weight=45, uniform="c")
        self.graph.rowconfigure(0, weight=58, uniform="r")
        self.graph.rowconfigure(1, weight=42, uniform="r")

        self.p_cpu = views.CpuPanel(self.graph)
        self.p_gpu = views.GpuPanel(self.graph)
        self.p_disk = views.DiskPanel(self.graph)
        self.p_cpu.cv.grid(row=0, column=0, sticky="nsew", padx=(6, 3), pady=(6, 3))
        self.p_gpu.cv.grid(row=0, column=1, sticky="nsew", padx=(3, 6), pady=(6, 3))
        self.p_disk.cv.grid(row=1, column=0, sticky="nsew", padx=(6, 3), pady=(3, 6))

        right = tk.Frame(self.graph, bg=THEME["bg"])
        right.grid(row=1, column=1, sticky="nsew", padx=(3, 6), pady=(3, 6))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=52, uniform="q")
        right.rowconfigure(1, weight=48, uniform="q")
        self.p_mem = views.MemPanel(right)
        self.p_net = views.NetPanel(right)
        self.p_mem.cv.grid(row=0, column=0, sticky="nsew", pady=(0, 3))
        self.p_net.cv.grid(row=1, column=0, sticky="nsew", pady=(3, 0))

        self.term = terminal.TerminalView(self.body)
        self.panels = [self.p_cpu, self.p_gpu, self.p_disk, self.p_mem, self.p_net]

    # ------------------------------------------------------------------ keys
    def _bind_keys(self):
        self.bind("<KeyPress-g>", lambda e: self.set_mode("graph"))
        self.bind("<KeyPress-t>", lambda e: self.set_mode("term"))
        self.bind("<space>", lambda e: self.set_mode(
            "term" if self.mode == "graph" else "graph"))
        for i, iv in enumerate(INTERVALS):
            self.bind("<KeyPress-%d>" % (i + 1), lambda e, v=iv: self.set_interval(v))
        self.bind("<KeyPress-o>", lambda e: self.toggle_topmost())
        self.bind("<F11>", lambda e: self.toggle_full())
        self.bind("<Escape>", lambda e: self._full and self.toggle_full())
        self.bind("<Control-q>", lambda e: self.quit_app())

    # --------------------------------------------------------------- actions
    def set_mode(self, mode):
        self.mode = mode
        self._apply_mode()
        self._render()

    def _apply_mode(self):
        self.graph.pack_forget()
        self.term.frame.pack_forget()
        (self.graph if self.mode == "graph" else self.term.frame).pack(
            fill="both", expand=True)
        self._mark(self.btn_graph, self.mode == "graph")
        self._mark(self.btn_term, self.mode == "term")

    def set_interval(self, iv):
        self.interval = iv
        self.sampler.interval = iv
        for v, b in self.btn_iv:
            self._mark(b, v == iv)

    def toggle_topmost(self):
        self.topmost = not self.topmost
        self.attributes("-topmost", self.topmost)
        self._mark(self.btn_top, self.topmost)

    def toggle_full(self):
        self._full = not self._full
        self.attributes("-fullscreen", self._full)

    # ---------------------------------------------------------------- render
    def _tick(self):
        if self.winfo_viewable():      # minimiert = nichts zeichnen
            self._render()
        self.after(int(self.interval * 1000), self._tick)

    def _render(self):
        snap = self.sampler.snapshot()
        if self.mode == "graph":
            for p in self.panels:
                p.render(snap)
        else:
            self.term.render(snap)
        g = snap["gpu"]
        self.status.configure(text="CPU %.0f%%   GPU %.0f%%   RAM %.0f%%" % (
            snap["cpu"]["total"], g["util"] if g else 0.0,
            snap["mem"].get("percent", 0.0)))

    # ------------------------------------------------------------------ exit
    def quit_app(self):
        try:
            with open(CFG, "w", encoding="utf-8") as fh:
                json.dump({"geometry": self.winfo_geometry(), "mode": self.mode,
                           "interval": self.interval, "topmost": self.topmost}, fh)
        except Exception:
            pass
        self.sampler.stop()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.set_interval(app.interval)
    app.mainloop()
