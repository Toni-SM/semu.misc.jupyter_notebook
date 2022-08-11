from typing import Any, List

import os

from jupyter_client.provisioning import LocalProvisioner
from jupyter_client.connect import KernelConnectionInfo
from jupyter_client.launcher import launch_kernel


class Provisioner(LocalProvisioner):
    async def launch_kernel(self, cmd: List[str], **kwargs: Any) -> KernelConnectionInfo:
        # set paths
        cmd[0] = "/isaac-sim/kit/python/bin/python3" # TODO: replace with the path to the Omniverse python3 
        cmd[1] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "launchers", "ipykernel_launcher.py"))

        scrubbed_kwargs = LocalProvisioner._scrub_kwargs(kwargs)
        self.process = launch_kernel(cmd, **scrubbed_kwargs)
        pgid = None
        if hasattr(os, "getpgid"):
            try:
                pgid = os.getpgid(self.process.pid)
            except OSError:
                pass

        self.pid = self.process.pid
        self.pgid = pgid
        return self.connection_info
