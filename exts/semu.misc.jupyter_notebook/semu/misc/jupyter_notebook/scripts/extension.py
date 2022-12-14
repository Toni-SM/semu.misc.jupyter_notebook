import __future__

import os
import sys
import json
import types
import socket
import asyncio
import traceback
import threading
import subprocess
import contextlib
from io import StringIO
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

def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Backward compatible function for getting the event loop
    """
    try:
        if sys.version_info >= (3, 7):
            return asyncio.get_running_loop()
        else:
            return asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.get_event_loop_policy().get_event_loop()

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
        self._server = None
        self._process = None

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

        self._socket_port = self._settings.get("/exts/semu.misc.jupyter_notebook/socket_port")
        kill_processes_with_port_in_use = self._settings.get("/exts/semu.misc.jupyter_notebook/kill_processes_with_port_in_use")

        # menu item
        self._editor_menu = omni.kit.ui.get_editor_menu()
        if self._editor_menu:
            self._menu = self._editor_menu.add_item(Extension.MENU_PATH, self._show_notification, toggle=False, value=False)

        # shutdown stream
        self.shutdown_stream_event = omni.kit.app.get_app().get_shutdown_event_stream() \
            .create_subscription_to_pop(self._on_shutdown_event, name="semu.misc.jupyter_notebook", order=0)

        # ensure port is free
        if kill_processes_with_port_in_use:
            # windows
            if sys.platform == "win32":
                pids = []
                cmd = ["netstat", "-ano"]
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
                for line in p.stdout:
                    if f":{self._socket_port}".encode() in line or f":{self._notebook_port}".encode() in line:
                        if "listening".encode() in line.lower():
                            pids.append(line.strip().split(b" ")[-1].decode())
                p.wait()
                for pid in pids:
                    carb.log_warn(f"Forced process shutdown with PID {pid}")
                    cmd = ["taskkill", "/PID", pid, "/F"]
                    subprocess.Popen(cmd).wait()
            # linux
            elif sys.platform == "linux":
                pids = []
                cmd = ["netstat", "-ltnup"]
                try:
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
                    for line in p.stdout:
                        if f":{self._socket_port}".encode() in line or f":{self._notebook_port}".encode() in line:
                            pids.append(line.strip().split(b" ")[-1].decode().split("/")[0])
                    p.wait()
                except FileNotFoundError as e:
                    carb.log_warn(f"Command (netstat) not available. Install it using `apt install net-tools`")
                for pid in pids:
                    carb.log_warn(f"Forced process shutdown with PID {pid}")
                    cmd = ["kill", "-9", pid]
                    subprocess.Popen(cmd).wait()

        # create socket
        self._create_socket()
        
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
        # clean up menu item
        if self._menu is not None:
            self._editor_menu.remove_item(self._menu)
            self._menu = None
        # close the socket
        if self._server:
            self._server.close()
            _get_event_loop().run_until_complete(self._server.wait_closed())
        # close the jupyter notebook (external process)
        if self._run_in_external_process:
            if self._process is not None:
                process_pid = self._process.pid
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
                # make sure the process is not running anymore in Windows
                if sys.platform == 'win32':
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(process_pid)])
                # wait for the process to terminate
                self._process.wait()
                self._process = None
        # close the jupyter notebook (internal thread)
        else:
            if self._app is not None:
                try:
                    _get_event_loop().run_until_complete(self._app._stop())
                except Exception as e:
                    carb.log_error(str(e))
                self._app = None

    # extension ui methods

    def _on_shutdown_event(self, event):
        if event.type == omni.kit.app.POST_QUIT_EVENT_TYPE:
            self.on_shutdown()

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

    def _create_socket(self) -> None:
        """Create a socket server to listen for incoming connections from the IPython kernel
        """
        socket_txt = os.path.join(self._extension_path, "data", "launchers", "socket.txt")

        # delete socket.txt file
        if os.path.exists(socket_txt):
            os.remove(socket_txt)

        class ServerProtocol(asyncio.Protocol):
            def __init__(self, parent) -> None:
                super().__init__()
                self._parent = parent

            def connection_made(self, transport):
                peername = transport.get_extra_info('peername')
                carb.log_info('Connection from {}'.format(peername))
                self.transport = transport

            def data_received(self, data):
                asyncio.run_coroutine_threadsafe(self._parent._exec_code_async(data.decode(), self.transport),
                                                 _get_event_loop())

        async def server_task():
            self._server = await _get_event_loop().create_server(protocol_factory=lambda: ServerProtocol(self), 
                                                                 host="127.0.0.1", 
                                                                 port=self._socket_port,
                                                                 family=socket.AF_INET,
                                                                 reuse_port=None if sys.platform == 'win32' else True)
            await self._server.start_serving()

        task = _get_event_loop().create_task(server_task())

        # write the socket port to socket.txt file
        carb.log_info("Internal socket server is running at port {}".format(self._socket_port))
        with open(socket_txt, "w") as f:
            f.write(str(self._socket_port))

    async def _exec_code_async(self, statement: str, transport: asyncio.Transport) -> None:
        """Execute the statement in the Omniverse scope and send the result to the IPython kernel
        
        :param statement: statement to execute
        :type statement: str
        :param transport: transport to send the result to the IPython kernel
        :type transport: asyncio.Transport

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
        reply = json.dumps(reply)
        transport.write(reply.encode())

        # close the connection
        transport.close()

    # launch Jupyter Notebook methods

    def _launch_jupyter_process(self) -> None:
        """Launch the Jupyter notebook in a separate process
        """
        # get packages path
        paths = [p for p in sys.path if "pip3-envs" in p]
        packages_txt = os.path.join(self._extension_path, "data", "launchers", "packages.txt")
        with open(packages_txt, "w") as f:
            f.write("\n".join(paths))

        if sys.platform == 'win32':
            executable_path = os.path.abspath(os.path.join(os.path.dirname(os.__file__), "..", "python.exe"))
        else:
            executable_path = os.path.abspath(os.path.join(os.path.dirname(os.__file__), "..", "..", "bin", "python3"))

        cmd = [executable_path, 
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
            self._process = subprocess.Popen(cmd, cwd=os.path.join(self._extension_path, "data", "launchers"))
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
