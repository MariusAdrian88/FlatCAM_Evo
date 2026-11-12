# ##########################################################
# FlatCAM EVO: 2D Post-processing for Manufacturing        #
# Free memory of the computer (Windows, Linux, macOS)      #
# MIT Licence                                              #
# ##########################################################

"""
The free physical memory of the computer, without a third-party package.

``free_memory()`` returns a number of bytes, or None if the operating system did not give the value. A caller must
treat None as "not known" and must not block the user because of it.
"""

import re
import subprocess
import sys
from typing import Optional


def parse_meminfo(text: str) -> Optional[int]:
    """
    Free memory from the text of ``/proc/meminfo`` (Linux).

    ``MemAvailable`` is the memory that a new process can get without swapping. Kernels older than 3.14 do not have
    it: ``MemFree`` is used then.

    :param text: content of /proc/meminfo
    :return:     bytes, or None if no value was found
    """
    for key in ('MemAvailable', 'MemFree'):
        match = re.search(r'^%s:\s+(\d+)\s*kB' % key, text, re.MULTILINE)
        if match:
            return int(match.group(1)) * 1024
    return None


def parse_vm_stat(text: str) -> Optional[int]:
    """
    Free memory from the output of the ``vm_stat`` command (macOS): the free, the inactive, and the speculative
    pages, times the page size (purgeable pages are already in the active and inactive counts). macOS gives these
    pages to a process that asks for memory.

    :param text: output of vm_stat
    :return:     bytes, or None if the page size or the free pages were not found
    """
    size = re.search(r'page size of (\d+) bytes', text)
    free = re.search(r'^Pages free:\s+(\d+)', text, re.MULTILINE)
    if not size or not free:
        return None
    pages = int(free.group(1))
    for key in ('Pages inactive', 'Pages speculative'):
        match = re.search(r'^%s:\s+(\d+)' % key, text, re.MULTILINE)
        if match:
            pages += int(match.group(1))
    return pages * int(size.group(1))


def _free_memory_windows() -> Optional[int]:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ('dwLength', ctypes.c_ulong),
            ('dwMemoryLoad', ctypes.c_ulong),
            ('ullTotalPhys', ctypes.c_ulonglong),
            ('ullAvailPhys', ctypes.c_ulonglong),
            ('ullTotalPageFile', ctypes.c_ulonglong),
            ('ullAvailPageFile', ctypes.c_ulonglong),
            ('ullTotalVirtual', ctypes.c_ulonglong),
            ('ullAvailVirtual', ctypes.c_ulonglong),
            ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)


def free_memory() -> Optional[int]:
    """
    The free physical memory of the computer.

    :return: bytes, or None if the operating system did not give the value
    """
    try:
        if sys.platform == 'win32':
            return _free_memory_windows()
        if sys.platform == 'darwin':
            out = subprocess.run(['vm_stat'], capture_output=True, text=True, timeout=3)
            return parse_vm_stat(out.stdout)
        # Linux and the systems that have a Linux-like /proc
        with open('/proc/meminfo', 'r') as f:
            return parse_meminfo(f.read())
    except Exception:
        return None


def format_size(size: float) -> str:
    """A number of bytes as text in MB or GB (1024-based), for a message to the user."""
    gb = size / (1024.0 ** 3)
    if gb >= 1.0:
        return '%.1f GB' % gb
    return '%.0f MB' % (size / (1024.0 ** 2))
