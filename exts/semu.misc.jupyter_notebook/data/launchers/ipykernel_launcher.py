import os
import sys
import json
import socket
import asyncio


SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 8224
PACKAGES_PATH = []
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# add packages to sys.path
with open(os.path.join(SCRIPT_DIR, "packages.txt"), "r") as f:
    for p in f.readlines():
        p = p.strip()
        if p:
            PACKAGES_PATH.append(p)
            if p not in sys.path:
                print("Adding package to sys.path: {}".format(p))
                sys.path.append(p)


from ipykernel.kernelbase import Kernel
from ipykernel.kernelapp import IPKernelApp


async def _send_and_recv(message):
    reader, writer = await asyncio.open_connection(host=SOCKET_HOST, 
                                                   port=SOCKET_PORT,
                                                   family=socket.AF_INET)
    writer.write(message.encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    return data.decode()


class EmbeddedKernel(Kernel):
    """Omniverse Kit Python wrapper kernels

    It re-use the IPython's kernel machinery
    https://jupyter-client.readthedocs.io/en/latest/wrapperkernels.html
    """
    # kernel info: https://jupyter-client.readthedocs.io/en/latest/messaging.html#kernel-info
    implementation = "Omniverse Kit (Python 3)"
    implementation_version = "0.1.0"
    language_info = {
        "name": "python",
        "version": "3.7",  # TODO: get from Omniverse Kit
        "mimetype": "text/x-python",
        "file_extension": ".py",
    }
    banner = "Embedded Omniverse (Python 3)"
    help_links = [{'text': "semu.misc.jupyter_notebook", 'url': "https://github.com/Toni-SM/semu.misc.jupyter_notebook"}]

    async def do_execute(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False):
        """Execute user code
        """
        # https://jupyter-client.readthedocs.io/en/latest/messaging.html#execute
        execute_reply = {"status": "ok", 
                         "execution_count": self.execution_count,
                         "payload": [], 
                         "user_expressions": {}}
        # no code
        if not code.strip():
            return execute_reply
        # magic commands
        if code.startswith('%'):
            # TODO: process magic commands
            pass
        # python code
        try:
            data = await _send_and_recv(code)
            reply_content = json.loads(data)
        except Exception as e:
            # show network error in client
            print("\x1b[0;31m==================================================\x1b[0m")
            print("\x1b[0;31mKernel error at port {}\x1b[0m".format(SOCKET_PORT))
            print(e)
            print("\x1b[0;31m==================================================\x1b[0m")
            reply_content = {"status": "error", "output": "", "traceback": [], "ename": str(type(e).__name__), "evalue": str(e)}

        # code execution stdout: {"status": str, "output": str}
        if not silent:
            if reply_content["output"]:
                stream_content = {"name": "stdout", "text": reply_content["output"]}
                self.send_response(self.iopub_socket, "stream", stream_content)
        reply_content.pop("output", None)

        # code execution error: {"status": str("error"), "output": str, "traceback": list(str), "ename": str, "evalue": str}
        if reply_content["status"] == "error":
            self.send_response(self.iopub_socket, "error", reply_content)

        execute_reply["status"] = reply_content["status"]
        execute_reply["execution_count"] = self.execution_count,  # the base class increments the execution count

        return execute_reply

    def do_debug_request(self, msg):
        return {}




if __name__ == "__main__":
    if sys.path[0] == "":
        del sys.path[0]
    
    # read socket port from file
    if os.path.exists(os.path.join(SCRIPT_DIR, "socket.txt")):
        with open(os.path.join(SCRIPT_DIR, "socket.txt"), "r") as f:
            SOCKET_PORT = int(f.read())
    
    IPKernelApp.launch_instance(kernel_class=EmbeddedKernel)
