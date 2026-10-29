# MkChad 0.5.2 maintenance

Branch `mkchad-0.5.2` starts at upstream tag `v0.5.2`, commit
`fdbd7e88ac7c168090c028a173d5875a8ccad76d`. Package version
`0.5.2+mkchad.1` changes only path normalization at runtime. It retains the
browser's lexical symlink spelling so the existing path-keyed file-ID registry
can find its collaborative room. It neither creates aliases nor migrates IDs.

## Path contract

`normalize_filepath()` URL-decodes once, anchors relative paths at the server
root (cwd on server-context failure), and uses `os.path.abspath()` for both
relative and absolute paths. Symlinks remain in the result. Dot segments are
collapsed lexically: `link/../file` means the lexical parent, not the symlink
target's parent, consistent with a normalized API path.

This is an identity fix, not a containment or authorization policy. Absolute
paths and `..` inputs were already accepted. Filesystem tools still follow
symlinks when opening files. The helper does not enforce server-root containment
and does not grant access. Do not use it as a security check or register resolved
aliases to make lookup succeed. Alternative spellings do not necessarily identify
the same open document.

The existing one-pass URL decoding is unchanged. Repeated normalization is
idempotent for ordinary decoded paths; names containing literal URL escapes
(for example `%20`) can still decode again in callers that normalize twice.
This pre-existing ambiguity is outside this maintenance patch.

The 0.5.2 read helpers read saved files, even for open notebooks. Add/edit helpers
prefer the live collaborative document. A disk read alone cannot prove live
lookup works; verify an unsaved edit directly in the browser model.

## Deterministic unit verification

Use an isolated Python 3.13 environment, with disposable caches and artifacts
outside the source tree. Install `.[test,lint]`, then run:

```bash
python -m pytest tests -q -o asyncio_default_fixture_loop_scope=function
mypy .
ruff check .
black --check --diff .
```

The path regression uses a temporary symlink and a real
`ArbitraryFileIdManager`. It checks relative, absolute, URL-encoded and dot-segment
inputs, stable registry contents, normalization repeated by callers, symlinked
roots, cwd fallback, and collaborative selection/edit/add behavior. Existing
tests cover ordinary paths, empty input and missing server context.

## Opt-in installed-wheel browser qualification

`tests/smoke_symlink.py` starts its own authenticated loopback Jupyter server and
Chromium browser, with isolated HOME/config/data/runtime/settings and a disposable
file-ID database. It calls the actual dedicated `run_cell` helper via a
test-only, token-authenticated Jupyter handler. It does **not** qualify MCP HTTP
transport or change the user's MCP configuration. The unneeded MCP listener is
disabled in this fixture.

Install these integration dependencies alongside the candidate wheel:

```text
jupyterlab==4.5.9
jupyter-server==2.20.0
jupyter-server-documents==0.2.5+mkchad.2
jupyter-server-fileid==0.9.2
jupyterlab-commands-toolkit==0.1.6
jupyterlab-notebook-awareness==0.2.0
jupyter-collaboration==4.4.0
jupyter-collaboration-ui==2.4.1
jupyter-docprovider==2.4.1
jupyter-server-ydoc==2.4.0
jupyter-ydoc==3.4.1
pycrdt==0.12.50
playwright==1.56.0
```

The custom Server Documents wheel must be provided by the MkChad build workflow;
do not substitute upstream 0.2.5. Its compatibility API remains enabled while the
older `jupyter_server_ydoc` extension is disabled. Notebook-awareness supplies
the `activeCellId` state required by selection and execution. A provisioned
Chromium executable is required; the script does not download one.

```bash
python tests/smoke_symlink.py --work-dir "$BUILD_ROOT/browser-new" \
  --chromium /path/to/chromium
```

Use a new, short work-directory path (Chromium Unix sockets have a path-length
limit). Set the invoking process's `TMPDIR` to an appropriate disposable path as
well. The script rejects an existing work directory and retains private logs and
synthetic notebook evidence there. Do not publish its runtime files or token.

Checks cover open/active awareness; `run_cell` by ID and index with real output,
execution counts and kernel idle state; selection without execution; unsaved
frontend and tool edits; live cell insertion; preserved unrelated outputs and
metadata; provider autosave and fresh-client reopening; and a non-symlink control.
Server Documents owns persistence in this profile. The harness waits for its
autosave; `context.save()` did not settle in the qualification fixture and is
not claimed as qualified. No live user notebook or kernel is used.

## Build and deployment

Use `build==1.6.1` and `hatchling==1.32.4` in a fresh environment. From the pinned
source checkout:

```bash
python -m build --no-isolation --outdir "$BUILD_ROOT/dist"
sha256sum "$BUILD_ROOT"/dist/*
```

This builds the wheel from the generated sdist. Record the full source commit,
version, build toolchain and wheel/sdist SHA-256 in the deployment's build
manifest. Test the **installed wheel**, not an editable source import, using the
browser procedure above. Preserve the existing custom Server Documents package.

Deploy the verified wheel into the **Jupyter server** environment at a coordinated
stopping point, using `pip install --no-deps` with the deployment manifest's hash
checked first. Do not install into notebook kernels. A loaded helper requires a
new server process or a supported server-side extension reload; restarting only
OpenCode or a kernel is insufficient. A server restart can lose kernel variables.

Rollback is the previous pinned `jupyter_ai_tools==0.5.2` wheel plus the same
Server Documents maintenance wheel, followed by coordinated server activation.
Retain both wheels before rollout. Source qualification and wheel creation do not
mean the live service has been updated.
