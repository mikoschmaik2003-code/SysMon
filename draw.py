"""Zeichen-Helfer im Retained-Mode.

Canvas-Items neu zu erzeugen kostet auf Windows ~0,2 ms pro Item - bei 325 Items
im CPU-Panel waren das >200 ms pro Bild und damit die Ruckler. Die Scene legt jedes
Item genau einmal an und schickt danach nur noch das, was sich wirklich geaendert
hat (coords / itemconfigure). Statische Teile wie Raster und Titel kosten null.
"""

import math
import tkinter.font as tkfont

from theme import THEME

_FONTS = {}


def F(size=9, bold=False):
    """Font-Objekte cachen - Tk muss den Font-Namen sonst bei jedem Item parsen."""
    key = (size, bold)
    f = _FONTS.get(key)
    if f is None:
        f = _FONTS[key] = tkfont.Font(family="Consolas", size=size,
                                      weight="bold" if bold else "normal")
    return f


def best_grid(n, w, h, target=1.55):
    """Spalten/Zeilen so waehlen, dass die Zellen ein schoenes Seitenverhaeltnis haben."""
    best, score = (1, n), 1e9
    for cols in range(1, min(n, 16) + 1):
        rows = math.ceil(n / cols)
        cw, ch = w / cols, h / rows
        if ch <= 0:
            continue
        s = abs(cw / ch - target) + 0.15 * (cols * rows - n)
        if s < score:
            best, score = (cols, rows), s
    return best


class Scene:
    """Haelt die Canvas-Items eines Panels und aktualisiert sie inkrementell."""

    def __init__(self, cv):
        self.cv = cv
        self.ids = {}
        self.geo = {}
        self.opt = {}
        self.sig = None

    def begin(self, sig):
        """Layout-Signatur (Groesse, Anzahl Elemente). Aendert sie sich, neu aufbauen."""
        if sig == self.sig:
            return False
        self.cv.delete("all")
        self.ids.clear()
        self.geo.clear()
        self.opt.clear()
        self.sig = sig
        return True

    # --------------------------------------------------------------- Basics
    def _upd(self, key, item, coords, opts):
        if self.geo.get(key) != coords:
            self.cv.coords(item, *coords)
            self.geo[key] = coords
        cur = self.opt.get(key)
        if cur != opts:
            diff = {k: v for k, v in opts.items() if cur.get(k) != v}
            if diff:
                self.cv.itemconfigure(item, **diff)
            self.opt[key] = opts

    def rect(self, key, x0, y0, x1, y1, fill="", outline=""):
        i = self.ids.get(key)
        coords, opts = (x0, y0, x1, y1), {"fill": fill, "outline": outline}
        if i is None:
            i = self.ids[key] = self.cv.create_rectangle(*coords, **opts)
            self.geo[key], self.opt[key] = coords, opts
        else:
            self._upd(key, i, coords, opts)
        return i

    def line(self, key, coords, fill, width=1):
        i = self.ids.get(key)
        coords = tuple(coords)
        opts = {"fill": fill, "width": width}
        if i is None:
            i = self.ids[key] = self.cv.create_line(coords, **opts)
            self.geo[key], self.opt[key] = coords, opts
        else:
            self._upd(key, i, coords, opts)
        return i

    def poly(self, key, coords, fill="", outline="", width=1, smooth=False):
        i = self.ids.get(key)
        coords = tuple(coords)
        opts = {"fill": fill, "outline": outline, "width": width}
        if i is None:
            i = self.ids[key] = self.cv.create_polygon(coords, smooth=smooth,
                                                       splinesteps=12, **opts)
            self.geo[key], self.opt[key] = coords, opts
        else:
            self._upd(key, i, coords, opts)
        return i

    def text(self, key, x, y, text, fill=None, size=9, bold=False, anchor="w"):
        i = self.ids.get(key)
        fill = fill or THEME["text"]
        coords, opts = (x, y), {"text": text, "fill": fill}
        if i is None:
            i = self.ids[key] = self.cv.create_text(x, y, text=text, fill=fill,
                                                    font=F(size, bold),
                                                    anchor=anchor)
            self.geo[key], self.opt[key] = coords, opts
        else:
            self._upd(key, i, coords, opts)
        return i

    # ------------------------------------------------------------ Bausteine
    def panel(self, key, x0, y0, x1, y1, title="", accent=None, sub=""):
        """Rahmen + Titelzeile. Gibt den nutzbaren Innenbereich zurueck."""
        accent = accent or THEME["cpu"]
        r = 6
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
               x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
               x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        self.poly(key + "/bg", pts, fill=THEME["panel"], outline=THEME["border"],
                  smooth=True)
        ty = y0 + 14
        if title:
            self.rect(key + "/tick", x0 + 10, ty - 6, x0 + 13, ty + 7,
                      fill=accent, outline=accent)
            self.text(key + "/title", x0 + 20, ty, title, THEME["text_hi"], 10, True)
        if sub:
            self.text(key + "/sub", x1 - 10, ty, sub, THEME["dim"], 9, anchor="e")
        return x0 + 10, y0 + (26 if title else 8), x1 - 10, y1 - 8

    def grid(self, key, x0, y0, x1, y1, cells=(10, 5)):
        self.rect(key + "/bg", x0, y0, x1, y1, fill=THEME["bg"],
                  outline=THEME["border"])
        for i in range(1, cells[0]):
            x = x0 + (x1 - x0) * i / cells[0]
            self.line(key + "/v%d" % i, (x, y0, x, y1), THEME["grid"])
        for i in range(1, cells[1]):
            y = y0 + (y1 - y0) * i / cells[1]
            self.line(key + "/h%d" % i, (x0, y, x1, y), THEME["grid"])

    def area(self, key, x0, y0, x1, y1, values, line, fill, vmax=100.0, width=1.4):
        """Gefuellte Verlaufskurve - ein einziges Item pro Kurve."""
        n = len(values)
        if n < 2 or x1 <= x0:
            return
        vmax = max(vmax, 1e-9)
        step = (x1 - x0) / (n - 1)
        h = y1 - y0
        pts = []
        for i, v in enumerate(values):
            f = v / vmax
            f = 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)
            pts.append(x0 + i * step)
            pts.append(y1 - f * h)
        if fill:
            pts += [x1, y1, x0, y1]
            self.poly(key, pts, fill=fill, outline=line, width=width)
        else:
            self.line(key, pts, line, width)

    def bar(self, key, x0, y0, x1, y1, pct, color, bg=None, text=None,
            text_color=None):
        pct = 0.0 if pct < 0.0 else (100.0 if pct > 100.0 else pct)
        self.rect(key + "/bg", x0, y0, x1, y1, fill=bg or THEME["bg"],
                  outline=THEME["border"])
        w = (x1 - x0 - 2) * pct / 100.0
        self.rect(key + "/fg", x0 + 1, y0 + 1, x0 + 1 + max(w, 0.0), y1 - 1,
                  fill=color, outline="")
        if text is not None:
            self.text(key + "/txt", (x0 + x1) / 2, (y0 + y1) / 2, text,
                      text_color or THEME["text"], 8, True, anchor="center")
