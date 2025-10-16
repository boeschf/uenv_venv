# uenv-venv

Create a Python venv layered on top of an active uenv view

- Fails fast if `PYTHONPATH` is set (to avoid broken resolver/imports).
- Prefer `uv venv --seed`, fallback to stdlib `python -m venv`.
- After creation, upgrades `pip/setuptools/wheel` (using `uv pip` if available).
- Writes a `.pth` so the venv can see the uenv’s site-packages.

## Install

```bash
pip install .
# or with uv
uv pip install -e .
```

## Usage

```bash
uenv-venv --venv ~/venvs/myvenv
# or specify the uenv's python explicitly
uenv-venv --venv .venv --python /user-environment/env/<view>/bin/python
source .venv/bin/activate
```

If `PYTHONPATH` is set in your shell, `uenv-venv` will abort with instructions for unsetting it.
