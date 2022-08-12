import os
import sys
import json
import types
import socket
import traceback
import asyncio
import threading
from urllib import response
from prompt_toolkit.eventloop.utils import get_event_loop

import nest_asyncio
from jupyter_client.kernelspec import KernelSpecManager as _KernelSpecManager
from jupyter_server.services.kernels.kernelmanager import MappingKernelManager as _MappingKernelManagerLab
from notebook.services.kernels.kernelmanager import MappingKernelManager as _MappingKernelManagerNotebook


def socket_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 9786))
        s.listen()

        while True:
            conn, addr = s.accept()
            with conn:
                while True:
                    data = conn.recv(2048)
                    if not data:
                        break
                    code = data.decode("utf-8")

                    try:
                        exec(code, globals(), globals())
                        reply = {"status": "ok"}
                    except Exception as e:
                        reply = {"status": "error", 
                                 "traceback": [traceback.format_exc()],
                                 "ename": str(type(e).__name__),
                                 "evalue": str(e)}
                    conn.sendall(json.dumps(reply).encode("utf-8"))


def _init_signal(self):
    """Dummy method to initialize the notebook app outside of the main thread
    """
    pass


class MappingKernelManagerLab(_MappingKernelManagerLab):
    def set_globals(self, value):
        self._embedded_kwargs = {"_globals": value, "_locals": value}

    async def start_kernel(self, kernel_id=None, path=None, **kwargs):
        try:
            kwargs.update(self._embedded_kwargs)
        except:
            pass
        return await super().start_kernel(kernel_id=kernel_id, path=path, **kwargs)


class MappingKernelManagerNotebook(_MappingKernelManagerNotebook):
    def set_globals(self, value):
        self._embedded_kwargs = {"_globals": value, "_locals": value}

    async def start_kernel(self, kernel_id=None, path=None, **kwargs):
        try:
            kwargs.update(self._embedded_kwargs)
        except:
            pass
        return await super().start_kernel(kernel_id=kernel_id, path=path, **kwargs)


class KernelSpecManager(_KernelSpecManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        kernel_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "kernels"))
        if kernel_dir not in self.kernel_dirs:
            self.kernel_dirs.append(kernel_dir)
        print(self.kernel_dirs)

    # def find_kernel_specs(self):
    #     self.kernel_dirs = [kernel_dirs]
    #     return {"embedded_omniverse_python3": os.path.join(kernel_dirs, "embedded_omniverse_python3"),
    #             "embedded_omniverse_python31": os.path.join(kernel_dirs, "embedded_omniverse_python31")}


class Extension:
    def _async_jupyter(self):
        sys.path.append("/isaac-sim/user-exts/semu.misc.jupyter_notebook/exts/semu.misc.jupyter_notebook/data/provisioners")

        # get settings
        ip = "0.0.0.0"
        port = 8889
        open_browser = False
        classic_notebook_interface = True

        argv = []
        argv.append("--allow-root")
        argv.append("--NotebookApp.token='abcd'")
        # argv.append("--debug")
        if not open_browser:
            argv.append("--no-browser")
        argv.append("--notebook-dir={}".format("/isaac-sim/user-exts/semu.misc.jupyter_notebook/exts/semu.misc.jupyter_notebook/data/notebooks"))

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
            # self._app.init_signal = types.MethodType(_init_signal, self._app)  # hack to initialize in a separate thread
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
            # self._app.init_signal = types.MethodType(_init_signal, self._app)  # hack to initialize in a separate thread
            self._app.initialize(argv=argv,
                                 starter_extension=LabApp.name,
                                 find_extensions=find_extensions)
        
        # start the notebook app
        self._app.start()



threading.Thread(target=socket_server).start()

ext = Extension()
ext._async_jupyter()