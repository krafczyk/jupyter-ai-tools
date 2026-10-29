"""Regressions for lexical Jupyter API paths and path-keyed document IDs."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from jupyter_server_fileid.manager import ArbitraryFileIdManager
from jupyter_ydoc import YNotebook
from pycrdt import Awareness, Doc

from jupyter_ai_tools import utils
from jupyter_ai_tools.toolkits import jupyterlab, notebook


@pytest.fixture
def lexical_notebook(tmp_path, monkeypatch):
    root = tmp_path / "server"
    target = tmp_path / "storage" / "project"
    root.mkdir()
    target.mkdir(parents=True)
    (root / "linked").symlink_to(target, target_is_directory=True)
    relative = "linked/my notebook.ipynb"
    path = root / relative
    content = {
        "cells": [
            {
                "cell_type": "code",
                "id": "first-cell",
                "source": "print('saved')",
                "metadata": {},
                "outputs": [],
                "execution_count": None,
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(content))
    manager = ArbitraryFileIdManager(root_dir=str(root), db_path=str(tmp_path / "ids.sqlite"))
    file_id = manager.index(relative)
    server = SimpleNamespace(
        root_dir=str(root), web_app=SimpleNamespace(settings={"file_id_manager": manager})
    )
    monkeypatch.setattr(utils, "get_serverapp", lambda: server)
    return SimpleNamespace(
        root=root,
        path=path,
        relative=relative,
        manager=manager,
        file_id=file_id,
        server=server,
        content=content,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["relative", "absolute", "encoded", "dots"])
async def test_existing_file_id_survives_normalization(lexical_notebook, spelling):
    fixture = lexical_notebook
    paths = {
        "relative": fixture.relative,
        "absolute": str(fixture.path),
        "encoded": quote(fixture.relative),
        "dots": "./linked/unused/../my notebook.ipynb",
    }
    before = fixture.manager.con.execute("SELECT id, path FROM Files").fetchall()
    assert fixture.manager.get_id(str(fixture.path.resolve())) is None
    normalized = utils.normalize_filepath(paths[spelling])
    assert normalized == str(fixture.path)
    assert utils.normalize_filepath(normalized) == normalized
    assert await utils.get_file_id(paths[spelling]) == fixture.file_id
    assert await utils.get_file_id(normalized) == fixture.file_id
    assert fixture.manager.con.execute("SELECT id, path FROM Files").fetchall() == before


def test_symlinked_server_root_is_preserved(lexical_notebook, tmp_path):
    fixture = lexical_notebook
    alias = tmp_path / "server-alias"
    alias.symlink_to(fixture.root, target_is_directory=True)
    fixture.server.root_dir = str(alias)
    assert utils.normalize_filepath(fixture.relative) == str(alias / fixture.relative)


def test_cwd_fallback_preserves_lexical_spelling(lexical_notebook, monkeypatch):
    fixture = lexical_notebook

    def unavailable():
        raise RuntimeError("no server context")

    monkeypatch.setattr(utils, "get_serverapp", unavailable)
    monkeypatch.setattr(os, "getcwd", lambda: str(fixture.root))
    assert utils.normalize_filepath(fixture.relative) == str(fixture.path)


def test_dot_segments_follow_lexical_api_not_symlink_target(lexical_notebook):
    fixture = lexical_notebook
    path = fixture.root / "linked" / ".." / "sibling.ipynb"
    assert path.resolve() != fixture.root / "sibling.ipynb"
    assert utils.normalize_filepath(str(path)) == str(fixture.root / "sibling.ipynb")
    assert utils.normalize_filepath("../outside.ipynb") == str(
        fixture.root.parent / "outside.ipynb"
    )


def test_decoding_remains_one_pass(lexical_notebook):
    fixture = lexical_notebook
    assert utils.normalize_filepath("linked/literal%2520.ipynb") == str(
        fixture.root / "linked" / "literal%20.ipynb"
    )


@pytest.mark.asyncio
async def test_callers_reuse_live_document_without_file_fallback(lexical_notebook, monkeypatch):
    fixture = lexical_notebook
    doc: Doc = Doc()
    awareness = Awareness(doc)
    ydoc = YNotebook(doc, awareness)
    ydoc.set(fixture.content)
    awareness.set_local_state({"activeCellId": "first-cell"})
    room = SimpleNamespace(_document=ydoc)
    room_id = f"json:notebook:{fixture.file_id}"
    rooms = SimpleNamespace(
        room_exists=lambda key: key == room_id,
        get_room=AsyncMock(return_value=room),
    )
    fixture.server.web_app.settings["jupyter_server_ydoc"] = SimpleNamespace(
        ywebsocket_server=rooms
    )
    monkeypatch.setattr(utils, "JSD_PRESENT", False)
    monkeypatch.setattr(notebook, "get_active_notebook", AsyncMock(return_value=fixture.relative))
    assert await notebook.get_active_cell_id(quote(fixture.relative)) == "first-cell"
    assert (await notebook.select_cell("first-cell"))["success"]
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(jupyterlab, "execute_command", execute)
    assert (await jupyterlab.run_cell("0"))["success"]
    execute.assert_awaited_once_with("notebook:run-cell")

    saved = fixture.path.read_bytes()
    await notebook.edit_cell(fixture.relative, "first-cell", content="print('unsaved')")
    await notebook.add_cell(fixture.relative, content="print('added')")
    assert ydoc.get_cell(0)["source"] == "print('unsaved')"
    assert ydoc.get_cell(1)["source"] == "print('added')"
    assert fixture.path.read_bytes() == saved
    # These 0.5.2 readers intentionally read disk, even for an open document.
    cell, index = await notebook.read_cell_json(quote(fixture.relative), "0")
    assert index == 0
    assert cell["source"] == "print('saved')"
    assert fixture.manager.con.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 1
