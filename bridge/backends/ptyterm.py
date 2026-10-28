"""Interactive terminals for the in-game terminal window (/pty).

Windows: ConPTY (CreatePseudoConsole) through ctypes. Linux/macOS: the pty module.
No extra dependencies. A reader thread feeds the output into a vt.Screen; the mod
polls GET /pty/screen and sends keys with POST /pty/input. Terminals keep running
when the window is closed and can be reopened; they end with the shell (exit) or
on /pty close. They run as the bridge's user, like every other shell command.
"""
import codecs
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

from backends.vt import Screen

MAX_TERMINALS = 8


def shell_command(kind):
    kind = (kind or 'auto').lower()
    if os.name == 'nt':
        if kind in ('cmd',):
            return 'cmd.exe'
        if kind in ('pwsh',) and shutil.which('pwsh'):
            return 'pwsh.exe -NoLogo'
        if kind in ('bash', 'sh'):
            git_bash = shutil.which('bash')
            if git_bash:
                return f'"{git_bash}" -i'
        if kind == 'wsl' and shutil.which('wsl'):
            return 'wsl.exe'
        return 'powershell.exe -NoLogo'
    if kind in ('zsh', 'bash', 'sh', 'fish') and shutil.which(kind):
        return shutil.which(kind)
    return os.environ.get('SHELL') or '/bin/sh'


class _WinPty:
    """ConPTY: two pipes, a pseudo console and a process attached to it."""

    def __init__(self, command, cwd, cols, rows, env):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k32 = k32

        class COORD(ctypes.Structure):
            _fields_ = [('X', w.SHORT), ('Y', w.SHORT)]

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR), ('lpTitle', w.LPWSTR),
                        ('dwX', w.DWORD), ('dwY', w.DWORD), ('dwXSize', w.DWORD), ('dwYSize', w.DWORD),
                        ('dwXCountChars', w.DWORD), ('dwYCountChars', w.DWORD), ('dwFillAttribute', w.DWORD),
                        ('dwFlags', w.DWORD), ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                        ('lpReserved2', ctypes.c_void_p), ('hStdInput', w.HANDLE), ('hStdOutput', w.HANDLE),
                        ('hStdError', w.HANDLE)]

        class STARTUPINFOEXW(ctypes.Structure):
            _fields_ = [('StartupInfo', STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [('hProcess', w.HANDLE), ('hThread', w.HANDLE), ('dwProcessId', w.DWORD),
                        ('dwThreadId', w.DWORD)]

        self.COORD = COORD
        k32.CreatePseudoConsole.argtypes = [COORD, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        k32.CreatePseudoConsole.restype = ctypes.c_long
        k32.ResizePseudoConsole.argtypes = [w.HANDLE, COORD]
        k32.ResizePseudoConsole.restype = ctypes.c_long
        k32.ClosePseudoConsole.argtypes = [w.HANDLE]
        k32.ClosePseudoConsole.restype = None
        k32.CreatePipe.argtypes = [ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD]
        k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                                  ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k32.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                                       ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION)]
        k32.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        k32.WriteFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        k32.CloseHandle.argtypes = [w.HANDLE]
        k32.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k32.WaitForSingleObject.restype = w.DWORD

        in_read, in_write, out_read, out_write = w.HANDLE(), w.HANDLE(), w.HANDLE(), w.HANDLE()
        if not k32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0) or \
                not k32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0):
            raise OSError(ctypes.get_last_error(), 'CreatePipe failed')
        self.hpc = w.HANDLE()
        hr = k32.CreatePseudoConsole(COORD(cols, rows), in_read, out_write, 0, ctypes.byref(self.hpc))
        if hr != 0:
            raise OSError(hr, 'CreatePseudoConsole failed (Windows 10 1809+ needed)')
        size = ctypes.c_size_t()
        k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self.attrs = ctypes.create_string_buffer(size.value)
        if not k32.InitializeProcThreadAttributeList(self.attrs, 1, 0, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), 'InitializeProcThreadAttributeList failed')
        PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
        if not k32.UpdateProcThreadAttribute(self.attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, self.hpc,
                                             ctypes.sizeof(w.HANDLE), None, None):
            raise OSError(ctypes.get_last_error(), 'UpdateProcThreadAttribute failed')
        info = STARTUPINFOEXW()
        info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        # Without this a bridge with redirected stdio passes its own pipes to the shell
        # and the pseudo console stays empty.
        info.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES, all handles NULL
        info.lpAttributeList = ctypes.cast(self.attrs, ctypes.c_void_p)
        process = PROCESS_INFORMATION()
        block = ''.join(f'{k}={v}\0' for k, v in env.items()) + '\0'
        env_buffer = ctypes.create_unicode_buffer(block, len(block))
        EXTENDED_STARTUPINFO_PRESENT, CREATE_UNICODE_ENVIRONMENT = 0x00080000, 0x00000400
        cmdline = ctypes.create_unicode_buffer(command)
        if not k32.CreateProcessW(None, cmdline, None, None, False,
                                  EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                                  env_buffer, str(cwd), ctypes.byref(info), ctypes.byref(process)):
            raise OSError(ctypes.get_last_error(), f'CreateProcess failed: {command}')
        k32.CloseHandle(process.hThread)
        k32.CloseHandle(in_read)   # the console owns these ends now
        k32.CloseHandle(out_write)
        self.process, self.pid = process.hProcess, process.dwProcessId
        self.input, self.output = in_write, out_read
        self.closed = False

    def read(self):
        buf = self.ctypes.create_string_buffer(8192)
        n = self.w.DWORD()
        if not self.k32.ReadFile(self.output, buf, 8192, self.ctypes.byref(n), None) or n.value == 0:
            return b''
        return buf.raw[:n.value]

    def write(self, data):
        n = self.w.DWORD()
        self.k32.WriteFile(self.input, data, len(data), self.ctypes.byref(n), None)

    def resize(self, cols, rows):
        if self.hpc is not None:
            self.k32.ResizePseudoConsole(self.hpc, self.COORD(cols, rows))

    def alive(self):
        return self.k32.WaitForSingleObject(self.process, 0) != 0  # WAIT_OBJECT_0 = exited

    def finish(self):
        """After the shell exits: ConPTY keeps the output pipe open until the console is closed."""
        if self.hpc is not None:
            hpc, self.hpc = self.hpc, None
            self.k32.ClosePseudoConsole(hpc)  # flushes the last output; the reader drains it

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.alive():
            self.k32.TerminateProcess(self.process, 1)
        self.finish()
        self.k32.CloseHandle(self.input)
        self.k32.CloseHandle(self.process)
        self.k32.DeleteProcThreadAttributeList(self.attrs)


