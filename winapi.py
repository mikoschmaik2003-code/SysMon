"""Direkte Windows-/NVIDIA-Schnittstellen - ohne Subprozesse, damit nichts haengt.

* CoreClocks - echter Takt pro logischem Kern ueber den PDH-Zaehler
  "% Processor Performance" (dieselbe Quelle, aus der auch der Task-Manager liest)
* GpuProcs   - GPU-Auslastung je Prozess ueber "\\GPU Engine(*)\\Utilization Percentage"
* GpuProcMem - dedizierter VRAM je Prozess ueber "\\GPU Process Memory(*)\\Dedicated Usage"
* Adapters   - IP-Konfiguration aller Netzwerkadapter (GetAdaptersAddresses)
* Pinger     - ICMP-Ping ohne ping.exe (IcmpSendEcho)
* Nvml       - GPU-Werte direkt aus nvml.dll statt via "nvidia-smi" (5 ms statt ~150 ms)
"""

import collections
import ctypes
import socket
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
        self.driver = ""
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
            if self.lib.nvmlSystemGetDriverVersion(buf, 96) == 0:
                self.driver = buf.value.decode("utf-8", "replace")
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


# ------------------------------------------- Netzwerkadapter (iphlpapi.dll)
class _SockAddr(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class _AddrNode(ctypes.Structure):
    """Gemeinsamer Kopf von DNS- und Gateway-Eintraegen."""


_AddrNode._fields_ = [("Length", ctypes.c_ulong), ("Flags", ctypes.c_ulong),
                      ("Next", ctypes.POINTER(_AddrNode)), ("Address", _SockAddr)]


class _Unicast(ctypes.Structure):
    pass


_Unicast._fields_ = [("Length", ctypes.c_ulong), ("Flags", ctypes.c_ulong),
                     ("Next", ctypes.POINTER(_Unicast)), ("Address", _SockAddr),
                     ("PrefixOrigin", ctypes.c_int), ("SuffixOrigin", ctypes.c_int),
                     ("DadState", ctypes.c_int), ("ValidLifetime", ctypes.c_ulong),
                     ("PreferredLifetime", ctypes.c_ulong),
                     ("LeaseLifetime", ctypes.c_ulong),
                     ("OnLinkPrefixLength", ctypes.c_ubyte)]


class _Adapter(ctypes.Structure):
    """IP_ADAPTER_ADDRESSES_LH bis einschliesslich Dhcpv4Server."""


_Adapter._fields_ = [
    ("Length", ctypes.c_ulong), ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_Adapter)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_Unicast)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.POINTER(_AddrNode)),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", ctypes.c_ulong),
    ("ZoneIndices", ctypes.c_ulong * 16),
    ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", ctypes.c_ulonglong),
    ("ReceiveLinkSpeed", ctypes.c_ulonglong),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.POINTER(_AddrNode)),
    ("Ipv4Metric", ctypes.c_ulong),
    ("Ipv6Metric", ctypes.c_ulong),
    ("Luid", ctypes.c_ulonglong),
    ("Dhcpv4Server", _SockAddr),
]


def _sockaddr_str(sa):
    """SOCKET_ADDRESS -> (Familie 4/6, Text) oder (0, "")."""
    if not sa.lpSockaddr:
        return 0, ""
    fam = ctypes.c_ushort.from_address(sa.lpSockaddr).value
    if fam == 2:
        raw = ctypes.string_at(sa.lpSockaddr + 4, 4)
        return 4, socket.inet_ntop(socket.AF_INET, raw)
    if fam == 23:
        raw = ctypes.string_at(sa.lpSockaddr + 8, 16)
        return 6, socket.inet_ntop(socket.AF_INET6, raw)
    return 0, ""


def _walk(node):
    while node:
        yield node.contents
        node = node.contents.Next


def _mask(prefix):
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


class Adapters:
    """Alles aus "ipconfig /all" in ~1 ms statt ~6 s ueber PowerShell."""

    GAA_FLAGS = 0x0002 | 0x0004 | 0x0080   # ohne Any-/Multicast, mit Gateways
    DHCP_ENABLED = 0x4
    IF_LOOPBACK = 24

    def __init__(self):
        self._size = 32 * 1024
        try:
            self.lib = ctypes.WinDLL("iphlpapi.dll")
            self.ok = True
        except Exception:
            self.ok = False

    def read(self):
        if not self.ok:
            return []
        rc = -1
        for _ in range(4):
            buf = ctypes.create_string_buffer(self._size)
            size = ctypes.c_ulong(self._size)
            rc = self.lib.GetAdaptersAddresses(0, self.GAA_FLAGS, None, buf,
                                               ctypes.byref(size))
            if rc != 111:                  # ERROR_BUFFER_OVERFLOW
                break
            self._size = size.value + 4096
        if rc != 0:
            return []

        out = []
        for a in _walk(ctypes.cast(buf, ctypes.POINTER(_Adapter))):
            if a.IfType == self.IF_LOOPBACK:
                continue
            v4, v6 = [], []
            for u in _walk(a.FirstUnicastAddress):
                fam, ip = _sockaddr_str(u.Address)
                if fam == 4:
                    v4.append((ip, u.OnLinkPrefixLength, _mask(u.OnLinkPrefixLength)))
                elif fam == 6:
                    v6.append((ip, u.OnLinkPrefixLength))
            gw = [_sockaddr_str(g.Address)[1] for g in _walk(a.FirstGatewayAddress)]
            dns = [_sockaddr_str(d.Address) for d in _walk(a.FirstDnsServerAddress)]
            mac = "-".join("%02X" % b for b in
                           a.PhysicalAddress[: a.PhysicalAddressLength])
            out.append({
                "name": a.FriendlyName or "",
                "desc": a.Description or "",
                "up": a.OperStatus == 1,
                "mac": mac,
                "mtu": a.Mtu,
                "speed": a.ReceiveLinkSpeed if a.ReceiveLinkSpeed < 1 << 62 else 0,
                "dhcp": bool(a.Flags & self.DHCP_ENABLED),
                "dhcp_server": _sockaddr_str(a.Dhcpv4Server)[1],
                "suffix": a.DnsSuffix or "",
                "v4": v4, "v6": v6,
                "gw": sorted((g for g in gw if g), key=lambda g: ":" in g),
                "dns": [ip for fam, ip in dns if fam == 4] +
                       [ip for fam, ip in dns if fam == 6],
                "metric": a.Ipv4Metric,
            })
        return out


