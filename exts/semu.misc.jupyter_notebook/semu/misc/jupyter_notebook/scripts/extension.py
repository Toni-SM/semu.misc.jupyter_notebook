import os
import sys
import types
import asyncio
import threading
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
    def find_kernel_specs(self):
        kernel_dirs = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "kernels"))
        self.kernel_dirs = [kernel_dirs]
        return {"embedded_omniverse_python3": os.path.join(kernel_dirs, "embedded_omniverse_python3")}


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id):
        ext_manager = omni.kit.app.get_app().get_extension_manager()
        self._settings = carb.settings.get_settings()

        self._app = None
        self._extension_path = ext_manager.get_extension_path(ext_id)

        sys.path.append(os.path.join(self._extension_path, "data", "provisioners"))

        threading.Thread(target=self._launch_app).start()

    def on_shutdown(self):
        if self._extension_path is not None:
            sys.path.remove(os.path.join(self._extension_path, "data", "provisioners"))
            self._extension_path = None
        if self._app is not None:
            loop = get_event_loop()
            try:
                loop.run_until_complete(self._app._stop())
            except Exception as e:
                print(f"Error stopping app: {e}")
            self._app = None
            print("Stopped Jupyter server")

    def _launch_app(self):
        asyncio.run(self._async_jupyter())

    async def _async_jupyter(self):
        # get settings
        ip = self._settings.get("/exts/semu.misc.jupyter_notebook/host")
        port = self._settings.get("/exts/semu.misc.jupyter_notebook/port")
        open_browser = self._settings.get("/exts/semu.misc.jupyter_notebook/open_browser")
        classic_notebook_interface = self._settings.get("/exts/semu.misc.jupyter_notebook/classic_notebook_interface")

        argv = []
        argv.append("--allow-root")
        if not open_browser:
            argv.append("--no-browser")
        argv.append("--notebook-dir={}".format(os.path.join(self._extension_path, "data", "notebooks")))

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
