import __future__

import os
import sys
import jedi
import json
import glob
import socket
import asyncio
import traceback
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


class Extension(omni.ext.IExt):
    
    WINDOW_NAME = "Embedded Jupyter Notebook"
    MENU_PATH = f"Window/{WINDOW_NAME}"

    def on_startup(self, ext_id):

        self._globals = {**globals()}
        self._locals = self._globals

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
        self._launch_jupyter_process()

        # jedi (autocompletion)
        # application root path
        app_folder = carb.settings.get_settings().get_as_string("/app/folder")
        if not app_folder:
            app_folder = carb.tokens.get_tokens_interface().resolve("${app}")
        path = os.path.normpath(os.path.join(app_folder, os.pardir))
        # get extension paths
        folders = [
            "exts", 
            "extscache",
            os.path.join("kit", "extensions"),
            os.path.join("kit", "exts"),
            os.path.join("kit", "extsPhysics"),
            os.path.join("kit", "extscore"),
        ]
        added_sys_path = []
        for folder in folders:
            sys_paths = glob.glob(os.path.join(path, folder, "*"))
            for sys_path in sys_paths:
                if os.path.isdir(sys_path):
                    added_sys_path.append(sys_path)
        # python environment
        python_exe = "python.exe" if sys.platform == "win32" else "bin/python3"
        environment_path = os.path.join(path, "kit", "python", python_exe)
        # jedi project
        carb.log_info("Autocompletion: jedi.Project")
        carb.log_info(f"  |-- path: {path}")
        carb.log_info(f"  |-- added_sys_path: {len(added_sys_path)} items")
        carb.log_info(f"  |-- environment_path: {environment_path}")
        self._jedi_project = jedi.Project(path=path,
                                          environment_path=environment_path,
                                          added_sys_path=added_sys_path,
                                          load_unsafe_extensions=False)

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

    # extension ui methods

    def _on_shutdown_event(self, event):
        if event.type == omni.kit.app.POST_QUIT_EVENT_TYPE:
            self.on_shutdown()

    def _show_notification(self, *args, **kwargs) -> None:
        """Show a Jupyter Notebook URL in the notification area
        """
        display_url = ""
        if self._process is not None:
            notebook_txt = os.path.join(self._extension_path, "data", "launchers", "notebook.txt")
            if os.path.exists(notebook_txt):
                with open(notebook_txt, "r") as f:
                    display_url = f.read()

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
                code = data.decode()
                # completion
                if code[:3] == "%!c":
                    code = code[3:]
                    asyncio.run_coroutine_threadsafe(self._parent._complete_code_async(code, self.transport), _get_event_loop())
                # introspection
                elif code[:3] == "%!i":
                    code = code[3:]
                    pos = code.find('%')
                    line, column = [int(i) for i in code[:pos].split(':')]
                    code = code[pos + 1:]
                    asyncio.run_coroutine_threadsafe(self._parent._introspect_code_async(code, line, column, self.transport), _get_event_loop())
                # execution
                else:
                    asyncio.run_coroutine_threadsafe(self._parent._exec_code_async(code, self.transport), _get_event_loop())

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

    async def _complete_code_async(self, statement: str, transport: asyncio.Transport) -> None:
        """Complete objects under the cursor and send the result to the IPython kernel
        
        :param statement: statement to complete
        :type statement: str
        :param transport: transport to send the result to the IPython kernel
        :type transport: asyncio.Transport

        :return: reply dictionary
        :rtype: dict
        """
        # generate completions
        script = jedi.Script(statement, project=self._jedi_project)
        completions = script.complete()
        delta = completions[0].get_completion_prefix_length() if completions else 0

        reply = {"matches": [c.name for c in completions], "delta": delta}

        # send the reply to the IPython kernel
        reply = json.dumps(reply)
        transport.write(reply.encode())

        # close the connection
        transport.close()

    async def _introspect_code_async(self, statement: str, line: int, column: int, transport: asyncio.Transport) -> None:
        """Introspect code under the cursor and send the result to the IPython kernel
        
        :param statement: statement to introspect
        :type statement: str
        :param line: the line where the definition occurs
        :type line: int
        :param column: the column where the definition occurs
        :type column: int
        :param transport: transport to send the result to the IPython kernel
        :type transport: asyncio.Transport

        :return: reply dictionary
        :rtype: dict
        """
        # generate introspection
        script = jedi.Script(statement, project=self._jedi_project)
        definitions = script.infer(line=line, column=column)
        
        reply = {"found": False, "data": "TODO"}
        if len(definitions):
            reply["found"] = True
            reply["data"] = definitions[0].docstring()

        # send the reply to the IPython kernel
        reply = json.dumps(reply)
        transport.write(reply.encode())

        # close the connection
        transport.close()

    async def _exec_code_async(self, statement: str, transport: asyncio.Transport) -> None:
        """Execute the statement in the Omniverse scope and send the result to the IPython kernel
        
        :param statement: statement to execute
        :type statement: str
        :param transport: transport to send the result to the IPython kernel
        :type transport: asyncio.Transport

        :return: reply dictionary
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
        carb.log_info("  |-- command: " + " ".join(cmd))
        try:
            self._process = subprocess.Popen(cmd, cwd=os.path.join(self._extension_path, "data", "launchers"))
        except Exception as e:
            carb.log_error("Error starting Jupyter server: {}".format(e))
            self._process = None
