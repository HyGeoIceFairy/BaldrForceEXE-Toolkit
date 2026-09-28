"""进程实例枚举和精确进程内存读写 Adapter。"""

from __future__ import annotations

import os
from collections.abc import Iterable
from ctypes import POINTER, Structure, byref, c_size_t, c_ubyte, c_void_p, sizeof, wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = c_void_p(-1).value
STILL_ACTIVE = 259
MAX_PATH = 260
IMAGE_FILE_MACHINE_UNKNOWN = 0x0000
IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_FILE_MACHINE_ARM64 = 0xAA64


class ProcessMemoryError(RuntimeError):
    """进程枚举、实例验证或精确内存读写失败。"""


@dataclass(frozen=True)
class ProcessInstance:
    """不能只由 PID 代替的进程实例身份。"""

    pid: int
    creation_time_100ns: int
    executable_path: Path
    architecture: str
    image_base: int

    @property
    def instance_key(self) -> tuple[int, int]:
        return self.pid, self.creation_time_100ns


class ProcessMemory(Protocol):
    """一个绑定到特定实例、具有固定权限的进程内存句柄。"""

    @property
    def instance(self) -> ProcessInstance: ...

    @property
    def writable(self) -> bool: ...

    def read(self, address: int, size: int) -> bytes: ...

    def write(self, address: int, data: bytes) -> None: ...

    def is_alive(self) -> bool: ...

    def close(self) -> None: ...


class ProcessMemoryAdapter(Protocol):
    """Windows Adapter 与确定性测试 Adapter 的公共 Seam。"""

    def list_processes(self, executable_name: str) -> tuple[ProcessInstance, ...]: ...

    def open_process(
        self,
        instance: ProcessInstance,
        *,
        writable: bool = False,
    ) -> ProcessMemory: ...


