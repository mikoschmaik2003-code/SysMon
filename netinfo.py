"""Daten fuer die NETZ-Ansicht: IP-Konfiguration, Ping, Ports, Laufwerke, System.

Laeuft in eigenen Threads und nur, solange die Ansicht offen ist - in GRAFIK
und TERMINAL kostet das nichts und es wird auch nichts gepingt.
"""

import collections
import ctypes
import getpass
import os
import platform
import socket
import subprocess
import threading
import time
import urllib.request

import psutil

import winapi

CREATE_NO_WINDOW = 0x08000000
INTERNET = ("1.1.1.1", "Internet (1.1.1.1)")
PING_HISTORY = 40


def _reg(path, *names):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        out = []
        for n in names:
            try:
                out.append(winreg.QueryValueEx(key, n)[0])
            except OSError:
                out.append("")
        winreg.CloseKey(key)
        return out
    except Exception:
        return [""] * len(names)


def _windows_version():
    name, disp, build, ubr = _reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                                  "ProductName", "DisplayVersion", "CurrentBuild", "UBR")
    try:
        if int(build) >= 22000:          # die Registry sagt auch bei 11 noch "10"
            name = name.replace("Windows 10", "Windows 11")
    except ValueError:
        pass
    name = name or ("Windows " + platform.release())
    ver = " ".join(x for x in (str(disp), "Build %s.%s" % (build, ubr) if build else "")
                   if x)
    return name, ver


def _volume_label(root):
    buf = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    try:
        if ctypes.windll.kernel32.GetVolumeInformationW(
                root, buf, 261, None, None, None, fs, 261):
            return buf.value, fs.value
    except Exception:
        pass
    return "", ""


