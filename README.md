## Embedded Jupyter Notebook for NVIDIA Omniverse

<hr>

<mark><strong>This extension is deprecated in favor of [omni.isaac.jupyter_notebook](https://docs.omniverse.nvidia.com/isaacsim/latest/advanced_tutorials/tutorial_advanced_code_editors.html#jupyterlab-jupyter-notebook)</strong></mark>

> See more details in [Isaac Sim docs](https://docs.omniverse.nvidia.com/isaacsim/latest/advanced_tutorials/tutorial_advanced_code_editors.html).

<hr>

> This extension can be described as the [Jupyter](https://jupyter.org/) notebook version of Omniverse's [Script Editor](https://docs.omniverse.nvidia.com/extensions/latest/ext_script-editor.html). It allows to open a Jupyter Notebook embedded in the current NVIDIA Omniverse application scope.

<br>

**Target applications:** Any NVIDIA Omniverse app

**Supported OS:** Windows and Linux

**Changelog:** [CHANGELOG.md](exts/semu.misc.jupyter_notebook/docs/CHANGELOG.md)

**Table of Contents:**

- [Extension setup](#setup)
  - [Troubleshooting](#setup-troubleshooting)
- [Extension usage](#usage)
  - [Code autocompletion](#usage-autocompletion)
  - [Code introspection](#usage-introspection)
- [Configuring the extension](#config)
- [Implementation details](#implementation)

<br>

![showcase](exts/semu.misc.jupyter_notebook/data/preview.png)

<hr>

<a name="setup"></a>
### Extension setup

1. Add the extension using the [Extension Manager](https://docs.omniverse.nvidia.com/extensions/latest/ext_extension-manager.html) or by following the steps in [Extension Search Paths](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/extensions_advanced.html#git-url-paths)

    * Git url (git+https) as extension search path
    
        ```
        git+https://github.com/Toni-SM/semu.misc.jupyter_notebook.git?branch=main&dir=exts
        ```

    * Compressed (.zip) file for import

        [semu.misc.jupyter_notebook.zip](https://github.com/Toni-SM/semu.misc.jupyter_notebook/releases)

2. Enable the extension using the [Extension Manager](https://docs.omniverse.nvidia.com/extensions/latest/ext_extension-manager.html) or by following the steps in [Extension Enabling/Disabling](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/extensions_advanced.html#extension-enabling-disabling)

<a name="setup-troubleshooting"></a>
#### Troubleshooting

* Failed installation (particularly in Kit 105 based applications - Python 3.10)

  **Issues/Errors:**

  ```ini
  [Warning] [omni.kit.pipapi.pipapi] 'jupyterlab' failed to install.
  [Warning] [omni.kit.pipapi.pipapi] 'notebook' failed to install.
  ```

  **Solution:**

  Upgrade `pip` to the latest version and install required libraries manually. Replace `<USER>`, `<OMNIVERSE_APP>`, `<APP_NAME>`, and `<APP_VERSION>` according to your system configuration. Example:
  
  - `<USER>`: toni
  - `<OMNIVERSE_APP>`: create-2023.1.1
  - `<APP_NAME>`: USD.Composer
  - `<APP_VERSION>`: 2023.1

  <br>

  Linux

  ```xml
  /home/<USER>/.local/share/ov/pkg/<OMNIVERSE_APP>/kit/python/bin/python3 -m pip install --upgrade pip
  /home/<USER>/.local/share/ov/pkg/<OMNIVERSE_APP>/kit/python/bin/python3 -m pip --isolated install --upgrade --target=/home/<USER>/.local/share/ov/data/Kit/<APP_NAME>/<APP_VERSION>/pip3-envs/default jupyterlab notebook jedi
  ```

  Windows

  ```xml
  C:\Users\<USER>\AppData\Local\ov\pkg\<OMNIVERSE_APP>\kit\python\python.exe -m pip install --upgrade pip
  C:\Users\<USER>\AppData\Local\ov\pkg\<OMNIVERSE_APP>\kit\python\python.exe -m pip --isolated install --upgrade --target=C:\Users\<USER>\AppData\Local\ov\data\Kit\<APP_NAME>\<APP_VERSION>\pip3-envs\default jupyterlab notebook jedi
  ```

<hr>

<a name="usage"></a>
### Extension usage

#### Omniverse app

Enabling the extension launches the Jupyter Notebook server ([JupyterLab](https://jupyterlab.readthedocs.io/en/stable/) or [Jupyter Notebook](https://jupyter-notebook.readthedocs.io/en/latest/)) in the background. The notebook can then be opened in the browser via its URL (`http://WORKSTATION_IP:PORT/`), which is also indicated inside the Omniverse application in the *Windows > Embedded Jupyter Notebook* menu.

> **Note:** The Jupyter Notebook URL port may change if the configured port is already in use.

<br>
<p align="center">
  <img src="exts/semu.misc.jupyter_notebook/data/preview1.png" width="75%">
</p>

Disabling the extension shutdowns the Jupyter Notebook server and the openened kernels.

#### Jupyter Notebook

To execute Python code in the current NVIDIA Omniverse application scope use the following kernel: 

<br>
<table align="center" class="table table-striped table-bordered">
  <thead>
  </thead>
  <tbody>
    <tr>
      <td>Embedded Omniverse (Python 3)</td>
      <td><p align="center" style="margin: 0"><img src="exts/semu.misc.jupyter_notebook/data/kernels/embedded_omniverse_python3_socket/logo-64x64.png" width="50px"></p></td>
    </tr>
  </tbody>
</table>

<a name="usage-autocompletion"></a>
##### Code autocompletion

Use the <kbd>Tab</kbd> key for code autocompletion.

<a name="usage-introspection"></a>
##### Code introspection 

Use the <kbd>Ctrl</kbd> + <kbd>i</kbd> keys for code introspection (display *docstring* if available).

<hr>

<a name="config"></a>
### Configuring the extension

The extension can be configured by editing the [config.toml](exts/semu.misc.jupyter_notebook/config/extension.toml) file under `[settings]` section. The following parameters are available:

<br>

**Extension settings**

<table class="table table-striped table-bordered">
  <thead>
    <tr>
      <th>Parameter</th>
      <th>Value</th>
      <th>Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>socket_port</td>
      <td>8224</td>
      <td>The port on which the Jupyter Notebook server will be listening for connections</td>
    </tr>
    <tr>
      <td>classic_notebook_interface</td>
      <td>false</td>
      <td>Whether the Jupyter Notebook server will use the JupyterLab interface (default interface) or the classic Jupyter Notebook interface</td>
    </tr>
    <tr>
      <td>kill_processes_with_port_in_use</td>
      <td>true</td>
      <td>Whether to kill applications/processes that use the same ports (8224 and 8225 by default) before activating the extension. Disable this option if you want to launch multiple applications that have this extension active</td>
    </tr>
  </tbody>
</table>

<br>

**Jupyter Notebook server settings**

<table class="table table-striped table-bordered">
  <thead>
    <tr>
      <th>Parameter</th>
      <th>Value</th>
      <th>Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>notebook_ip</td>
      <td>"0.0.0.0"</td>
      <td>The IP address on which the Jupyter Notebook server will be launched</td>
    </tr>
    <tr>
      <td>notebook_port</td>
      <td>8225</td>
      <td>The port on which the Jupyter Notebook server will be launched. If the port is already in use, the server will be launched on a different incrementing port</td>
    </tr>
    <tr>
      <td>token</td>
      <td>""</td>
      <td>The Jupyter Notebook server token. If empty, the default configuration, the server will be launched without authentication</td>
    </tr>
    <tr>
      <td>notebook_dir</td>
      <td>""</td>
      <td>The Jupyter Notebook server directory</td>
    </tr>
    <tr>
      <td>command_line_options</td>
      <td>"--allow-root --no-browser"</td>
      <td>The Jupyter Notebook server command line options excluding the previously mentioned parameters</td>
    </tr>
  </tbody>
</table>

<hr>

<a name="implementation"></a>
### Implementation details

Both the Jupyter Notebook server and the IPython kernels are designed to be launched as independent processes (or subprocesses). Due to this specification, the Jupyter Notebook server and the IPython kernels are launched in separate (sub)processes.

<br>
<table class="table table-striped table-bordered">
  <thead>
    <tr>
      <th></th>
      <th>Jupyter Notebook as (sub)process</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Kernel (display name)</td>
      <td>Embedded Omniverse (Python 3)</td>
    </tr>
    <tr>
      <td>Kernel (logo)</td>
      <td><p align="center"><img src="exts/semu.misc.jupyter_notebook/data/kernels/embedded_omniverse_python3_socket/logo-64x64.png" width="50px"></p></td>
    </tr>
    <tr>
      <td>Kernel (raw name)</td>
      <td>embedded_omniverse_python3_socket</td>
    </tr>
    <tr>
      <td>Instanceable kernels</td>
      <td>Unlimited</td>
    </tr>
    <tr>
      <td>Python backend</td>
      <td>Omniverse Kit embedded Python</td>
    </tr>
    <tr>
      <td>Code execution</td>
      <td>Intercept Jupyter-IPython communication, forward and execute code in Omniverse Kit and send back the results to the published by the notebook</td>
    </tr>
    <tr>
      <td>Main limitations</td>
      <td>
        <ul>
          <li>IPython magic commands are not available</li>
          <li>Printing, inside callbacks, is not displayed in the notebook but in the Omniverse terminal</li>
          <li>Matplotlib plotting is not available in notebooks</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>