class PROCESSENTRY32W(Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", POINTER(c_ubyte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


def normalized_executable_path(path: str | Path) -> str:
    """生成只用于身份比较的规范化、不区分大小写路径。"""

    return os.path.normcase(str(Path(path).resolve(strict=False)))


class WindowsProcessMemoryAdapter:
    """基于最小 Win32 权限的进程内存 Adapter。"""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProcessMemoryError("Windows 进程内存 Adapter 只能在 Windows 上使用")
        import ctypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_functions()

    def list_processes(self, executable_name: str) -> tuple[ProcessInstance, ...]:
        wanted = executable_name.casefold()
        snapshot = self._snapshot(TH32CS_SNAPPROCESS, 0)
        found: list[ProcessInstance] = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = sizeof(entry)
            more = bool(self._kernel32.Process32FirstW(snapshot, byref(entry)))
            while more:
                if str(entry.szExeFile).casefold() == wanted:
                    instance = self._inspect_process(int(entry.th32ProcessID))
                    if instance is not None:
                        found.append(instance)
                more = bool(self._kernel32.Process32NextW(snapshot, byref(entry)))
        finally:
            self._kernel32.CloseHandle(snapshot)
        return tuple(sorted(found, key=lambda item: item.instance_key))

    def open_process(
        self,
        instance: ProcessInstance,
        *,
        writable: bool = False,
    ) -> ProcessMemory:
        access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_OPERATION | PROCESS_VM_WRITE
        handle = self._kernel32.OpenProcess(access, False, instance.pid)
        if not handle:
            self._raise_last_error(f"无法打开进程 {instance.pid}")
        try:
            actual_creation = self._creation_time(handle)
            if actual_creation != instance.creation_time_100ns:
                raise ProcessMemoryError("PID 已被另一个进程实例复用")
        except Exception:
            self._kernel32.CloseHandle(handle)
            raise
        return _WindowsProcessMemory(self._kernel32, handle, instance, writable)

    def _configure_functions(self) -> None:
        functions = self._kernel32
        functions.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        functions.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        functions.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            POINTER(PROCESSENTRY32W),
        ]
        functions.Process32FirstW.restype = wintypes.BOOL
        functions.Process32NextW.argtypes = [
            wintypes.HANDLE,
            POINTER(PROCESSENTRY32W),
        ]
        functions.Process32NextW.restype = wintypes.BOOL
        functions.Module32FirstW.argtypes = [
            wintypes.HANDLE,
            POINTER(MODULEENTRY32W),
        ]
        functions.Module32FirstW.restype = wintypes.BOOL
        functions.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        functions.OpenProcess.restype = wintypes.HANDLE
        functions.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            POINTER(wintypes.DWORD),
        ]
        functions.QueryFullProcessImageNameW.restype = wintypes.BOOL
        functions.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            POINTER(wintypes.FILETIME),
            POINTER(wintypes.FILETIME),
            POINTER(wintypes.FILETIME),
            POINTER(wintypes.FILETIME),
        ]
        functions.GetProcessTimes.restype = wintypes.BOOL
        is_wow64_process2 = getattr(functions, "IsWow64Process2", None)
        if is_wow64_process2 is not None:
            is_wow64_process2.argtypes = [
                wintypes.HANDLE,
                POINTER(wintypes.USHORT),
                POINTER(wintypes.USHORT),
            ]
            is_wow64_process2.restype = wintypes.BOOL
        functions.IsWow64Process.argtypes = [
            wintypes.HANDLE,
            POINTER(wintypes.BOOL),
        ]
        functions.IsWow64Process.restype = wintypes.BOOL
        functions.ReadProcessMemory.argtypes = [
            wintypes.HANDLE,
            c_void_p,
            c_void_p,
            c_size_t,
            POINTER(c_size_t),
        ]
        functions.ReadProcessMemory.restype = wintypes.BOOL
        functions.WriteProcessMemory.argtypes = [
            wintypes.HANDLE,
            c_void_p,
            c_void_p,
            c_size_t,
            POINTER(c_size_t),
        ]
        functions.WriteProcessMemory.restype = wintypes.BOOL
        functions.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            POINTER(wintypes.DWORD),
        ]
        functions.GetExitCodeProcess.restype = wintypes.BOOL
        functions.CloseHandle.argtypes = [wintypes.HANDLE]
        functions.CloseHandle.restype = wintypes.BOOL

    def _snapshot(self, flags: int, pid: int) -> int:
        handle = self._kernel32.CreateToolhelp32Snapshot(flags, pid)
        if handle == INVALID_HANDLE_VALUE:
            self._raise_last_error("无法创建进程快照")
        return int(handle)

    def _inspect_process(self, pid: int) -> ProcessInstance | None:
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            path = self._process_path(handle)
            creation_time = self._creation_time(handle)
            architecture = self._architecture(handle)
            image_base = self._image_base(pid)
            return ProcessInstance(pid, creation_time, path, architecture, image_base)
        except ProcessMemoryError:
            return None
        finally:
            self._kernel32.CloseHandle(handle)

    def _process_path(self, handle: int) -> Path:
        capacity = wintypes.DWORD(32768)
        buffer = self._ctypes.create_unicode_buffer(capacity.value)
        if not self._kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            byref(capacity),
        ):
            self._raise_last_error("无法读取进程映像路径")
        return Path(buffer.value).resolve(strict=False)

    def _creation_time(self, handle: int) -> int:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not self._kernel32.GetProcessTimes(
            handle,
            byref(creation),
            byref(exit_time),
            byref(kernel),
            byref(user),
        ):
            self._raise_last_error("无法读取进程实例创建时间")
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)

    def _architecture(self, handle: int) -> str:
        is_wow64_process2 = getattr(self._kernel32, "IsWow64Process2", None)
        if is_wow64_process2 is not None:
            process_machine = wintypes.USHORT()
            native_machine = wintypes.USHORT()
            if not is_wow64_process2(
                handle,
                byref(process_machine),
                byref(native_machine),
            ):
                self._raise_last_error("无法读取目标进程架构")
            machine = (
                int(native_machine.value)
                if process_machine.value == IMAGE_FILE_MACHINE_UNKNOWN
                else int(process_machine.value)
            )
            return _architecture_name(machine)
        is_wow64 = wintypes.BOOL()
        if not self._kernel32.IsWow64Process(handle, byref(is_wow64)):
            self._raise_last_error("无法读取目标进程架构")
        if is_wow64.value:
            return "x86"
        return "x64" if sizeof(c_void_p) == 8 else "x86"

    def _image_base(self, pid: int) -> int:
        snapshot = self._snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = sizeof(entry)
            if not self._kernel32.Module32FirstW(snapshot, byref(entry)):
                self._raise_last_error("无法读取进程主模块")
            address = self._ctypes.cast(entry.modBaseAddr, c_void_p).value
            if address is None or address <= 0:
                raise ProcessMemoryError("进程主模块基址无效")
            return address
        finally:
            self._kernel32.CloseHandle(snapshot)

    def _raise_last_error(self, message: str) -> None:
        error = self._ctypes.get_last_error()
        raise ProcessMemoryError(f"{message}（Win32 错误 {error}）")


