"""Cross-platform ownership and termination of one Git subprocess tree."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import time

from clipvault.backup import cancellation


class ProcessTreeSetupError(RuntimeError):
    """A Git process tree could not be created with fail-closed ownership."""


if os.name == "nt":  # pragma: no cover - opposite branch runs on other hosts
    import ctypes
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _TH32CS_SNAPTHREAD = 0x00000004
    _THREAD_SUSPEND_RESUME = 0x0002
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _INVALID_DWORD = 0xFFFFFFFF
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("per_process_user_time_limit", ctypes.c_longlong),
            ("per_job_user_time_limit", ctypes.c_longlong),
            ("limit_flags", wintypes.DWORD),
            ("minimum_working_set_size", ctypes.c_size_t),
            ("maximum_working_set_size", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("read_operation_count", ctypes.c_ulonglong),
            ("write_operation_count", ctypes.c_ulonglong),
            ("other_operation_count", ctypes.c_ulonglong),
            ("read_transfer_count", ctypes.c_ulonglong),
            ("write_transfer_count", ctypes.c_ulonglong),
            ("other_transfer_count", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("basic_limit_information", _BasicLimitInformation),
            ("io_info", _IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory_used", ctypes.c_size_t),
            ("peak_job_memory_used", ctypes.c_size_t),
        ]

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("total_user_time", ctypes.c_longlong),
            ("total_kernel_time", ctypes.c_longlong),
            ("this_period_total_user_time", ctypes.c_longlong),
            ("this_period_total_kernel_time", ctypes.c_longlong),
            ("total_page_fault_count", wintypes.DWORD),
            ("total_processes", wintypes.DWORD),
            ("active_processes", wintypes.DWORD),
            ("total_terminated_processes", wintypes.DWORD),
        ]

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage_count", wintypes.DWORD),
            ("thread_id", wintypes.DWORD),
            ("owner_process_id", wintypes.DWORD),
            ("base_priority", wintypes.LONG),
            ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    class _WindowsJob:
        """Assign a suspended leader before it can create helper descendants."""

        def __init__(self) -> None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel32 = kernel32
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [
                wintypes.HANDLE,
                wintypes.HANDLE,
            ]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            kernel32.QueryInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.QueryInformationJobObject.restype = wintypes.BOOL
            kernel32.CreateToolhelp32Snapshot.argtypes = [
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Thread32First.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_ThreadEntry32),
            ]
            kernel32.Thread32First.restype = wintypes.BOOL
            kernel32.Thread32Next.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_ThreadEntry32),
            ]
            kernel32.Thread32Next.restype = wintypes.BOOL
            kernel32.OpenThread.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenThread.restype = wintypes.HANDLE
            kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
            kernel32.ResumeThread.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ProcessTreeSetupError("Git process job creation failed")
            self._handle = handle
            limits = _ExtendedLimitInformation()
            limits.basic_limit_information.limit_flags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                kernel32.CloseHandle(handle)
                self._handle = None
                raise ProcessTreeSetupError("Git process job setup failed")

        @property
        def popen_kwargs(self) -> dict[str, int]:
            return {
                "creationflags": (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                    | _CREATE_SUSPENDED
                )
            }

        def attach(self, process: subprocess.Popen) -> None:
            process_handle = self._kernel32.OpenProcess(
                _PROCESS_TERMINATE | _PROCESS_SET_QUOTA,
                False,
                process.pid,
            )
            if not process_handle:
                raise ProcessTreeSetupError("Git process job assignment failed")
            try:
                if self._handle is None or not self._kernel32.AssignProcessToJobObject(
                    self._handle,
                    process_handle,
                ):
                    raise ProcessTreeSetupError("Git process job assignment failed")
            finally:
                self._kernel32.CloseHandle(process_handle)
            self._resume_process(process.pid)

        def _resume_process(self, process_id: int) -> None:
            snapshot = self._kernel32.CreateToolhelp32Snapshot(
                _TH32CS_SNAPTHREAD,
                0,
            )
            if snapshot == _INVALID_HANDLE_VALUE:
                raise ProcessTreeSetupError("Git process resume failed")
            resumed = False
            try:
                entry = _ThreadEntry32()
                entry.size = ctypes.sizeof(entry)
                present = self._kernel32.Thread32First(
                    snapshot,
                    ctypes.byref(entry),
                )
                while present:
                    if entry.owner_process_id == process_id:
                        thread = self._kernel32.OpenThread(
                            _THREAD_SUSPEND_RESUME,
                            False,
                            entry.thread_id,
                        )
                        if not thread:
                            raise ProcessTreeSetupError("Git process resume failed")
                        try:
                            if self._kernel32.ResumeThread(thread) == _INVALID_DWORD:
                                raise ProcessTreeSetupError(
                                    "Git process resume failed"
                                )
                            resumed = True
                        finally:
                            self._kernel32.CloseHandle(thread)
                    present = self._kernel32.Thread32Next(
                        snapshot,
                        ctypes.byref(entry),
                    )
            finally:
                self._kernel32.CloseHandle(snapshot)
            if not resumed:
                raise ProcessTreeSetupError("Git process resume failed")

        def terminate(self, process: subprocess.Popen, grace_s: float) -> None:
            # Give Git a best-effort chance to remove ref/index lock files before
            # the Job enforces a complete tree kill. CREATE_NEW_PROCESS_GROUP is
            # required for CTRL_BREAK; GUI/no-console environments may reject it.
            if process.poll() is None:
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except (OSError, ValueError):
                    pass
                else:
                    try:
                        process.wait(timeout=grace_s)
                    except subprocess.TimeoutExpired:
                        pass
            if self._handle is not None and not self._kernel32.TerminateJobObject(
                self._handle,
                130,
            ):
                raise cancellation.BackupProcessTerminationError(
                    "Git process tree termination failed"
                )
            self._wait_empty()

        def _wait_empty(self, timeout_s: float = 2.0) -> None:
            deadline = time.monotonic() + timeout_s
            while True:
                accounting = _BasicAccountingInformation()
                if self._handle is None or not self._kernel32.QueryInformationJobObject(
                    self._handle,
                    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                    ctypes.byref(accounting),
                    ctypes.sizeof(accounting),
                    None,
                ):
                    raise cancellation.BackupProcessTerminationError(
                        "Git process job inspection failed"
                    )
                if accounting.active_processes == 0:
                    return
                if time.monotonic() >= deadline:
                    raise cancellation.BackupProcessTerminationError(
                        "Git process tree termination failed"
                    )
                time.sleep(0.01)

        def close(self) -> None:
            handle = self._handle
            if handle is None:
                return
            error = None
            try:
                if not self._kernel32.TerminateJobObject(handle, 130):
                    error = cancellation.BackupProcessTerminationError(
                        "Git process tree termination failed"
                    )
                else:
                    self._wait_empty()
            except cancellation.BackupProcessTerminationError as exc:
                error = exc
            finally:
                self._handle = None
                if not self._kernel32.CloseHandle(handle) and error is None:
                    error = cancellation.BackupProcessTerminationError(
                        "Git process job close failed"
                    )
            if error is not None:
                raise error


class ProcessTreeController:
    """Own every descendant from process creation until verified completion."""

    def __init__(self, *, grace_s: float) -> None:
        self.grace_s = grace_s
        self._process_group: int | None = None
        self._windows_job = _WindowsJob() if os.name == "nt" else None

    @property
    def popen_kwargs(self) -> dict[str, object]:
        if self._windows_job is not None:
            return dict(self._windows_job.popen_kwargs)
        return {"start_new_session": True}

    def attach(self, process: subprocess.Popen) -> None:
        self._process_group = process.pid
        if self._windows_job is not None:
            self._windows_job.attach(process)

    def terminate(self, process: subprocess.Popen) -> None:
        if self._windows_job is not None:
            self._windows_job.terminate(process, self.grace_s)
            return
        process_group = self._process_group
        if process_group is None:
            try:
                process.kill()
            except ProcessLookupError:
                return
            except OSError:
                raise cancellation.BackupProcessTerminationError(
                    "Git process termination failed"
                ) from None
            return
        if not _signal_posix_group(process_group, signal.SIGTERM):
            self._process_group = None
            return
        end = time.monotonic() + self.grace_s
        while time.monotonic() < end:
            if not _posix_group_exists(process_group):
                self._process_group = None
                return
            time.sleep(min(0.01, max(0.0, end - time.monotonic())))
        _signal_posix_group(process_group, signal.SIGKILL)
        self._process_group = None

    def close(self) -> None:
        if self._windows_job is not None:
            self._windows_job.close()
            return
        process_group = self._process_group
        if process_group is None:
            return
        # Successful SIGKILL proves every non-escaped member can no longer run;
        # killpg(0) cannot distinguish live processes from unreaped zombies.
        # Keep the group id on failure so a second close attempt is not a no-op.
        _signal_posix_group(process_group, signal.SIGKILL)
        self._process_group = None


def _signal_posix_group(process_group: int, sig: int) -> bool:
    """Signal one owned POSIX process group; only ESRCH proves absence."""

    try:
        os.killpg(process_group, sig)
    except ProcessLookupError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        raise cancellation.BackupProcessTerminationError(
            "Git process group control failed"
        ) from None
    return True


def _posix_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        raise cancellation.BackupProcessTerminationError(
            "Git process group inspection failed"
        ) from None
    return True
