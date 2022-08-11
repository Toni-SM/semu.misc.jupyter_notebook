import sys
import time
import json
import types
import socket

from ipykernel.jsonutil import json_clean
from ipykernel.kernelapp import IPKernelApp as _IPKernelApp


async def execute_request(self, stream, ident, parent):
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

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.connect(("127.0.0.1", 8224))
            s.sendall(str(code).encode("utf-8"))
            reply_content = json.loads(s.recv(2048).decode("utf-8"))
        except Exception as e:
            print(e)
            return
    
    print(reply_content["output"])
    reply_content.pop("output", None)

    reply_content.update({"execution_count": self.execution_count,
                          "user_expressions": {},
                          "payload": []})
    if reply_content["status"] == "error":
        reply_content.update({"engine_info": {"engine_uuid": self.ident, 
                                              "engine_id": self.int_id, 
                                              "method": "execute"}})
        print(reply_content["traceback"][0])
    
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
    # await _Kernel.execute_request(self, stream, ident, parent)


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

# app.init_signal = types.MethodType(_init_signal, app)

if __name__ == "__main__":
    if sys.path[0] == "":
        del sys.path[0]
    
    launch_instance(IPKernelApp)
