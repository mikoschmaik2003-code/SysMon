"""Direkte Windows-/NVIDIA-Schnittstellen - ohne Subprozesse, damit nichts haengt.

* CoreClocks - echter Takt pro logischem Kern ueber den PDH-Zaehler
  "% Processor Performance" (dieselbe Quelle, aus der auch der Task-Manager liest)
* GpuProcs   - GPU-Auslastung je Prozess ueber "\\GPU Engine(*)\\Utilization Percentage"
* GpuProcMem - dedizierter VRAM je Prozess ueber "\\GPU Process Memory(*)\\Dedicated Usage"
* Nvml       - GPU-Werte direkt aus nvml.dll statt via "nvidia-smi" (5 ms statt ~150 ms)
"""

import collections
import ctypes
import time
from ctypes import wintypes

PDH_FMT_DOUBLE = 0x00000200


class _Value(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong), ("ansi", ctypes.c_char_p)]


class _CounterValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _Value)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _CounterValue)]


class _Counter:
    """Ein PDH-Wildcard-Zaehler, der seinen Puffer wiederverwendet."""

    def __init__(self, path):
        self.ok = False
        self._buf = ctypes.create_string_buffer(0)
        try:
            self.pdh = ctypes.WinDLL("pdh.dll")
            self.q = wintypes.HANDLE()
            if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) != 0:
                return
            self.c = wintypes.HANDLE()
            if self.pdh.PdhAddEnglishCounterW(self.q, path, 0,
                                              ctypes.byref(self.c)) != 0:
                return
            self.pdh.PdhCollectQueryData(self.q)   # erster Messpunkt
            self.ok = True
        except Exception:
            self.ok = False

    def sample(self):
        """Liste (Instanzname, Wert) oder None."""
        if not self.ok:
            return None
        try:
            if self.pdh.PdhCollectQueryData(self.q) != 0:
                return None
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            self.pdh.PdhGetFormattedCounterArrayW(self.c, PDH_FMT_DOUBLE,
                                                  ctypes.byref(size),
                                                  ctypes.byref(count), None)
            if not count.value:
                return None
            if size.value > len(self._buf):
                self._buf = ctypes.create_string_buffer(size.value)
            if self.pdh.PdhGetFormattedCounterArrayW(self.c, PDH_FMT_DOUBLE,
                                                     ctypes.byref(size),
                                                     ctypes.byref(count),
                                                     self._buf) != 0:
                return None
            items = ctypes.cast(self._buf,
                                ctypes.POINTER(_Item * count.value)).contents
            return [(it.szName or "", it.FmtValue.value.doubleValue) for it in items]
        except Exception:
            return None


class CoreClocks:
    """Taktrate je logischem Prozessor in GHz."""

    def __init__(self, base_ghz, ncores):
        self.base = base_ghz or 0.0
        self.n = ncores
        self.ctr = _Counter(r"\Processor Information(*)\% Processor Performance")
        self.ok = self.ctr.ok and self.base > 0

    @staticmethod
    def _order(name):
        try:
            grp, idx = name.split(",")
            return (int(grp), int(idx))
        except Exception:
            return (9999, 9999)

    def read(self):
        """(Liste GHz je Kern, Gesamt-GHz) - bei Problemen (None, 0.0)."""
        rows = self.ctr.sample() if self.ok else None
        if not rows:
            return None, 0.0
        per, total = [], 0.0
        for name, pct in rows:
            if name.endswith("_Total"):
                if name == "_Total":
                    total = self.base * pct / 100.0
                continue
            per.append((self._order(name), self.base * pct / 100.0))
        if not per:
            return None, 0.0
        per.sort()
        ghz = [g for _, g in per][: self.n]
        while len(ghz) < self.n:
            ghz.append(total)
        return ghz, (total or sum(ghz) / len(ghz))


