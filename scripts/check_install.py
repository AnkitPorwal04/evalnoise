"""Build a wheel and exercise its CLI and web assets outside the source tree."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="evalnoise-install-") as temporary:
        root = Path(temporary)
        environment = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", str(source),
                        "--wheel-dir", str(root)], check=True, env=environment, cwd=root)
        wheel, = root.glob("evalnoise-*.whl")
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv" / "bin" / "python"
        subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                       check=True, env=environment, cwd=root)
        subprocess.run([str(root / "venv" / "bin" / "evalnoise"), "--help"],
                       check=True, env=environment, cwd=root, stdout=subprocess.DEVNULL)
        probe = '''
import importlib.resources, pathlib, threading, urllib.request
from evalnoise.workbench import server
assets = importlib.resources.files('evalnoise').joinpath('workbench.js').read_text()
assert 'refresh' in assets
root = pathlib.Path('empty-runs'); root.mkdir()
httpd = server(root, 0)
thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
try:
    for path in ('/', '/app.js', '/style.css'):
        with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}{path}') as response:
            assert response.status == 200 and response.read()
finally:
    httpd.shutdown(); httpd.server_close(); thread.join()
'''
        subprocess.run([str(python), "-I", "-c", probe], check=True, env=environment, cwd=root)
        print(json.dumps({"wheel": wheel.name, "isolated_install": "passed",
                          "cli": "passed", "packaged_web_assets": "passed"}))


if __name__ == "__main__":
    main()
