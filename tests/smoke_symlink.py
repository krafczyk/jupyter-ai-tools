"""Opt-in installed-wheel browser qualification; see docs/mkchad-maintenance.md.

Run with --work-dir pointing to a nonexistent disposable directory and --chromium
pointing to a provisioned browser. Creates isolated Jupyter state, a synthetic
notebook and an authenticated test-only tool endpoint. Raises on failed checks,
leaves evidence in the work directory, and shuts down only its own server.
"""

import argparse
import asyncio
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import nbformat
from playwright.async_api import async_playwright

TOOL_CONFIG = """
import sys
from types import ModuleType
from jupyter_server.base.handlers import APIHandler
from tornado.web import authenticated
from jupyter_ai_tools.toolkits import notebook, jupyterlab

class SmokeToolHandler(APIHandler):
    @authenticated
    async def post(self):
        request = self.get_json_body()
        tools = {
            'get_open_documents': notebook.get_open_documents,
            'get_active_notebook': notebook.get_active_notebook,
            'get_active_cell_id': notebook.get_active_cell_id,
            'select_cell': notebook.select_cell,
            'run_cell': jupyterlab.run_cell,
            'edit_cell': notebook.edit_cell,
            'add_cell': notebook.add_cell,
            'read_cell_json': notebook.read_cell_json,
        }
        result = await tools[request['name']](**request.get('arguments', {}))
        self.finish({'result': result})

service = ModuleType('_symlink_smoke_service')
service.default_handlers = [('/_smoke_tools', SmokeToolHandler)]
sys.modules[service.__name__] = service
c.ServerApp.extra_services = [service.__name__]
c.ServerApp.jpserver_extensions = {
    'jupyterlab': True, 'jupyter_ai_tools': True,
    'jupyter_server_documents': True, 'jupyter_server_fileid': True,
    'jupyterlab_commands_toolkit': True,
    'jupyter_server_mcp': False, 'jupyter_server_ydoc': False,
}
c.LabApp.expose_app_in_browser = True
c.LabApp.extension_manager = 'readonly'
c.FileIdExtension.file_id_manager_class = 'jupyter_server_fileid.manager.ArbitraryFileIdManager'
"""


async def _qualify(base, token, env, root, chromium):
    headers = {"Authorization": "token " + token}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30) as client:

        async def tool(name, **arguments):
            response = await client.post(
                "/_smoke_tools", json={"name": name, "arguments": arguments}
            )
            response.raise_for_status()
            return response.json()["result"]

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=chromium, headless=True, env=env
            )
            try:
                page = await browser.new_page(extra_http_headers=headers)
                await page.goto(base + "/lab/tree/linked/smoke%20notebook.ipynb")
                await page.wait_for_function(
                    "window.jupyterapp?.shell.currentWidget?.sessionContext?.session?.kernel"
                    "?.status === 'idle'",
                    timeout=60000,
                )
                model = "window.jupyterapp.shell.currentWidget"
                await page.evaluate(f"{model}.content.activeCellIndex = 0")
                await page.locator(".jp-CodeCell .jp-InputArea").first.click()
                expected_path = "linked/smoke notebook.ipynb"
                deadline = time.monotonic() + 30
                while await tool("get_active_notebook") != expected_path:
                    assert time.monotonic() < deadline, "global awareness did not converge"
                    await asyncio.sleep(0.2)
                assert expected_path in await tool("get_open_documents")
                deadline = time.monotonic() + 30
                while await tool("get_active_cell_id", notebook_path=expected_path) != "first-cell":
                    if time.monotonic() >= deadline:
                        state = await page.evaluate(
                            f"({{index:{model}.content.activeCellIndex, "
                            f"cells:{model}.content.widgets.map(c => c.model.id), "
                            f"awareness:Array.from({model}.model.sharedModel.awareness.getStates())}})"
                        )
                        raise AssertionError(f"cell awareness did not converge: {state}")
                    await asyncio.sleep(0.2)
                print("PASS: lexical open-document and active-cell awareness", flush=True)

                for cell_id, index, text in [
                    ("first-cell", 0, "first executed"),
                    ("1", 1, "second executed"),
                ]:
                    result = await tool("run_cell", cell_id=cell_id, timeout=10)
                    assert result.get("success"), result
                    await page.locator(".jp-OutputArea-output").filter(has_text=text).wait_for()
                    await page.wait_for_function(
                        f"{model}.content.widgets[{index}].model.executionCount !== null && "
                        f"{model}.sessionContext.session.kernel.status === 'idle'"
                    )
                    print(f"PASS: run_cell({cell_id!r}) produced {text!r}", flush=True)
                assert await page.evaluate(
                    f"{model}.content.widgets.map(cell => cell.model.executionCount)"
                ) == [1, 2, None]
                before = await page.evaluate(
                    f"{model}.content.widgets.slice(0,2).map(cell => cell.model.toJSON())"
                )
                assert (await tool("select_cell", cell_id="first-cell"))["success"]
                await page.wait_for_function(f"{model}.content.activeCellIndex === 0")
                print("PASS: selected another cell without execution", flush=True)

                # Make an unsaved frontend edit, then amend that same live cell by ID.
                await page.evaluate(
                    f"{model}.content.widgets[2].model.sharedModel"
                    ".setSource('unsaved browser edit')"
                )
                print("PASS: unsaved browser edit", flush=True)
                await tool(
                    "edit_cell",
                    file_path=expected_path,
                    cell_id="third-cell",
                    content="unsaved tool edit",
                )
                await page.wait_for_function(
                    f"{model}.content.widgets[2].model.sharedModel.getSource()"
                    " === 'unsaved tool edit'"
                )
                print("PASS: tool edit reached live browser model", flush=True)
                await tool("add_cell", file_path=expected_path, content="added through live tool")
                await page.wait_for_function(f"{model}.content.widgets.length === 4")
                print("PASS: tool add reached live browser model", flush=True)
                assert (
                    await page.evaluate(f"{model}.content.widgets[3].model.sharedModel.getSource()")
                    == "added through live tool"
                )
                assert (
                    await page.evaluate(
                        f"{model}.content.widgets.slice(0,2).map(cell => cell.model.toJSON())"
                    )
                    == before
                )
                deadline = time.monotonic() + 30
                while True:
                    saved = nbformat.read(
                        root / "storage/project/smoke notebook.ipynb", as_version=4
                    )
                    if (
                        len(saved.cells) == 4
                        and saved.cells[2].source == "unsaved tool edit"
                        and saved.cells[3].source == "added through live tool"
                    ):
                        break
                    assert time.monotonic() < deadline, "provider autosave did not converge"
                    await asyncio.sleep(0.2)
                print("PASS: live edit/add preserves other cells and saves", flush=True)
                cell, _ = await tool(
                    "read_cell_json", file_path=expected_path, cell_id="third-cell"
                )
                assert "".join(cell["source"]) == "unsaved tool edit"

                # New client and ordinary-path control, sharing the isolated service.
                await page.close()
                page = await browser.new_page(extra_http_headers=headers)
                await page.goto(base + "/lab/tree/linked/smoke%20notebook.ipynb")
                await page.wait_for_function(
                    f"{model}?.content?.widgets?.length === 4", timeout=60000
                )
                await page.locator(".jp-OutputArea-output").filter(
                    has_text="second executed"
                ).wait_for()
                print("PASS: saved outputs survive a new client", flush=True)
                await page.goto(base + "/lab/tree/plain.ipynb")
                await page.wait_for_function(
                    f"{model}?.context?.path === 'plain.ipynb' && "
                    f"{model}.sessionContext?.session?.kernel?.status === 'idle'",
                    timeout=60000,
                )
                await page.locator(".jp-Notebook:visible .jp-CodeCell .jp-InputArea").first.click()
                deadline = time.monotonic() + 30
                while await tool("get_active_notebook") != "plain.ipynb":
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.2)
                assert (await tool("run_cell", cell_id="0", timeout=10)).get("success")
                await page.locator(".jp-OutputArea-output").filter(
                    has_text="plain executed"
                ).wait_for()
                print(
                    "PASS: symlink awareness, ID/index run_cell, selection, live edits/add, "
                    "preserved outputs, save/reopen, and non-symlink execution"
                )
            finally:
                await browser.close()


