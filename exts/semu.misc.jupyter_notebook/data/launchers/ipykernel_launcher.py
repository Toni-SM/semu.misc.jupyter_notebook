import os
import sys
import time
import json
import types
import socket
import asyncio


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


from ipykernel.jsonutil import json_clean
from ipykernel.kernelapp import IPKernelApp as _IPKernelApp


async def execute_request(self, stream, ident, parent):
    async def send_and_recv(message):
        reader, writer = await asyncio.open_connection(host="127.0.0.1", 
                                                       port=SOCKET_PORT,
                                                       family=socket.AF_INET)
        writer.write(message.encode())
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        return data.decode()
    
    try:
        content = parent["content"]
        code = content["code"]
        silent = content["silent"]
        store_history = content.get("store_history", not silent)
        user_expressions = content.get("user_expressions", {})
        allow_stdin = content.get("allow_stdin", False)
    except Exception:
        self.log.error("Got bad msg: ")
        self.log.error("%s", parent)
        return

    stop_on_error = content.get("stop_on_error", True)
    metadata = self.init_metadata(parent)

    if not silent:
        self.execution_count += 1
        self._publish_execute_input(code, parent, self.execution_count)

    try:
        data = await send_and_recv(code)
        reply_content = json.loads(data)
    except Exception as e:
        print('\x1b[0;31m==================================================\x1b[0m')
        print("\x1b[0;31mKernel error at port {}\x1b[0m".format(SOCKET_PORT))
        print(e)
        print('\x1b[0;31m==================================================\x1b[0m')
        return

    if reply_content["output"]:
        print(reply_content["output"])
    reply_content.pop("output", None)

    reply_content.update({"execution_count": self.execution_count,
                          "user_expressions": {},
                          "payload": []})
    if reply_content["status"] == "error":
        reply_content.update({"engine_info": {"engine_uuid": self.ident, 
                                              "engine_id": self.int_id, 
                                              "method": "execute"}})
        print('\x1b[0;31m--------------------------------------------------\x1b[0m')
        for traceback_line in reply_content["traceback"]:
            traceback_line = traceback_line.replace(reply_content["ename"], 
                                                    "\x1b[0;31m{}\x1b[0m".format(reply_content["ename"]))
            if traceback_line.startswith("Traceback"):
                print(traceback_line)
            else:
                print("Traceback (most recent call last) " + traceback_line)
        
    sys.stdout.flush()
    sys.stderr.flush()

    if self._execute_sleep:
        time.sleep(self._execute_sleep)

    reply_content = json_clean(reply_content)
    metadata = self.finish_metadata(parent, metadata, reply_content)

    reply_msg = self.session.send(stream, "execute_reply", reply_content, parent, 
                                  metadata=metadata, ident=ident)

    self.log.debug("%s", reply_msg)

    if not silent and reply_msg["content"]["status"] == "error" and stop_on_error:
        self._abort_queues()


class IPKernelApp(_IPKernelApp):
    def initialize(self, argv=None):
        super().initialize(argv)

    def start(self):
        self.kernel.shell_handlers["execute_request"] = types.MethodType(execute_request, self.kernel)
        self.kernel.execute_request = types.MethodType(execute_request, self.kernel)
        super().start()


def launch_instance(cls, argv=None, **kwargs):
    app = cls.instance()
    app.initialize(argv)
    app.start()




if __name__ == "__main__":
    if sys.path[0] == "":
        del sys.path[0]
    
    # read socket port from file
    if os.path.exists(os.path.join(SCRIPT_DIR, "socket.txt")):
        with open(os.path.join(SCRIPT_DIR, "socket.txt"), "r") as f:
            SOCKET_PORT = int(f.read())
    
    launch_instance(IPKernelApp)