class _WindowsProcessMemory:
    def __init__(
        self,
        kernel32: Any,
        handle: int,
        instance: ProcessInstance,
        writable: bool,
    ) -> None:
        self._kernel32 = kernel32
        self._handle = handle
        self._instance = instance
        self._writable = writable

    @property
    def instance(self) -> ProcessInstance:
        return self._instance

    @property
    def writable(self) -> bool:
        return self._writable

    def read(self, address: int, size: int) -> bytes:
        if address <= 0 or size <= 0:
            raise ProcessMemoryError("读取地址和长度必须为正数")
        self._require_open()
        buffer = (c_ubyte * size)()
        transferred = c_size_t()
        succeeded = self._kernel32.ReadProcessMemory(
            self._handle,
            c_void_p(address),
            byref(buffer),
            size,
            byref(transferred),
        )
        if not succeeded or transferred.value != size:
            raise ProcessMemoryError(
                f"进程内存短读：期望 {size} 字节，实际 {transferred.value} 字节"
            )
        return bytes(buffer)

    def write(self, address: int, data: bytes) -> None:
        if not self._writable:
            raise ProcessMemoryError("当前句柄是只读连接")
        if address <= 0 or not data:
            raise ProcessMemoryError("写入地址必须为正数且数据不能为空")
        self._require_open()
        buffer = (c_ubyte * len(data)).from_buffer_copy(data)
        transferred = c_size_t()
        succeeded = self._kernel32.WriteProcessMemory(
            self._handle,
            c_void_p(address),
            byref(buffer),
            len(data),
            byref(transferred),
        )
        if not succeeded or transferred.value != len(data):
            raise ProcessMemoryError(
                f"进程内存短写：期望 {len(data)} 字节，实际 {transferred.value} 字节"
            )

    def is_alive(self) -> bool:
        if not self._handle:
            return False
        exit_code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(
            self._handle,
            byref(exit_code),
        ):
            return False
        return int(exit_code.value) == STILL_ACTIVE

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = 0

    def _require_open(self) -> None:
        if not self._handle:
            raise ProcessMemoryError("进程内存句柄已关闭")
        if not self.is_alive():
            raise ProcessMemoryError("目标进程实例已退出")


def _architecture_name(machine: int) -> str:
    names = {
        IMAGE_FILE_MACHINE_I386: "x86",
        IMAGE_FILE_MACHINE_AMD64: "x64",
        IMAGE_FILE_MACHINE_ARM64: "arm64",
    }
    return names.get(machine, f"unknown-{machine:04X}")


@dataclass
class _FakeProcessState:
    instance: ProcessInstance
    memory: bytearray
    alive: bool = True
    readable: bool = True
    writable: bool = True
    maximum_transfer: int | None = None


