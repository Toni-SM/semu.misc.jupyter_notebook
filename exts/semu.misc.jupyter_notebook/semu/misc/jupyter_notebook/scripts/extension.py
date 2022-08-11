import os
import sys
import json
import types
import socket
import asyncio
import traceback
import threading
import contextlib
from io import StringIO
from prompt_toolkit.eventloop.utils import get_event_loop

import carb
import omni.ext

import nest_asyncio
from jupyter_client.kernelspec import KernelSpecManager as _KernelSpecManager
from jupyter_server.services.kernels.kernelmanager import MappingKernelManager as _MappingKernelManagerLab
from notebook.services.kernels.kernelmanager import MappingKernelManager as _MappingKernelManagerNotebook


def _init_signal(self):
    """Dummy method to initialize the notebook app outside of the main thread
    """
    pass


class MappingKernelManagerLab(_MappingKernelManagerLab):
    def set_globals(self, value):
        self._embedded_kwargs = {"_globals": value, "_locals": value}

    async def start_kernel(self, kernel_id=None, path=None, **kwargs):
        return await super().start_kernel(kernel_id=kernel_id, path=path, **kwargs)


class MappingKernelManagerNotebook(_MappingKernelManagerNotebook):
    def set_globals(self, value):
        self._embedded_kwargs = {"_globals": value, "_locals": value}

    async def start_kernel(self, kernel_id=None, path=None, **kwargs):
        return await super().start_kernel(kernel_id=kernel_id, path=path, **kwargs)


class KernelSpecManager(_KernelSpecManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        kernel_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "kernels"))
        if kernel_dir not in self.kernel_dirs:
            self.kernel_dirs.append(kernel_dir)


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id):
        ext_manager = omni.kit.app.get_app().get_extension_manager()
        self._settings = carb.settings.get_settings()

        self._app = None
        self._socket = None
        self._loop = get_event_loop()
        self._extension_path = ext_manager.get_extension_path(ext_id)

        sys.path.append(os.path.join(self._extension_path, "data", "provisioners"))

        threading.Thread(target=self._launch_app).start()
        threading.Thread(target=self._launch_socket).start()

    def on_shutdown(self):
        if self._extension_path is not None:
            sys.path.remove(os.path.join(self._extension_path, "data", "provisioners"))
            self._extension_path = None
        # close the socket
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        # close the juypter app
        if self._app is not None:
            loop = get_event_loop()
            try:
                loop.run_until_complete(self._app._stop())
            except Exception as e:
                print(f"Error stopping app: {e}")
            self._app = None
            print("Stopped Jupyter server")

    def _launch_socket(self):
        socket_port = self._settings.get("/exts/semu.misc.jupyter_notebook/socket_port")

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", socket_port))
        self._socket.listen()

        async def _exec_code(code):
            _stdout = StringIO()
            try:
                with contextlib.redirect_stdout(_stdout):
                    exec(code, globals(), globals())
                reply = {"status": "ok"}
            except Exception as e:
                _traceback = traceback.format_exc()
                _i = _traceback.find('\n  File "<string>"')
                if _i != -1:
                    _traceback = _traceback[_i + 20:]
                reply = {"status": "error", 
                        "traceback": [_traceback],
                        "ename": str(type(e).__name__),
                        "evalue": str(e)}
            output = _stdout.getvalue()
            reply["output"] = output
            return reply

        while self._socket is not None:
            conn, addr = self._socket.accept()
            with conn:
                while True:
                    data = conn.recv(2048)
                    if not data:
                        break
                    code = data.decode("utf-8")
                    reply = self._loop.run_until_complete(_exec_code(code))
                    conn.sendall(json.dumps(reply).encode("utf-8"))

        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _launch_app(self):
        asyncio.run(self._async_jupyter())

    async def _async_jupyter(self):
        # get settings
        token = self._settings.get("/exts/semu.misc.jupyter_notebook/token")
        ip = self._settings.get("/exts/semu.misc.jupyter_notebook/notebook_ip")
        port = self._settings.get("/exts/semu.misc.jupyter_notebook/notebook_port")
        notebook_dir = self._settings.get("/exts/semu.misc.jupyter_notebook/notebook_dir")
        command_line_options = self._settings.get("/exts/semu.misc.jupyter_notebook/command_line_options")
        classic_notebook_interface = self._settings.get("/exts/semu.misc.jupyter_notebook/classic_notebook_interface")

        argv = []
        if command_line_options:
            argv = command_line_options.split(" ")
        if notebook_dir:
            argv.append("--notebook-dir={}".format(notebook_dir))
        else:
            argv.append("--notebook-dir={}".format(os.path.join(self._extension_path, "data", "notebooks")))
        if classic_notebook_interface:
            argv.append("--NotebookApp.token='{}'".format(token))
        else:
            argv.append("--ServerApp.token='{}'".format(token))
        carb.log_info("argv: {}".format(argv))

        # jupyter notebook
        if classic_notebook_interface:
            from notebook.notebookapp import NotebookApp

            self._app = NotebookApp(ip=ip, 
                                    port=port,
                                    kernel_spec_manager_class=KernelSpecManager,
                                    kernel_manager_class=MappingKernelManagerNotebook)
            try:
                self._app.kernel_manager.set_globals(globals())
            except:
                pass
            self._app.init_signal = types.MethodType(_init_signal, self._app)  # hack to initialize in a separate thread
            self._app.initialize(argv=argv)

        # jupyter lab
        else:
            from jupyterlab.labapp import LabApp
            from jupyter_server.serverapp import ServerApp

            jpserver_extensions = {LabApp.get_extension_package(): True}
            find_extensions = LabApp.load_other_extensions
            if "jpserver_extensions" in LabApp.serverapp_config:
                jpserver_extensions.update(LabApp.serverapp_config["jpserver_extensions"])
                LabApp.serverapp_config["jpserver_extensions"] = jpserver_extensions
                find_extensions = False
            self._app = ServerApp.instance(ip=ip, 
                                           port=port,
                                           kernel_spec_manager_class=KernelSpecManager,
                                           kernel_manager_class=MappingKernelManagerLab, 
                                           jpserver_extensions=jpserver_extensions)
            self._app.aliases.update(LabApp.aliases)
            try:
                self._app.kernel_manager.set_globals(globals())
            except:
                pass
            self._app.init_signal = types.MethodType(_init_signal, self._app)  # hack to initialize in a separate thread
            self._app.initialize(argv=argv,
                                 starter_extension=LabApp.name,
                                 find_extensions=find_extensions)
        
        # start the notebook app
        nest_asyncio.apply()
        try:
            self._app.start()
        except:
            self._app = None