class GpuProcs:
    """GPU-Auslastung je Prozess-ID (max ueber alle Engines, wie im Task-Manager)."""

    def __init__(self):
        self.ctr = _Counter(r"\GPU Engine(*)\Utilization Percentage")
        self.ok = self.ctr.ok

    def read(self):
        rows = self.ctr.sample() if self.ok else None
        if not rows:
            return {}
        per = collections.defaultdict(float)
        for name, val in rows:
            if not name.startswith("pid_") or val <= 0.0:
                continue
            try:
                pid = int(name.split("_", 2)[1])
            except Exception:
                continue
            if val > per[pid]:
                per[pid] = val
        return dict(per)


class GpuProcMem:
    """Dedizierter VRAM je Prozess-ID in Bytes (Summe ueber alle Adapter).

    Quelle ist "\\GPU Process Memory(*)\\Dedicated Usage" - genau der Wert, den
    der Task-Manager unter "Dedizierter GPU-Speicher" zeigt. NVML liefert den
    unter WDDM nicht (usedGpuMemory ist dort immer "nicht verfuegbar").
    """

    def __init__(self):
        self.ctr = _Counter(r"\GPU Process Memory(*)\Dedicated Usage")
        self.ok = self.ctr.ok

    def read(self):
        rows = self.ctr.sample() if self.ok else None
        if not rows:
            return {}
        per = collections.defaultdict(float)
        for name, val in rows:
            if not name.startswith("pid_") or val <= 0.0:
                continue
            try:
                pid = int(name.split("_", 2)[1])
            except Exception:
                continue
            per[pid] += val
        return dict(per)


# --------------------------------------------------------------------- NVML
class _Util(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Mem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class Nvml:
    """Minimaler NVML-Wrapper - nur die Felder, die das Panel anzeigt."""

    CLOCK_SM, CLOCK_MEM = 1, 2

    def __init__(self):
        self.ok = False
        self.name = ""
        try:
            self.lib = ctypes.CDLL("nvml.dll")
            if self.lib.nvmlInit_v2() != 0:
                return
            self.h = ctypes.c_void_p()
            if self.lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.h)) != 0:
                return
            buf = ctypes.create_string_buffer(96)
            if self.lib.nvmlDeviceGetName(self.h, buf, 96) == 0:
                self.name = buf.value.decode("utf-8", "replace")
            self.ok = True
        except Exception:
            self.ok = False

    def _u32(self, fn, *extra):
        v = ctypes.c_uint(0)
        try:
            if fn(self.h, *(extra + (ctypes.byref(v),))) == 0:
                return float(v.value)
        except Exception:
            pass
        return 0.0

    def poll(self):
        if not self.ok:
            return None
        lib = self.lib
        util, mem = _Util(), _Mem()
        try:
            lib.nvmlDeviceGetUtilizationRates(self.h, ctypes.byref(util))
            lib.nvmlDeviceGetMemoryInfo(self.h, ctypes.byref(mem))
        except Exception:
            return None
        enc, dec, samp = ctypes.c_uint(0), ctypes.c_uint(0), ctypes.c_uint(0)
        try:
            lib.nvmlDeviceGetEncoderUtilization(self.h, ctypes.byref(enc),
                                                ctypes.byref(samp))
            lib.nvmlDeviceGetDecoderUtilization(self.h, ctypes.byref(dec),
                                                ctypes.byref(samp))
        except Exception:
            pass
        total_mb = max(mem.total / 1048576.0, 1.0)
        used_mb = mem.used / 1048576.0
        limit = self._u32(lib.nvmlDeviceGetEnforcedPowerLimit) / 1000.0
        return {
            "name": self.name,
            "util": float(util.gpu),
            "enc": float(enc.value),
            "dec": float(dec.value),
            "vram_used": used_mb,
            "vram_total": total_mb,
            "vram_pct": used_mb / total_mb * 100.0,
            "temp": self._u32(lib.nvmlDeviceGetTemperature, ctypes.c_uint(0)),
            "power": self._u32(lib.nvmlDeviceGetPowerUsage) / 1000.0,
            "power_limit": max(limit, 1.0),
            "clk_sm": self._u32(lib.nvmlDeviceGetClockInfo,
                                ctypes.c_uint(self.CLOCK_SM)),
            "clk_mem": self._u32(lib.nvmlDeviceGetClockInfo,
                                 ctypes.c_uint(self.CLOCK_MEM)),
            "fan": self._u32(lib.nvmlDeviceGetFanSpeed),
        }

    def close(self):
        try:
            if self.ok:
                self.lib.nvmlShutdown()
        except Exception:
            pass