def main():
    """Run the opt-in smoke CLI; fail if state exists or qualification fails.

    Arguments are --work-dir (new private artifact directory) and --chromium
    (existing Chromium executable). All files and the owned server are isolated;
    no existing environment is installed into or restarted. Errors propagate.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--chromium", required=True)
    args = parser.parse_args()
    root = args.work_dir.absolute()
    root.mkdir(mode=0o700)
    env = os.environ.copy()
    for key in [
        "HOME",
        "JUPYTER_CONFIG_DIR",
        "JUPYTER_DATA_DIR",
        "JUPYTER_RUNTIME_DIR",
        "JUPYTERLAB_SETTINGS_DIR",
        "JUPYTERLAB_WORKSPACES_DIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "IPYTHONDIR",
        "TMPDIR",
    ]:
        path = root / key.lower()
        path.mkdir(mode=0o700)
        env[key] = str(path)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    token = secrets.token_urlsafe(32)
    env["JUPYTER_TOKEN"] = token
    server_root = root / "server"
    server_root.mkdir()
    target = root / "storage/project"
    target.mkdir(parents=True)
    (server_root / "linked").symlink_to(target, target_is_directory=True)
    cells = [
        nbformat.v4.new_code_cell("print('first executed')", id="first-cell"),
        nbformat.v4.new_code_cell("print('second executed')", id="second-cell"),
        nbformat.v4.new_code_cell("raise RuntimeError('must not execute')", id="third-cell"),
    ]
    metadata = {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}}
    nbformat.write(
        nbformat.v4.new_notebook(cells=cells, metadata=metadata), target / "smoke notebook.ipynb"
    )
    nbformat.write(
        nbformat.v4.new_notebook(
            cells=[nbformat.v4.new_code_cell("print('plain executed')")], metadata=metadata
        ),
        server_root / "plain.ipynb",
    )
    config = root / "config.py"
    config.write_text(
        TOOL_CONFIG + f"\nc.BaseFileIdManager.db_path = {str(root / 'ids.sqlite')!r}\n"
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with (root / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "jupyterlab",
                "--no-browser",
                f"--config={config}",
                "--ServerApp.ip=127.0.0.1",
                f"--ServerApp.port={port}",
                "--ServerApp.port_retries=0",
                f"--ServerApp.root_dir={server_root}",
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(headers={"Authorization": "token " + token}) as client:
                deadline = time.monotonic() + 60
                while True:
                    assert process.poll() is None, "isolated server exited; inspect private log"
                    try:
                        if client.get(base + "/api/status", timeout=2).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "server startup timeout"
                    time.sleep(0.2)
            asyncio.run(_qualify(base, token, env, root, args.chromium))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
