import os
import sys
import types
import asyncio
import threading

import carb
import omni.ext

import nest_asyncio
from notebook.notebookapp import NotebookApp
from notebook.services.kernels.kernelmanager import MappingKernelManager
from jupyter_client.kernelspec import KernelSpecManager


def _init_signal(self):
    """Dummy method to initialize the notebook app outside of the main thread
    """
    pass


class CustomMappingKernelManager(MappingKernelManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._inner_kwargs = {}

    def set_globals(self, value):
        self._inner_kwargs = {"_globals": value, "_locals": value}

    async def start_kernel(self, kernel_id=None, path=None, **kwargs):
        kwargs.update(self._inner_kwargs)
        return await super().start_kernel(kernel_id=kernel_id, path=path, **kwargs)


class CustomKernelSpecManager(KernelSpecManager):
    def find_kernel_specs(self):
        kernel_dirs = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "kernels"))
        self.kernel_dirs = [kernel_dirs]
        return {"embedded_omniverse_python3": os.path.join(kernel_dirs, "embedded_omniverse_python3")}


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id):
        ext_manager = omni.kit.app.get_app().get_extension_manager()

        self._app = None
        self._extension_path = ext_manager.get_extension_path(ext_id)

        sys.path.append(os.path.join(self._extension_path, "data", "provisioners"))
        threading.Thread(target=self._inner_app_thread).start()

    def on_shutdown(self):
        if self._extension_path is not None:
            sys.path.remove(os.path.join(self._extension_path, "data", "provisioners"))
            self._extension_path = None

    def _inner_app_thread(self):
        asyncio.run(self._inner_app_async())

    async def _inner_app_async(self):
        nest_asyncio.apply()

        # get settings
        settings = carb.settings.get_settings()

        ip = settings.get("/exts/semu.misc.jupyter_notebook/host")
        port = settings.get("/exts/semu.misc.jupyter_notebook/port")
        open_browser = settings.get("/exts/semu.misc.jupyter_notebook/open_browser")

        # instantiate notebook app
        self._app = NotebookApp(ip=ip, 
                                port=port, 
                                kernel_manager_class=CustomMappingKernelManager, 
                                kernel_spec_manager_class=CustomKernelSpecManager)

        # override the init_signal method to initialize the notebook app outside of the main thread
        self._app.init_signal = types.MethodType(_init_signal, self._app)

        # initialize the notebook app
        argv = []
        argv.append("--allow-root")
        if not open_browser:
            argv.append("--no-browser")
        argv.append("--notebook-dir={}".format(os.path.join(self._extension_path, "data", "notebooks")))

        carb.log_info("argv: {}".format(argv))
        self._app.initialize(argv=argv)

        # set the globals to the custom notebook kernel manager
        try:
            self._app.kernel_manager.set_globals(globals())
        except Exception as e:
            carb.log_warn(f"Failed to set globals: {e}")

        # start the notebook app
        self._app.start()
