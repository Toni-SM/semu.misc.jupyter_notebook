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
from subprocess import Popen
from prompt_toolkit.eventloop.utils import get_event_loop

import carb
import omni.ext

import nest_asyncio
from jupyter_client.kernelspec import KernelSpecManager as _KernelSpecManager


def _init_signal(self):
    """Dummy method to initialize the notebook app outside of the main thread
    """
    pass


class KernelSpecManager(_KernelSpecManager):
    def __init__(self, *args, **kwargs) -> None:
        """Custom kernel spec manager to allow for loading of custom kernels
        """
        super().__init__(*args, **kwargs)
        kernel_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "kernels"))
        if kernel_dir not in self.kernel_dirs:
            self.kernel_dirs.append(kernel_dir)


class Extension(omni.ext.IExt):
    
    WINDOW_NAME = "Embedded Jupyter Notebook"
    MENU_PATH = f"Window/{WINDOW_NAME}"

    def on_startup(self, ext_id):
        self._app = None
        self._socket = None
        self._process = None
        self._stop_socket = False

        self._loop = get_event_loop()
        self._settings = carb.settings.get_settings()
        self._extension_path = omni.kit.app.get_app().get_extension_manager().get_extension_path(ext_id)
        sys.path.append(os.path.join(self._extension_path, "data", "provisioners"))

        # get extension settings
        self._token = self._settings.get("/exts/semu.misc.jupyter_notebook/token")
        self._notebook_ip = self._settings.get("/exts/semu.misc.jupyter_notebook/notebook_ip")
        self._notebook_port = self._settings.get("/exts/semu.misc.jupyter_notebook/notebook_port")
        self._notebook_dir = self._settings.get("/exts/semu.misc.jupyter_notebook/notebook_dir")
        self._command_line_options = self._settings.get("/exts/semu.misc.jupyter_notebook/command_line_options")
        self._run_in_external_process = self._settings.get("/exts/semu.misc.jupyter_notebook/run_in_external_process")
        self._classic_notebook_interface = self._settings.get("/exts/semu.misc.jupyter_notebook/classic_notebook_interface")

        # menu item
        editor_menu = omni.kit.ui.get_editor_menu()
        if editor_menu:
            self._menu = editor_menu.add_item(Extension.MENU_PATH, self._show_notification, toggle=False, value=False)

        # code execution thread
        threading.Thread(target=self._launch_socket).start()

        # run jupyter notebook in a separate process
        if self._run_in_external_process:
            self._launch_jupyter_process()
        # run jupyter notebook in the separate thread
        else:
            threading.Thread(target=self._launch_jupyter_thread).start()

    def on_shutdown(self):
        # clean extension paths from sys.path
        if self._extension_path is not None:
            sys.path.remove(os.path.join(self._extension_path, "data", "provisioners"))
            self._extension_path = None
        # close the socket
        self._stop_socket = True
        if self._socket is not None:
            self._socket.close()
            self._loop.run_until_complete(asyncio.sleep(1.0))
            self._socket = None
        # close the jupyter notebook (external process)
        if self._run_in_external_process:
            if self._process is not None:
                try:
                    self._process.terminate()  # .kill()
                except OSError as e:
                    if sys.platform == 'win32':
                        if e.winerror != 5:
                            raise
                    else:
                        from errno import ESRCH
                        if not isinstance(e, ProcessLookupError) or e.errno != ESRCH:
                            raise
                self._process.wait()
                self._process = None
        # close the jupyter notebook (internal thread)
        else:
            if self._app is not None:
                try:
                    self._loop.run_until_complete(self._app._stop())
                except Exception as e:
                    carb.log_error(str(e))
                self._app = None

    def _show_notification(self, *args, **kwargs) -> None:
        """Show a Jupyter Notebook URL in the notification area
        """
        display_url = ""
        if self._run_in_external_process:
            if self._process is not None:
                notebook_txt = os.path.join(self._extension_path, "data", "launchers", "notebook.txt")
                if os.path.exists(notebook_txt):
                    with open(notebook_txt, "r") as f:
                        display_url = f.read()
        else:
            if self._app is not None:
                display_url = self._app.display_url

        if display_url:
            notification = "Jupyter Notebook is running at:\n\n  - " + display_url.replace(" or ", "  - ")
            status=omni.kit.notification_manager.NotificationStatus.INFO
        else:
            notification = "Unable to identify Jupyter Notebook URL"
            status=omni.kit.notification_manager.NotificationStatus.WARNING

        ok_button = omni.kit.notification_manager.NotificationButtonInfo("OK", on_complete=None)
        omni.kit.notification_manager.post_notification(notification, 
                                                        hide_after_timeout=False, 
                                                        duration=0, 
                                                        status=status, 
                                                        button_infos=[ok_button])
        
        print(notification)
        carb.log_info(notification)

    def _launch_socket(self) -> None:
        """Launch the internal socket server for executing code using the Omniverse scope
        """
        async def _exec_code(code: str) -> dict:
            """Execute code in the Omniverse scope
            
            :param code: code to execute
            :type code: str
            :return: reply dictionary as ipython notebook expects it
            :rtype: dict
            """
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
        
        socket_port = self._settings.get("/exts/semu.misc.jupyter_notebook/socket_port")
        socket_txt = os.path.join(self._extension_path, "data", "launchers", "socket.txt")

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._socket.bind(("127.0.0.1", socket_port))
            self._socket.listen()
        except OSError as e:
            carb.log_error(str(e))
            self._socket.close()
            self._socket = None
            # delete socket.txt file
            if os.path.exists(socket_txt):
                os.remove(socket_txt)
            return
        
        # write the socket port to socket.txt file
        with open(socket_txt, "w") as f:
            f.write(str(socket_port))

        # start the processing loop
        while not self._stop_socket:
            conn, _ = self._socket.accept()
            with conn:
                data = conn.recv(2048)
                code = data.decode("utf-8")
                reply = self._loop.run_until_complete(_exec_code(code))
                conn.sendall(json.dumps(reply).encode("utf-8"))

    def _launch_jupyter_process(self) -> None:
        """Launch the Jupyter notebook in a separate process
        """
        cmd = [os.path.abspath(os.path.join(os.path.dirname(os.__file__), "..", "..", "bin", "python3")), 
               os.path.join(self._extension_path, "data", "launchers", "jupyter_launcher.py"),
               self._notebook_ip,
               str(self._notebook_port),
               self._token,
               str(self._classic_notebook_interface),
               self._notebook_dir,
               self._command_line_options]
                
        carb.log_info("Starting Jupyter server in separate process")
        carb.log_info("  Command: " + " ".join(cmd))
        try:
            self._process = Popen(cmd, cwd=os.path.join(self._extension_path, "data", "launchers"))
        except Exception as e:
            carb.log_error("Error starting Jupyter server: {}".format(e))
            self._process = None

    def _launch_jupyter_thread(self) -> None:
        """Launch the Jupyter notebook in a separate thread
        """
        asyncio.run(self._async_jupyter())

    async def _async_jupyter(self) -> None:
        """Launch the Jupyter notebook inside the Omniverse application
        """
        # get settings
        argv = []
        if self._command_line_options:
            argv = self._command_line_options.split(" ")
        if self._notebook_dir:
            self._notebook_dir = "--notebook-dir={}".format(self._notebook_dir)
        else:
            self._notebook_dir = "--notebook-dir={}".format(os.path.join(self._extension_path, "data", "notebooks"))
        argv.append(self._notebook_dir)
        if self._classic_notebook_interface:
            argv.append("--NotebookApp.token={}".format(self._token))
        else:
            argv.append("--ServerApp.token={}".format(self._token))

        carb.log_info("Starting Jupyter server in separate thread")
        carb.log_info(" Argv: {}".format(argv))

        # jupyter notebook
        if self._classic_notebook_interface:
            from notebook.notebookapp import NotebookApp

            self._app = NotebookApp(ip=self._notebook_ip, 
                                    port=self._notebook_port,
                                    kernel_spec_manager_class=KernelSpecManager)
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
            self._app = ServerApp.instance(ip=self._notebook_ip, 
                                           port=self._notebook_port,
                                           kernel_spec_manager_class=KernelSpecManager,
                                           jpserver_extensions=jpserver_extensions)
            self._app.aliases.update(LabApp.aliases)
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