class NetInfo:
    def __init__(self, pid_names, gpu_driver=""):
        self.pid_names = pid_names          # Callable -> {pid: name}
        self.lock = threading.Lock()
        self.active = threading.Event()
        self._stop = threading.Event()
        self.adapters_api = winapi.Adapters()
        self.pinger = winapi.Pinger()

        win, ver = _windows_version()
        board = _reg(r"HARDWARE\DESCRIPTION\System\BIOS", "BaseBoardManufacturer",
                     "BaseBoardProduct", "BIOSVersion", "BIOSReleaseDate")
        try:
            user = getpass.getuser()
        except Exception:
            user = os.environ.get("USERNAME", "")
        self.system = {
            "host": socket.gethostname(), "user": user,
            "windows": win, "winver": ver,
            "board": " ".join(x for x in board[:2] if x).strip(),
            "bios": " ".join(x for x in board[2:] if x).strip(),
            "boot": psutil.boot_time(),
            "gpu_driver": gpu_driver,
        }

        self.adapters = []
        self.rates = {}                      # Adaptername -> (down B/s, up B/s)
        self.ports = []
        self.conn = {"est": 0, "listen": 0, "udp": 0}
        self.volumes = []
        self.targets = []                    # [(ip, label)]
        self.pings = {}                      # ip -> deque der ms-Werte (None = Verlust)
        self.public_ip = ""
        self.message = ""

        self._prev_io = None
        self._prev_t = 0.0
        for fn in (self._loop_info, self._loop_ping):
            threading.Thread(target=fn, daemon=True).start()

    # --------------------------------------------------------------- Steuerung
    def set_active(self, on):
        (self.active.set if on else self.active.clear)()

    def stop(self):
        self._stop.set()
        self.active.set()

    def _wait(self, sec):
        """Schlafen; ist die Ansicht zu, bis sie wieder aufgeht."""
        if self._stop.wait(sec):
            return False
        self.active.wait()
        return not self._stop.is_set()

    def note(self, msg):
        with self.lock:
            self.message = msg

    # ------------------------------------------------------------- Abfragen
    def _loop_info(self):
        n = 0
        while self._wait(0 if n == 0 else 2.0):
            try:
                self._read_adapters()
                if n % 3 == 0:
                    self._read_ports()
                if n % 5 == 0:
                    self._read_volumes()
            except Exception:
                pass
            n += 1

    def _read_adapters(self):
        adapters = self.adapters_api.read()
        io = psutil.net_io_counters(pernic=True)
        now = time.time()
        rates = {}
        if self._prev_io:
            dt = max(now - self._prev_t, 1e-3)
            for name, cur in io.items():
                prev = self._prev_io.get(name)
                if prev:
                    rates[name] = (max(cur.bytes_recv - prev.bytes_recv, 0) / dt,
                                   max(cur.bytes_sent - prev.bytes_sent, 0) / dt)
        self._prev_io, self._prev_t = io, now

        # aktive Adapter mit Gateway zuerst, danach nach Metrik
        adapters.sort(key=lambda a: (not a["up"], not a["gw"], a["metric"], a["name"]))
        targets = []
        prim = next((a for a in adapters if a["up"] and a["gw"]), None)
        if prim:
            gw = next((g for g in prim["gw"] if "." in g), "")
            if gw:
                targets.append((gw, "Router " + gw))
            dns = next((d for d in prim["dns"] if "." in d), "")
            if dns and dns != gw:
                targets.append((dns, "DNS " + dns))
        targets.append(INTERNET)
        with self.lock:
            self.adapters = adapters
            self.rates = rates
            self.targets = targets

    def _read_ports(self):
        names = self.pid_names() or {}
        listen, est, udp = {}, 0, 0
        for c in psutil.net_connections(kind="inet"):
            if c.type == socket.SOCK_DGRAM:
                udp += 1
                continue
            if c.status == psutil.CONN_ESTABLISHED:
                est += 1
            elif c.status == psutil.CONN_LISTEN and c.laddr:
                ip, port = c.laddr.ip, c.laddr.port
                scope = ("lokal" if ip.startswith("127.") or ip == "::1"
                         else "alle" if ip in ("0.0.0.0", "::") else "LAN")
                key = (port, c.pid)
                old = listen.get(key)
                if old is None or old[1] != "alle":
                    listen[key] = (port, scope,
                                   names.get(c.pid, "pid %s" % c.pid if c.pid else "System"))
        rows = sorted(set(listen.values()), key=lambda r: (r[1] == "lokal", r[0]))
        with self.lock:
            self.ports = rows
            self.conn = {"est": est, "listen": len(rows), "udp": udp}

    def _read_volumes(self):
        vols = []
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
            except Exception:
                continue
            label, fs = _volume_label(p.mountpoint)
            vols.append({"root": p.mountpoint.rstrip("\\"), "label": label,
                         "fs": fs or p.fstype, "total": u.total, "used": u.used,
                         "free": u.free, "pct": u.percent})
        with self.lock:
            self.volumes = vols

    def _loop_ping(self):
        while self._wait(1.0):
            with self.lock:
                targets = list(self.targets)
            for ip, _ in targets:
                ms = self.pinger.ping(ip, 1000)
                with self.lock:
                    self.pings.setdefault(
                        ip, collections.deque(maxlen=PING_HISTORY)).append(ms)

    # ---------------------------------------------------------------- Werkzeuge
    def fetch_public_ip(self):
        """Fragt einmalig api.ipify.org - nur auf Knopfdruck, nie automatisch."""
        def run():
            self.note("frage oeffentliche IP ab ...")
            try:
                with urllib.request.urlopen("https://api.ipify.org", timeout=6) as r:
                    ip = r.read().decode("ascii", "replace").strip()
                with self.lock:
                    self.public_ip = ip
                self.note("oeffentliche IP: " + ip)
            except Exception as e:
                self.note("oeffentliche IP nicht erreichbar (%s)" % type(e).__name__)
        threading.Thread(target=run, daemon=True).start()

    def flush_dns(self):
        def run():
            try:
                r = subprocess.run(["ipconfig", "/flushdns"], capture_output=True,
                                   timeout=10, creationflags=CREATE_NO_WINDOW)
                self.note("DNS-Cache geleert" if r.returncode == 0
                          else "DNS-Cache leeren fehlgeschlagen")
            except Exception:
                self.note("DNS-Cache leeren fehlgeschlagen")
        threading.Thread(target=run, daemon=True).start()

    def primary_ip(self):
        with self.lock:
            for a in self.adapters:
                if a["up"] and a["gw"] and a["v4"]:
                    return a["v4"][0][0]
        return ""

    # --------------------------------------------------------------- Snapshot
    def snapshot(self):
        with self.lock:
            return {
                "system": dict(self.system),
                "adapters": [dict(a) for a in self.adapters],
                "rates": dict(self.rates),
                "ports": list(self.ports),
                "conn": dict(self.conn),
                "volumes": [dict(v) for v in self.volumes],
                "targets": list(self.targets),
                "pings": {k: list(v) for k, v in self.pings.items()},
                "public_ip": self.public_ip,
                "message": self.message,
            }
