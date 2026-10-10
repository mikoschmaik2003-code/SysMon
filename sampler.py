"""Sammelt alle Systemwerte in Hintergrund-Threads und stellt sie als Snapshot bereit."""

import collections
import subprocess
import threading
import time

import psutil

import winapi

HISTORY = 60  # Sekunden-Fenster wie im Task-Manager
CREATE_NO_WINDOW = 0x08000000


def _ring():
    return collections.deque([0.0] * HISTORY, maxlen=HISTORY)


def _cpu_name():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        mhz = winreg.QueryValueEx(key, "~MHz")[0]
        winreg.CloseKey(key)
        return name, mhz / 1000.0
    except Exception:
        return "Unbekannte CPU", 0.0


def _run(cmd, timeout=6):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=CREATE_NO_WINDOW)


class Sampler:
    """Alle Messwerte, thread-sicher unter self.lock.

    Drei Threads: schnelle Werte (CPU/RAM/Disk/Netz/Takt), GPU (NVML) und die
    teuren Sachen (Prozessliste, Handles, GPU je Prozess).
    """

    def __init__(self, interval=1.0):
        self.interval = interval
        self.lock = threading.RLock()
        self._stop = threading.Event()

        self.cpu_name, self.cpu_base_ghz = _cpu_name()
        self.cores_phys = psutil.cpu_count(logical=False) or 1
        self.cores_log = psutil.cpu_count(logical=True) or 1

        self.clocks = winapi.CoreClocks(self.cpu_base_ghz, self.cores_log)
        self.nvml = winapi.Nvml()
        self.gpu_procs = winapi.GpuProcs()
        self.gpu_mem = winapi.GpuProcMem()
        self.proctable = winapi.ProcTable(self.cores_log)

        # Verlaeufe
        self.h_cpu = _ring()
        self.h_core = [_ring() for _ in range(self.cores_log)]
        self.h_mem = _ring()
        self.h_gpu = _ring()
        self.h_gpu_enc = _ring()
        self.h_gpu_dec = _ring()
        self.h_vram = _ring()
        self.h_net_up = _ring()
        self.h_net_dn = _ring()

        self.cpu = {"total": 0.0, "per_core": [0.0] * self.cores_log, "freq": 0.0}
        self.core_ghz = None
        self.mem = {}
        self.disks = []
        self.h_disk = {}          # disk-key -> (read-ring, write-ring, aktiv-ring)
        self.net = {"up": 0.0, "down": 0.0, "name": "-", "up_total": 0, "dn_total": 0}
        self.gpu = None
        self.procs = {"processes": 0, "threads": 0, "handles": 0}
        self.top = []             # groesste Verbraucher (CPU)
        self.top_mem = []         # groesste Verbraucher (RAM)
        self.top_gpu = []         # groesste Verbraucher (GPU)
        self.top_vram = []        # groesste Verbraucher (VRAM, Bytes)
        self.disk_labels = {}     # PhysicalDriveN -> "Disk 0 (C:)"
        self.net_peak = 1.0

        self._prev_disk = psutil.disk_io_counters(perdisk=True)
        self._prev_net = psutil.net_io_counters(pernic=True)
        self._prev_t = time.time()
        psutil.cpu_percent(percpu=True)

        self._threads = [
            threading.Thread(target=self._loop_fast, daemon=True),
            threading.Thread(target=self._loop_gpu, daemon=True),
            threading.Thread(target=self._loop_slow, daemon=True),
        ]

    # ------------------------------------------------------------------ start
    def start(self):
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop.set()
        self.nvml.close()

    # --------------------------------------------------------- schnelle Werte
    def _loop_fast(self):
        while not self._stop.wait(self.interval):
            try:
                self._sample()
            except Exception:
                pass

    def _sample(self):
        now = time.time()
        dt = max(now - self._prev_t, 1e-3)
        self._prev_t = now

        per_core = psutil.cpu_percent(percpu=True)
        total = sum(per_core) / len(per_core) if per_core else 0.0

        # echter Takt je Kern (PDH); faellt auf psutil zurueck
        core_ghz, freq = self.clocks.read()
        if not freq:
            try:
                freq = (psutil.cpu_freq().current or 0.0) / 1000.0
            except Exception:
                freq = 0.0

        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()

        cur_disk = psutil.disk_io_counters(perdisk=True)
        disks = []
        for key, cur in cur_disk.items():
            prev = self._prev_disk.get(key)
            if prev is None:
                continue
            read = (cur.read_bytes - prev.read_bytes) / dt
            write = (cur.write_bytes - prev.write_bytes) / dt
            busy = getattr(cur, "read_time", 0) + getattr(cur, "write_time", 0)
            pbusy = getattr(prev, "read_time", 0) + getattr(prev, "write_time", 0)
            active = min(100.0, (busy - pbusy) / (dt * 10.0))
            disks.append({
                "key": key,
                "label": self.disk_labels.get(key, key.replace("PhysicalDrive", "Disk ")),
                "read": max(read, 0.0),
                "write": max(write, 0.0),
                "active": max(active, 0.0),
                "iops": max((cur.read_count + cur.write_count
                             - prev.read_count - prev.write_count) / dt, 0.0),
            })
        self._prev_disk = cur_disk

        cur_net = psutil.net_io_counters(pernic=True)
        best, up, dn = "-", 0.0, 0.0
        for nic, cur in cur_net.items():
            prev = self._prev_net.get(nic)
            if prev is None:
                continue
            u = (cur.bytes_sent - prev.bytes_sent) / dt
            d = (cur.bytes_recv - prev.bytes_recv) / dt
            if u + d >= up + dn:
                best, up, dn = nic, u, d
        agg = psutil.net_io_counters()
        self._prev_net = cur_net

        with self.lock:
            self.cpu = {"total": total, "per_core": per_core, "freq": freq}
            self.core_ghz = core_ghz
            self.h_cpu.append(total)
            for i, v in enumerate(per_core[: len(self.h_core)]):
                self.h_core[i].append(v)

            self.mem = {
                "used": vm.total - vm.available,
                "total": vm.total,
                "percent": vm.percent,
                "available": vm.available,
                "cached": getattr(vm, "cached", 0),
                "swap_used": sm.used,
                "swap_total": sm.total,
            }
            self.h_mem.append(vm.percent)

            self.disks = sorted(disks, key=lambda d: d["key"])
            for d in self.disks:
                r = self.h_disk.setdefault(d["key"], (_ring(), _ring(), _ring()))
                r[0].append(d["read"])
                r[1].append(d["write"])
                r[2].append(d["active"])

            self.net = {"up": up, "down": dn, "name": best,
                        "up_total": agg.bytes_sent, "dn_total": agg.bytes_recv}
            self.h_net_up.append(up)
            self.h_net_dn.append(dn)
            self.net_peak = max(1.0, max(self.h_net_up), max(self.h_net_dn))

    # ------------------------------------------------------------------- GPU
    def _loop_gpu(self):
        while True:
            gpu = self.nvml.poll() if self.nvml.ok else self._gpu_smi()
            with self.lock:
                if gpu:
                    self.gpu = gpu
                    self.h_gpu.append(gpu["util"])
                    self.h_gpu_enc.append(gpu["enc"])
                    self.h_gpu_dec.append(gpu["dec"])
                    self.h_vram.append(gpu["vram_pct"])
            if self._stop.wait(self.interval):
                return

    def _gpu_smi(self):
        """Nur noch Notnagel, falls nvml.dll fehlt."""
        fields = ("name,utilization.gpu,utilization.encoder,utilization.decoder,"
                  "memory.used,memory.total,temperature.gpu,power.draw,power.limit,"
                  "clocks.sm,clocks.mem,fan.speed")
        try:
            out = _run(["nvidia-smi", "--query-gpu=" + fields,
                        "--format=csv,noheader,nounits"]).stdout
            v = [x.strip() for x in out.strip().splitlines()[0].split(",")]

            def num(i):
                try:
                    return float(v[i])
                except Exception:
                    return 0.0

            used, tot = num(4), max(num(5), 1.0)
            return {"name": v[0], "util": num(1), "enc": num(2), "dec": num(3),
                    "vram_used": used, "vram_total": tot,
                    "vram_pct": used / tot * 100.0, "temp": num(6),
                    "power": num(7), "power_limit": max(num(8), 1.0),
                    "clk_sm": num(9), "clk_mem": num(10), "fan": num(11)}
        except Exception:
            return None

    # ------------------------------------------------- langsame / teure Werte
    def _loop_slow(self):
        self._map_disks()
        while not self._stop.is_set():
            try:
                self._scan_procs()
            except Exception:
                pass
            if self._stop.wait(2.0):
                return

    def _scan_procs(self):
        """Prozessliste + Summen in einem Kernel-Aufruf (siehe winapi.ProcTable)."""
        rows, totals = self.proctable.read()
        if not rows:
            return
        gpu_by_pid = self.gpu_procs.read()
        vram_by_pid = self.gpu_mem.read()
        for r in rows:
            r["gpu"] = gpu_by_pid.get(r["pid"], 0.0)
            r["vram"] = vram_by_pid.get(r["pid"], 0.0)

        top_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:20]
        top_mem = sorted(rows, key=lambda r: r["rss"], reverse=True)[:20]
        top_gpu = [r for r in sorted(rows, key=lambda r: r["gpu"], reverse=True)
                   if r["gpu"] > 0.0][:20]
        top_vram = [r for r in sorted(rows, key=lambda r: r["vram"], reverse=True)
                    if r["vram"] > 0.0][:20]
        with self.lock:
            self.procs = totals
            self.top = top_cpu
            self.top_mem = top_mem
            self.top_gpu = top_gpu
            self.top_vram = top_vram

    def _map_disks(self):
        """PhysicalDriveN -> Laufwerksbuchstaben (einmalig via PowerShell)."""
        try:
            ps = ('Get-Partition | Where-Object DriveLetter | '
                  'ForEach-Object { "$($_.DiskNumber);$($_.DriveLetter)" }')
            out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=25).stdout
            mapping = {}
            for line in out.splitlines():
                if ";" not in line:
                    continue
                num, letter = line.strip().split(";", 1)
                mapping.setdefault("PhysicalDrive" + num.strip(), []).append(letter.strip())
            labels = {}
            for key, letters in mapping.items():
                pretty = ", ".join(x + ":" for x in letters)
                labels[key] = key.replace("PhysicalDrive", "Disk ") + " (" + pretty + ")"
            with self.lock:
                self.disk_labels.update(labels)
        except Exception:
            pass

    # --------------------------------------------------------------- Snapshot
    def snapshot(self):
        with self.lock:
            return {
                "cpu": dict(self.cpu),
                "core_ghz": list(self.core_ghz) if self.core_ghz else None,
                "cpu_name": self.cpu_name,
                "cpu_base": self.cpu_base_ghz,
                "cores_phys": self.cores_phys,
                "cores_log": self.cores_log,
                "h_cpu": list(self.h_cpu),
                "h_core": [list(r) for r in self.h_core],
                "mem": dict(self.mem),
                "h_mem": list(self.h_mem),
                "disks": [dict(d) for d in self.disks],
                "h_disk": {k: (list(a), list(b), list(c))
                           for k, (a, b, c) in self.h_disk.items()},
                "net": dict(self.net),
                "h_net_up": list(self.h_net_up),
                "h_net_dn": list(self.h_net_dn),
                "net_peak": self.net_peak,
                "gpu": dict(self.gpu) if self.gpu else None,
                "h_gpu": list(self.h_gpu),
                "h_gpu_enc": list(self.h_gpu_enc),
                "h_gpu_dec": list(self.h_gpu_dec),
                "h_vram": list(self.h_vram),
                "procs": dict(self.procs),
                "top": [dict(r) for r in self.top],
                "top_mem": [dict(r) for r in self.top_mem],
                "top_gpu": [dict(r) for r in self.top_gpu],
                "top_vram": [dict(r) for r in self.top_vram],
                "uptime": time.time() - psutil.boot_time(),
            }
