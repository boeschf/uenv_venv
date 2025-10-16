from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def _err(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def ensure_no_pythonpath():
    pp = os.environ.get("PYTHONPATH")
    if pp:
        msg = dedent(f"""
        ERROR: PYTHONPATH is set in your environment and will break venv tooling.

          PYTHONPATH={pp}

        Please unset it and rerun:

          # bash/zsh:
          unset PYTHONPATH

          # fish:
          set -e PYTHONPATH

          # csh/tcsh:
          unsetenv PYTHONPATH

        (This is intentional: uenv-venv refuses to proceed while PYTHONPATH is set.)
        """).strip()
        print(msg, file=sys.stderr)
        sys.exit(2)


def parse_uenv() -> tuple[Path, str, str]:
    uv = os.environ.get("UENV_VIEW", "")
    if uv:
        parts = uv.split(":", 3)
        if len(parts) == 3:
            mount, name, view = parts
            return Path(mount), name, view
    uml = os.environ.get("UENV_MOUNT_LIST", "")
    if uml:
        # take last "<squashfs>:<mount>" entry
        for token in reversed(re.split(r"[ ,]", uml.strip())):
            if token and ":" in token:
                mount = token.rsplit(":", 1)[-1]
                return Path(mount), "", ""
    _err("ERROR: Could not detect uenv.")


def py_in_uenv(py: Path, mount: Path) -> bool:
    try:
        return Path(py).resolve().as_posix().startswith(Path(mount).resolve().as_posix() + "/")
    except Exception:
        return False


def discover_uenv_site_packages(mount: Path, view_name: str, py: Path) -> Path:
    """
    Prefer the uenv view site-packages by scanning the interpreter's sys.path
    for entries under: <mount>/env/<view_name>/lib/pythonX.Y/site-packages
    If not found, fall back to the deterministic path and verify it exists.
    """
    # Ask the chosen Python for version and sys.path
    code = r"""
import sys, json
pyver = f"{sys.version_info[0]}.{sys.version_info[1]}"
print(json.dumps({"ver": pyver, "path": sys.path}))
"""
    out = subprocess.check_output([str(py), "-c", code]).decode()
    data = __import__("json").loads(out)
    pyver = data["ver"]
    sys_path = [Path(p).resolve() for p in data["path"] if isinstance(p, str)]

    # Target prefix inside the view
    want_prefix = (mount / "env" / view_name / "lib" / f"python{pyver}" / "site-packages").resolve()

    # Prefer the first sys.path entry that is inside the view's site-packages
    for p in sys_path:
        try:
            pr = p.resolve()
        except Exception:
            continue
        if pr.is_dir() and pr.as_posix().startswith(want_prefix.as_posix()):
            return pr

    # Fallback: use the deterministic view path if it exists
    if want_prefix.is_dir():
        return want_prefix

    _err(
        dedent(
            f"""
            ERROR: Could not locate the uenv view's site-packages.
              looked for: {want_prefix}
            Hint: ensure the uenv is active and exposes its view on sys.path.
            """
        ).strip()
    )


def venv_site_packages(venv_python: Path) -> Path:
    code = "import sysconfig;print(sysconfig.get_paths()['purelib'])"
    out = subprocess.check_output([str(venv_python), "-c", code]).decode().strip()
    return Path(out)


def _venv_python(target: Path) -> Path:
    return target / "bin/python"


def upgrade_bootstrap(venv_python: Path, env: dict) -> None:
    """
    Ensure pip/setuptools/wheel are present and up-to-date in the given venv.
    Prefer `uv pip` if available, otherwise use `pip`.
    """
    uv = shutil.which("uv")

    # Try to ensure pip exists first (harmless if already present)
    try:
        subprocess.check_call([str(venv_python), "-m", "ensurepip", "--upgrade"], env=env)
    except Exception:
        pass

    if uv:
        # Use uv against the venv explicitly
        subprocess.check_call(
            [uv, "pip", "install", "-p", str(venv_python), "-U", "pip", "setuptools", "wheel"],
            env=env,
        )
    else:
        # Fallback to pip inside the venv
        subprocess.check_call(
            [str(venv_python), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"], env=env
        )


def create_with_uv(target: Path, py: Path, copies: bool, env: dict) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise FileNotFoundError
    cmd = [uv, "venv", str(target), "--python", str(py), "--seed"]
    if copies:
        cmd.append("--copies")
    subprocess.check_call(cmd, env=env)
    upgrade_bootstrap(_venv_python(target), env)


def create_with_stdlib(target: Path, py: Path, copies: bool, env: dict) -> None:
    cmd = [str(py), "-m", "venv", str(target)]
    if copies:
        cmd.append("--copies")
    subprocess.check_call(cmd, env=env)
    upgrade_bootstrap(_venv_python(target), env)


def main():
    ap = argparse.ArgumentParser(description="Create a venv layered on an active uenv.")
    ap.add_argument("--venv", required=True, type=Path, help="Target venv directory")
    ap.add_argument(
        "--python",
        default=sys.executable,
        type=Path,
        help="Python to seed the venv (must be inside the uenv)",
    )
    ap.add_argument("--force", action="store_true", help="Remove existing venv if present")
    ap.add_argument("--copies", action="store_true", help="Use file copies instead of symlinks")
    args = ap.parse_args()

    mount, name, view = parse_uenv()

    if not name:
        _err("ERROR: Could not detect uenv name.")
    if not mount.is_dir():
        _err(f"ERROR: mount point does not exist: {mount}")
    if not view:
        _err("ERROR: Could not detect active view.")

    # Enforce: interpreter must live inside the uenv mount
    py = args.python.resolve()
    if not py.exists():
        _err(f"ERROR: --python not found: {py}")
    if not py_in_uenv(py, mount):
        _err(
            dedent(
                f"""
                ERROR: Selected Python is not inside the uenv mount.
                  python: {py}
                  mount:  {mount}

                Hint: pass --python {mount}/env/{view}/bin/python
                """
            ).strip()
        )

    # Compute uenv site-packages path
    uenv_sp = discover_uenv_site_packages(mount, view, py)
    if not uenv_sp.is_dir():
        _err(f"ERROR: uenv site-packages not found: {uenv_sp}")

    ensure_no_pythonpath()

    # Prepare venv dir
    if args.force and args.venv.exists():
        shutil.rmtree(args.venv)
    if args.venv.exists() and any(args.venv.iterdir()):
        _err(f"ERROR: venv directory exists and is not empty: {args.venv} (use --force)")
    args.venv.mkdir(parents=True, exist_ok=True)

    # Create venv
    env = os.environ
    try:
        create_with_uv(args.venv, py, args.copies, env)
        used = "uv"
    except Exception:
        create_with_stdlib(args.venv, py, args.copies, env)
        used = "venv"

    # Compute venv python and its site-packages
    vpy = _venv_python(args.venv)
    vsp = venv_site_packages(vpy)

    # Write .pth so venv sees uenv’s site-packages
    pth = vsp / "uenv.pth"
    pth.write_text(str(uenv_sp) + "\n")

    # Done
    act = args.venv / "bin/activate"
    print(f"uenv-venv created with {used}")
    print(f"  venv:                 {args.venv}")
    print(f"  python:               {py}")
    print(f"  uenv mount/name/view: {mount} / {name} / {view}")
    print(f"  uenv site-pkgs:       {uenv_sp}")
    print(f"  venv site-pkgs:       {vsp}")
    print(f"  wrote:                {pth}")
    print()
    print("Activate with:")
    print(f"source {act}")

    return 0


if __name__ == "__main__":
    main()