class _PosixPty:
    def __init__(self, command, cwd, cols, rows, env):
        import pty
        import shlex
        self.master, slave = pty.openpty()
        self._winsize(slave, cols, rows)
        self.proc = subprocess.Popen(shlex.split(command), stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                                     env=env, start_new_session=True, close_fds=True)
        os.close(slave)
        self.pid = self.proc.pid
        self.closed = False

    @staticmethod
    def _winsize(fd, cols, rows):
        import fcntl
        import struct
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def read(self):
        try:
            return os.read(self.master, 8192)
        except OSError:
            return b''

    def write(self, data):
        os.write(self.master, data)

    def resize(self, cols, rows):
        self._winsize(self.master, cols, rows)

    def alive(self):
        return self.proc.poll() is None

    def finish(self):
        pass  # the pty reports EOF/EIO by itself

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.alive():
            try:
                os.killpg(self.proc.pid, 15)
            except OSError:
                pass
        try:
            os.close(self.master)
        except OSError:
            pass


class Terminal:
    def __init__(self, shell, cwd, cols, rows):
        self.id = uuid.uuid4().hex[:8]
        self.shell, self.cwd = shell, str(cwd)
        self.lock = threading.Lock()
        self.screen = Screen(cols, rows, reply=self._reply)
        env = dict(os.environ, TERM='xterm-256color', COLORTERM='truecolor')
        env.pop('CLAUDECODE', None)
        command = shell_command(shell)
        cls = _WinPty if os.name == 'nt' else _PosixPty
        self.pty = cls(command, self.cwd, self.screen.cols, self.screen.rows, env)
        self.started = time.time()
        self.ended = None
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.thread = threading.Thread(target=self._pump, daemon=True, name='pty-' + self.id)
        self.thread.start()
        threading.Thread(target=self._watch, daemon=True, name='pty-watch-' + self.id).start()

    def _watch(self):
        while self.pty.alive():
            time.sleep(0.25)
        time.sleep(0.2)
        self.pty.finish()

    def _reply(self, text):
        try:
            self.pty.write(text.encode('utf-8'))
        except OSError:
            pass

    def _pump(self):
        while True:
            data = self.pty.read()
            if not data:
                break
            text = self.decoder.decode(data)
            with self.lock:
                self.screen.feed(text)
        with self.lock:
            self.ended = time.time()
            self.screen.feed('\r\n\x1b[90m[process exited]\x1b[0m')

    def write(self, text):
        if self.ended:
            raise ValueError('Терминал завершён')
        self.pty.write(text.encode('utf-8'))

    def resize(self, cols, rows):
        with self.lock:
            self.screen.resize(cols, rows)
            cols, rows = self.screen.cols, self.screen.rows
        self.pty.resize(cols, rows)

    def snapshot(self, scroll=0):
        with self.lock:
            data = self.screen.snapshot(scroll)
        data.update(id=self.id, shell=self.shell, cwd=self.cwd, alive=self.ended is None)
        return data

    def info(self):
        return {'id': self.id, 'shell': self.shell, 'cwd': self.cwd, 'alive': self.ended is None,
                'title': self.screen.title, 'started': self.started}

    def close(self):
        self.pty.close()


class Terminals:
    def __init__(self, bridge):
        self.bridge = bridge
        self.items = {}

    def open(self, shell, cwd, cols=100, rows=30):
        # finished terminals free their slot
        for tid in [t for t, term in self.items.items() if term.ended]:
            self.items.pop(tid).close()
        if len(self.items) >= MAX_TERMINALS:
            raise ValueError(f'Открыто уже {MAX_TERMINALS} терминалов — закрой лишние')
        term = Terminal(shell, cwd, cols, rows)
        self.items[term.id] = term
        return term

    def get(self, tid):
        term = self.items.get(tid)
        if term is None:
            raise ValueError('Нет такого терминала (закрыт или мост перезапущен)')
        return term

    def close(self, tid):
        term = self.items.pop(tid, None)
        if term:
            term.close()

    def close_all(self):
        for tid in list(self.items):
            self.close(tid)

    def list(self):
        return [t.info() for t in self.items.values()]
