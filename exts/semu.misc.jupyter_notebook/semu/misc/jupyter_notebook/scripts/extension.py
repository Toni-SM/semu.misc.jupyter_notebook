import __future__

import os
import sys
import json
import types
import struct
import socket
import asyncio
import traceback
import threading
import contextlib
from io import StringIO
from subprocess import Popen
from prompt_toolkit.eventloop.utils import get_event_loop
from dis import COMPILER_FLAG_NAMES
try:
    from ast import PyCF_ALLOW_TOP_LEVEL_AWAIT
except ImportError:
    PyCF_ALLOW_TOP_LEVEL_AWAIT = 0

import carb
import omni.ext

import nest_asyncio
from jupyter_client.kernelspec import KernelSpecManager as _KernelSpecManager


def _get_coroutine_flag() -> int:
    """Get the coroutine flag for the current Python version
    """
    for k, v in COMPILER_FLAG_NAMES.items():
        if v == "COROUTINE":
            return k
    return -1

COROUTINE_FLAG = _get_coroutine_flag()

def _has_coroutine_flag(code) -> bool:
    """Check if the code has the coroutine flag set
    """
    if COROUTINE_FLAG == -1:
        return False
    return bool(code.co_flags & COROUTINE_FLAG)

def _get_compiler_flags() -> int:
    """Get the compiler flags for the current Python version
    """
    flags = 0
    for value in globals().values():
        try:
            if isinstance(value, __future__._Feature):
                f = value.compiler_flag
                flags |= f
        except BaseException:
            pass
    flags = flags | PyCF_ALLOW_TOP_LEVEL_AWAIT
    return flags

def _init_signal(self) -> None:
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

        self._globals = {**globals()}
        self._locals = self._globals

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
        
        # create socket
        self._socket = self._create_socket()
        get_event_loop().add_reader(self._socket, self._on_accept_connection)
        
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
        if self._socket:
            get_event_loop().remove_reader(self._socket)
            self._socket.close()
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
                    get_event_loop().run_until_complete(self._app._stop())
                except Exception as e:
                    carb.log_error(str(e))
                self._app = None

    # extension ui methods

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

    # internal socket methods

    def _create_socket(self) -> socket.socket:
        """Create a socket to listen for incoming connections from the IPython kernel

        :return: The socket
        :rtype: socket.socket
        """
        socket_port = self._settings.get("/exts/semu.misc.jupyter_notebook/socket_port")
        socket_txt = os.path.join(self._extension_path, "data", "launchers", "socket.txt")

        # delete socket.txt file
        if os.path.exists(socket_txt):
            os.remove(socket_txt)

        # create socket
        _socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _socket.bind(("127.0.0.1", socket_port))
        _socket.listen(5)

        # write the socket port to socket.txt file
        carb.log_info("Internal socket server is running at port {}".format(socket_port))
        with open(socket_txt, "w") as f:
            f.write(str(socket_port))

        return _socket

    def _on_accept_connection(self) -> None:
        """Accept a connection from the IPython kernel
        """
        async def run() -> None:
            """Coroutine for handling the incoming connection
            """
            def _recvall(n):
                data = bytearray()
                while len(data) < n:
                    packet = conn.recv(n - len(data))
                    if not packet:
                        return None
                    data.extend(packet)
                return data

            def _recv_msg():
                raw_msglen = _recvall(4)
                if not raw_msglen:
                    return None
                return _recvall(struct.unpack('>I', raw_msglen)[0])
            
            def _handle_incoming_data() -> None:
                """Handle incoming data from the IPython kernel
                """
                data = _recv_msg()
                if not data:
                    get_event_loop().remove_reader(conn)
                    conn.close()
                asyncio.run_coroutine_threadsafe(self._exec_code_async(data.decode("utf-8"), conn), get_event_loop())
            
            get_event_loop().add_reader(conn, _handle_incoming_data)

        if self._socket is None:
            return
        conn, _ = self._socket.accept()
        task = get_event_loop().create_task(run())

    async def _exec_code_async(self, statement: str, conn: socket.socket) -> None:
        """Execute the statement in the Omniverse scope and send the result to the IPython kernel
        
        :param statement: statement to execute
        :type statement: str
        :param conn: connection to the IPython kernel
        :type conn: socket.socket

        :return: reply dictionary as ipython notebook expects it
        :rtype: dict
        """
        _stdout = StringIO()
        try:
            with contextlib.redirect_stdout(_stdout):
                should_exec_code = True
                # try 'eval' first
                try:
                    code = compile(statement, "<string>", "eval", flags= _get_compiler_flags(), dont_inherit=True)
                except SyntaxError:
                    pass
                else:
                    result = eval(code, self._globals, self._locals)
                    should_exec_code = False
                # if 'eval' fails, try 'exec'
                if should_exec_code:
                    code = compile(statement, "<string>", "exec", flags= _get_compiler_flags(), dont_inherit=True)
                    result = eval(code, self._globals, self._locals)
                # await the result if it is a coroutine
                if _has_coroutine_flag(code):
                    result = await result
        except Exception as e:
            # clean traceback
            _traceback = traceback.format_exc()
            _i = _traceback.find('\n  File "<string>"')
            if _i != -1:
                _traceback = _traceback[_i + 20:]
            _traceback = _traceback.replace(", in <module>\n", "\n")
            # build reply dictionary
            reply = {"status": "error", 
                    "traceback": [_traceback],
                    "ename": str(type(e).__name__),
                    "evalue": str(e)}
        else:
            reply = {"status": "ok"}

        # add output to reply dictionary for printing
        reply["output"] = _stdout.getvalue()

        # send the reply to the IPython kernel
        conn.send(json.dumps(reply).encode("utf-8"))
        get_event_loop().remove_reader(conn)
        conn.close()

    # launch Jupyter Notebook methods

    def _launch_jupyter_process(self) -> None:
        """Launch the Jupyter notebook in a separate process
        """
        # get packages path
        paths = [p for p in sys.path if "pip3-envs" in p]
        packages_txt = os.path.join(self._extension_path, "data", "launchers", "packages.txt")
        with open(packages_txt, "w") as f:
            f.write("\n".join(paths))

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
