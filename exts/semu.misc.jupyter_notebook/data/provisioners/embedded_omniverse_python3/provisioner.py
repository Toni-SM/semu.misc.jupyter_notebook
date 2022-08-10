from typing import Any, Dict, List, Optional

import sys
import types
import asyncio
import threading

import nest_asyncio
from ipykernel.kernelapp import IPKernelApp
from jupyter_client.connect import KernelConnectionInfo, LocalPortCache
from jupyter_client.provisioning import KernelProvisionerBase
from jupyter_client.localinterfaces import local_ips, is_local_ip


def _init_signal(self):
    """Dummy method to initialize the kernel outside of the main thread
    """
    pass


class _Module:
    """Dummy module to be used as a placeholder for the calling scope
    """
    pass


class Provisioner(KernelProvisionerBase):

    _local_dict = {}
    _globals_dict = {}
    process = None
    ports_cached = False

    @property
    def has_process(self) -> bool:
        return self.process is not None

    async def poll(self) -> Optional[int]:
        if self.process is not None:
            return None
        return 0

    async def wait(self) -> Optional[int]:
        if self.process:
            while await self.poll() is None:
                await asyncio.sleep(0.1)
            self.process = None  # allow has_process to now return False
        return 0

    async def send_signal(self, signum: int) -> None:
        if self.process:
            pass
        self.process = None

    async def kill(self, restart: bool = False) -> None:
        if self.process:
            pass
        self.process = None

    async def terminate(self, restart: bool = False) -> None:
        if self.process:
            pass
        self.process = None
        
    async def cleanup(self, restart: bool = False) -> None:
        if self.ports_cached and not restart:
            lpc = LocalPortCache.instance()
            for port in [self.connection_info['shell_port'],
                         self.connection_info['iopub_port'],
                         self.connection_info['stdin_port'],
                         self.connection_info['hb_port'],
                         self.connection_info['control_port']]:
                lpc.return_port(port)

    async def pre_launch(self, **kwargs: Any) -> Dict[str, Any]:
        if self.parent:
            kernel_manager = self.parent
            if kernel_manager.transport == 'tcp' and not is_local_ip(kernel_manager.ip):
                raise RuntimeError("Can only launch a kernel on a local interface."
                    "Current address: %s."
                    "Valid addresses are: %s" % (kernel_manager.ip, local_ips()))

            # write connection file / get default ports
            if kernel_manager.cache_ports and not self.ports_cached:
                lpc = LocalPortCache.instance()
                kernel_manager.shell_port = lpc.find_available_port(kernel_manager.ip)
                kernel_manager.iopub_port = lpc.find_available_port(kernel_manager.ip)
                kernel_manager.stdin_port = lpc.find_available_port(kernel_manager.ip)
                kernel_manager.hb_port = lpc.find_available_port(kernel_manager.ip)
                kernel_manager.control_port = lpc.find_available_port(kernel_manager.ip)
                self.ports_cached = True

            kernel_manager.write_connection_file()
            self.connection_info = kernel_manager.get_connection_info()
            kernel_cmd = kernel_manager.format_kernel_cmd()
        else:
            kernel_cmd = self.kernel_spec.argv

        return await super().pre_launch(cmd=kernel_cmd, **kwargs)

    async def launch_kernel(self, cmd: List[str], **kwargs: Any) -> KernelConnectionInfo:
        connection_file = cmd[-1]
        self._globals_dict = kwargs.get("_globals", {})
        self._locals_dict = kwargs.get("_locals", {})

        threading.Thread(target=self._inner_kernel_thread, args=(connection_file, True,)).start()
        self.process = True

        return self.connection_info

    def _inner_kernel_sync(self, connection_file):
        app = IPKernelApp(connection_file=connection_file)
        app.init_signal = types.MethodType(_init_signal, app)
        try:
            app.initialize([])
        except Exception as e:
            self.process = None
            return
        
        main = app.kernel.shell._orig_sys_modules_main_mod
        if main is not None:
            sys.modules[app.kernel.shell._orig_sys_modules_main_name] = main

        if app is None:
            self.process = None
            return

        _module = _Module()
        _module.__dict__ = self._globals_dict
        app.kernel.user_module = _module
        app.kernel.user_ns = self._locals_dict
        app.shell.set_completer_frame()

        self.process = app
        app.start()

    async def _inner_kernel_async(self, connection_file):
        app = IPKernelApp(connection_file=connection_file)
        app.init_signal = types.MethodType(_init_signal, app)
        try:
            app.initialize([])
        except Exception as e:
            self.process = None
            return
        
        main = app.kernel.shell._orig_sys_modules_main_mod
        if main is not None:
            sys.modules[app.kernel.shell._orig_sys_modules_main_name] = main

        if app is None:
            self.process = None
            return

        _module = _Module()
        _module.__dict__ = self._globals_dict
        app.kernel.user_module = _module
        app.kernel.user_ns = self._locals_dict
        app.shell.set_completer_frame()

        self.process = app
        nest_asyncio.apply()
        app.start()
        
    def _inner_kernel_thread(self, connection_file, async_run=False):
        # start embedded kernel (ipykernel/embed.py)
        if async_run:
            asyncio.run(self._inner_kernel_async(connection_file))
        else:
            self._inner_kernel_sync(connection_file)