class FakeProcessMemoryAdapter:
    """支持退出、PID 复用、权限失败和短读写的确定性测试 Adapter。"""

    def __init__(self) -> None:
        self._states: dict[tuple[int, int], _FakeProcessState] = {}
        self._next_pid = 1000
        self._next_creation_time = 1

    def start_process(
        self,
        executable_path: str | Path,
        memory: bytes,
        *,
        architecture: str = "x86",
        image_base: int = 0x400000,
        pid: int | None = None,
        readable: bool = True,
        writable: bool = True,
        maximum_transfer: int | None = None,
    ) -> ProcessInstance:
        process_id = self._next_pid if pid is None else pid
        self._next_pid = max(self._next_pid + 1, process_id + 1)
        creation_time = self._next_creation_time
        self._next_creation_time += 1
        instance = ProcessInstance(
            process_id,
            creation_time,
            Path(executable_path).resolve(strict=False),
            architecture,
            image_base,
        )
        self._states[instance.instance_key] = _FakeProcessState(
            instance,
            bytearray(memory),
            readable=readable,
            writable=writable,
            maximum_transfer=maximum_transfer,
        )
        return instance

    def terminate(self, instance: ProcessInstance) -> None:
        self._state(instance).alive = False

    def list_processes(self, executable_name: str) -> tuple[ProcessInstance, ...]:
        wanted = executable_name.casefold()
        return tuple(
            state.instance
            for state in self._states.values()
            if state.alive and state.instance.executable_path.name.casefold() == wanted
        )

    def open_process(
        self,
        instance: ProcessInstance,
        *,
        writable: bool = False,
    ) -> ProcessMemory:
        state = self._state(instance)
        if not state.alive:
            raise ProcessMemoryError("目标进程实例已退出")
        if not state.readable or (writable and not state.writable):
            raise ProcessMemoryError("测试进程拒绝所请求的内存权限")
        return _FakeProcessMemory(state, writable)

    def _state(self, instance: ProcessInstance) -> _FakeProcessState:
        state = self._states.get(instance.instance_key)
        if state is None or state.instance != instance:
            raise ProcessMemoryError("PID 已被另一个进程实例复用")
        return state


class _FakeProcessMemory:
    def __init__(self, state: _FakeProcessState, writable: bool) -> None:
        self._state = state
        self._writable = writable
        self._closed = False

    @property
    def instance(self) -> ProcessInstance:
        return self._state.instance

    @property
    def writable(self) -> bool:
        return self._writable

    def read(self, address: int, size: int) -> bytes:
        self._require_available()
        if address <= 0 or size <= 0:
            raise ProcessMemoryError("读取地址和长度必须为正数")
        offset = address - self._state.instance.image_base
        actual_size = self._transfer_size(size)
        if offset < 0 or offset + actual_size > len(self._state.memory):
            raise ProcessMemoryError("测试进程内存读取越界")
        if actual_size != size:
            raise ProcessMemoryError(
                f"进程内存短读：期望 {size} 字节，实际 {actual_size} 字节"
            )
        return bytes(self._state.memory[offset : offset + size])

    def write(self, address: int, data: bytes) -> None:
        self._require_available()
        if not self._writable:
            raise ProcessMemoryError("当前句柄是只读连接")
        if address <= 0 or not data:
            raise ProcessMemoryError("写入地址必须为正数且数据不能为空")
        offset = address - self._state.instance.image_base
        actual_size = self._transfer_size(len(data))
        if offset < 0 or offset + actual_size > len(self._state.memory):
            raise ProcessMemoryError("测试进程内存写入越界")
        if actual_size != len(data):
            raise ProcessMemoryError(
                f"进程内存短写：期望 {len(data)} 字节，实际 {actual_size} 字节"
            )
        self._state.memory[offset : offset + len(data)] = data

    def is_alive(self) -> bool:
        return not self._closed and self._state.alive

    def close(self) -> None:
        self._closed = True

    def _require_available(self) -> None:
        if self._closed:
            raise ProcessMemoryError("进程内存句柄已关闭")
        if not self._state.alive:
            raise ProcessMemoryError("目标进程实例已退出")

    def _transfer_size(self, requested: int) -> int:
        maximum = self._state.maximum_transfer
        return requested if maximum is None else min(requested, maximum)


def close_all(handles: Iterable[ProcessMemory]) -> None:
    """尽力关闭一组只读或读写句柄。"""

    for handle in handles:
        handle.close()
