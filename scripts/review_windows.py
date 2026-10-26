"""Windows process ownership and Job Objects for the CCB supervisor.

Only imported on Windows. Named jobs keep review descendants together and kill
them if the supervisor exits, including an abrupt termination of its process.
"""
import ctypes
from ctypes import wintypes
import subprocess
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")


def api(library, name, restype, *arguments):
    function = getattr(library, name)
    function.restype = restype
    function.argtypes = arguments
    return function


close = api(k32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
open_process = api(k32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
get_times = api(k32, "GetProcessTimes", wintypes.BOOL, wintypes.HANDLE,
                *([ctypes.POINTER(wintypes.FILETIME)] * 4))
get_exit = api(k32, "GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
query_process = api(ntdll, "NtQueryInformationProcess", wintypes.LONG, wintypes.HANDLE,
                    wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))
resume_process = api(ntdll, "NtResumeProcess", wintypes.LONG, wintypes.HANDLE)
create_job = api(k32, "CreateJobObjectW", wintypes.HANDLE, ctypes.c_void_p, wintypes.LPCWSTR)
open_job = api(k32, "OpenJobObjectW", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
set_job = api(k32, "SetInformationJobObject", wintypes.BOOL, wintypes.HANDLE,
              ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
query_job = api(k32, "QueryInformationJobObject", wintypes.BOOL, wintypes.HANDLE,
                ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
assign_job = api(k32, "AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
kill_job = api(k32, "TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)


class UnicodeString(ctypes.Structure):
    _fields_ = [("length", wintypes.USHORT), ("maximum", wintypes.USHORT), ("buffer", ctypes.c_void_p)]


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("reads", "writes", "others", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [
        (name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]


def checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def identity(pid):
    handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        if ctypes.get_last_error() == 87:  # process no longer exists
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        code = wintypes.DWORD()
        checked(get_exit(handle, ctypes.byref(code)))
        if code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        checked(get_times(handle, *[ctypes.byref(value) for value in times]))
        buffer = ctypes.create_string_buffer(131072)
        size = wintypes.ULONG()
        status = query_process(handle, 60, buffer, len(buffer), ctypes.byref(size))
        if status < 0:
            raise OSError("Cannot query Windows process command line: " + hex(status & 0xffffffff))
        value = UnicodeString.from_buffer(buffer)
        start = ctypes.addressof(buffer)
        if not value.buffer or not start <= value.buffer <= start + len(buffer) - value.length:
            raise OSError("Invalid Windows process command-line buffer")
        command = ctypes.wstring_at(value.buffer, value.length // 2)
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return str(created) + " " + command
    finally:
        close(handle)


def job_name(nonce):
    if not nonce:
        raise OSError("Missing review job identity")
    return "Local\\magent-review-" + nonce


def existing_job(nonce, access):
    handle = open_job(access, False, job_name(nonce))
    if not handle and ctypes.get_last_error() != 2:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def active(handle):
    value = Accounting()
    checked(query_job(handle, 1, ctypes.byref(value), ctypes.sizeof(value), None))
    return value.active > 0


def group_alive(pgid, nonce=None):
    if not isinstance(pgid, int) or pgid <= 1:
        return False
    handle = existing_job(nonce, 4)  # JOB_OBJECT_QUERY
    if not handle:
        return identity(pgid) is not None
    try:
        return active(handle)
    finally:
        close(handle)


def terminate_group(pgid, nonce=None):
    handle = existing_job(nonce, 4 | 8)  # QUERY | TERMINATE
    if not handle:
        if identity(pgid) is not None:
            raise OSError("Live process has no verified Windows review job")
        return
    try:
        checked(kill_job(handle, 130))
        deadline = time.monotonic() + 5
        while active(handle):
            if time.monotonic() >= deadline:
                raise OSError("Windows review job did not terminate")
            time.sleep(0.02)
    finally:
        close(handle)


def spawn(command, nonce, **kwargs):
    handle = checked(create_job(None, job_name(nonce)))
    proc = None
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        checked(set_job(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        # Assign before the process can spawn children; there is no tree race.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 4
        proc = subprocess.Popen(command, creationflags=flags, **kwargs)
        checked(assign_job(handle, int(proc._handle)))
        if resume_process(int(proc._handle)) < 0:
            raise OSError("Could not resume the suspended review process")
        proc.review_job = handle
        return proc
    except BaseException:
        if proc is not None:
            proc.kill()
            proc.wait()
        close(handle)
        raise


def terminate(proc):
    handle = getattr(proc, "review_job", None)
    if handle:
        try:
            checked(kill_job(handle, 130))
            proc.wait(timeout=5)
        finally:
            close(handle)
            proc.review_job = None