# ------------------------------------------------------- Prozessliste (ntdll)
class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


class _SysProc(ctypes.Structure):
    """SYSTEM_PROCESS_INFORMATION - nur bis zu den Feldern, die wir brauchen."""
    _fields_ = [
        ("NextEntryOffset", ctypes.c_ulong),
        ("NumberOfThreads", ctypes.c_ulong),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", ctypes.c_ulong),
        ("NumberOfThreadsHighWatermark", ctypes.c_ulong),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UnicodeString),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", ctypes.c_ulong),
        ("SessionId", ctypes.c_ulong),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
    ]


class ProcTable:
    """Alle Prozesse in einem einzigen Kernel-Aufruf.

    psutil braucht fuer Name/Threads/Speicher pro Prozess je einen eigenen
    System-Snapshot - auf diesem Rechner ueber 2,5 s fuer ~300 Prozesse und
    damit der Hauptgrund fuer Ruckler. NtQuerySystemInformation liefert alles
    auf einmal in wenigen Millisekunden.
    """

    SYSTEM_PROCESS_INFORMATION = 5
    STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

    def __init__(self, ncores):
        self.ncores = max(ncores, 1)
        self.ok = False
        self._prev = {}          # pid -> (cpu-zeit in 100ns, zeitstempel)
        self._size = 1 << 20
        try:
            self.ntdll = ctypes.WinDLL("ntdll.dll")
            self.ok = True
        except Exception:
            self.ok = False

    def read(self):
        """(Liste Prozesse, Summen-Dict) - Prozesse mit pid/name/cpu/rss/threads."""
        if not self.ok:
            return [], {}
        buf = ctypes.create_string_buffer(self._size)
        need = ctypes.c_ulong(0)
        for _ in range(6):
            st = self.ntdll.NtQuerySystemInformation(
                self.SYSTEM_PROCESS_INFORMATION, buf, len(buf), ctypes.byref(need))
            if (st & 0xFFFFFFFF) != self.STATUS_INFO_LENGTH_MISMATCH:
                break
            self._size = max(need.value, self._size * 2) + (1 << 16)
            buf = ctypes.create_string_buffer(self._size)
        if st != 0:
            return [], {}

        now = time.perf_counter()
        base = ctypes.addressof(buf)
        off = 0
        rows = []
        procs = threads = handles = 0
        seen = set()
        while True:
            e = _SysProc.from_address(base + off)
            pid = e.UniqueProcessId or 0
            procs += 1
            threads += e.NumberOfThreads
            handles += e.HandleCount
            if pid:
                seen.add(pid)
                if e.ImageName.Buffer and e.ImageName.Length:
                    name = ctypes.wstring_at(e.ImageName.Buffer,
                                             e.ImageName.Length // 2)
                else:
                    name = "pid %d" % pid
                cpu_100ns = e.UserTime + e.KernelTime
                prev = self._prev.get(pid)
                self._prev[pid] = (cpu_100ns, now)
                cpu = 0.0
                if prev:
                    dt = now - prev[1]
                    if dt > 0.05:
                        # 100-ns-Ticks -> Prozent einer *ganzen* CPU
                        cpu = (cpu_100ns - prev[0]) / 1e7 / dt / self.ncores * 100.0
                rows.append({"pid": pid, "name": name,
                             "cpu": max(cpu, 0.0),
                             "rss": e.WorkingSetPrivateSize,
                             "threads": e.NumberOfThreads})
            if not e.NextEntryOffset:
                break
            off += e.NextEntryOffset

        for pid in [p for p in self._prev if p not in seen]:
            del self._prev[pid]
        return rows, {"processes": procs, "threads": threads, "handles": handles}
