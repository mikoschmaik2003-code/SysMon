"""Anonym-Modus (Taste A): verdeckt alles, was den Rechner identifiziert.

Gedacht fuer Screenshots: Rechner- und Benutzername, MAC-Adressen, oeffentliche
IPv4-Adressen, alle IPv6-Adressen und das DNS-Suffix. Private IPv4-Adressen
(192.168.x.x usw.) bleiben stehen - die sagen nichts ueber den Anschluss aus.
"""

import ipaddress

ON = False


def host(name):
    return "MEIN-PC" if ON else name


def user(name):
    return "benutzer" if ON else name


def mac(m):
    return "XX-XX-XX-XX-XX-XX" if ON and m else m


def text(s):
    return "verdeckt" if ON and s else s


def ip(addr):
    if not ON:
        return addr
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if a.version == 6:
        # auch lokale IPv6 verraten etwas: fd..-Praefix des Netzes, MAC im fe80::
        return ":".join(addr.split(":")[:2]) + ":xxxx:xxxx:xxxx:xxxx:xxxx:xxxx"
    return "xxx.xxx.xxx.xxx" if a.is_global else addr