# ----------------------------------------------------------- Ping (ICMP)
class _IcmpReply(ctypes.Structure):
    _fields_ = [("Address", ctypes.c_ulong), ("Status", ctypes.c_ulong),
                ("RoundTripTime", ctypes.c_ulong), ("DataSize", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                ("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte),
                ("OFlags", ctypes.c_ubyte), ("OptionsSize", ctypes.c_ubyte),
                ("OptionsData", ctypes.c_void_p)]


class Pinger:
    """ICMP-Echo direkt ueber IcmpSendEcho - kein ping.exe, kein Konsolenfenster."""

    def __init__(self):
        self.ok = False
        try:
            self.lib = ctypes.WinDLL("iphlpapi.dll")
            self.lib.IcmpCreateFile.restype = wintypes.HANDLE
            self.lib.IcmpSendEcho.argtypes = [
                wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ushort,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
            self.h = self.lib.IcmpCreateFile()
            self.ok = bool(self.h) and self.h != wintypes.HANDLE(-1).value
        except Exception:
            self.ok = False
        self._data = ctypes.create_string_buffer(b"sysmon-ping", 32)
        self._reply = ctypes.create_string_buffer(ctypes.sizeof(_IcmpReply) + 64)

    def ping(self, ip, timeout_ms=1000):
        """Antwortzeit in ms oder None (Timeout / nicht erreichbar)."""
        if not self.ok:
            return None
        try:
            addr = int.from_bytes(socket.inet_aton(ip), "little")
        except OSError:
            return None
        n = self.lib.IcmpSendEcho(self.h, addr, self._data, 32, None,
                                  self._reply, len(self._reply), timeout_ms)
        if not n:
            return None
        r = _IcmpReply.from_buffer(self._reply)
        return float(r.RoundTripTime) if r.Status == 0 else None


# ------------------------------------------------- Monitor unter dem Fenster
class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def monitor_rect(hwnd):
    """(x, y, w, h) des Bildschirms, auf dem das Fenster ueberwiegend liegt."""
    try:
        u = ctypes.windll.user32
        u.MonitorFromWindow.restype = wintypes.HANDLE
        u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        mon = u.MonitorFromWindow(hwnd, 2)          # MONITOR_DEFAULTTONEAREST
        mi = _MonitorInfo()
        mi.cbSize = ctypes.sizeof(_MonitorInfo)
        if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return None
        r = mi.rcMonitor
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return None


# ------------------------------------------------- P-/E-Kerne (Intel Hybrid)
def core_types(ncores):
    """Liste "P"/"E" je logischem Prozessor - oder None, wenn alle Kerne gleich sind.

    GetSystemCpuSetInformation liefert je logischem Prozessor eine
    EfficiencyClass: bei Hybrid-CPUs haben die Performance-Kerne die hoehere
    Klasse, die Effizienz-Kerne 0. Auf AMD/aelteren Intel sind alle gleich.
    """
    try:
        k = ctypes.windll.kernel32
        need = wintypes.ULONG(0)
        k.GetSystemCpuSetInformation(None, 0, ctypes.byref(need), None, 0)
        if not need.value:
            return None
        buf = ctypes.create_string_buffer(need.value)
        if not k.GetSystemCpuSetInformation(buf, need.value, ctypes.byref(need),
                                            None, 0):
            return None
        raw, off, cpus = buf.raw, 0, []
        while off + 19 <= need.value:
            size = int.from_bytes(raw[off:off + 4], "little")
            if not size:
                break
            if int.from_bytes(raw[off + 4:off + 8], "little") == 0:  # CpuSetInformation
                group = int.from_bytes(raw[off + 12:off + 14], "little")
                cpus.append(((group, raw[off + 14]), raw[off + 18]))
            off += size
        classes = {c for _, c in cpus}
        if len(classes) < 2 or len(cpus) < ncores:
            return None
        top = max(classes)
        return ["P" if c == top else "E" for _, c in sorted(cpus)][:ncores]
    except Exception:
        return None
