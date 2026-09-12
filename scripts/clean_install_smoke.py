"""Install the built wheel outside the checkout and smoke CLI/Web twice."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--reuse-root", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    root = (
        args.reuse_root.resolve(strict=True)
        if args.reuse_root
        else Path(tempfile.mkdtemp(prefix="dca-clean-install-")).resolve()
    )
    if not root.is_relative_to(
        Path(tempfile.gettempdir()).resolve()
    ) or not root.name.startswith("dca-clean-install-"):
        raise ValueError("Only an isolated smoke directory below TEMP may be reused")
    environment = root / "venv"
    if not args.reuse_root:
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "OPENAI_", "ZHIPU_", "GLM_", "PYTHONPATH"))
    }
    child_env["PYTHONIOENCODING"] = "utf-8"

    def run(arguments: list[str], name: str, timeout: int = 120) -> str:
        result = subprocess.run(
            [str(python), *arguments],
            cwd=root,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        (root / f"{name}.txt").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        if result.returncode:
            raise RuntimeError(f"{name} failed; inspect isolated log at {root}")
        return result.stdout

    print(f"clean_install_root={root}", flush=True)
    if args.reuse_root:
        run(
            ["-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)],
            "reinstall",
        )
    else:
        run(["-m", "pip", "install", f"{wheel}[web]", "httpx>=0.28,<1"], "install", 600)
    run(["-m", "pip", "check"], "dependencies")
    run(["-m", "context_agent", "--help"], "cli")
    script = """
import json, sys
from pathlib import Path
import context_agent
from context_agent.config import AppConfig, ProviderConfig
from context_agent.web import create_app
from fastapi.testclient import TestClient
origin = Path(context_agent.__file__).resolve()
assert origin.is_relative_to(Path(sys.prefix).resolve()), str(origin)
root = Path.cwd()
config = AppConfig(project_root=root, workspace=root/'workspace',
                   context_root=root/'workspace', data_dir=root/'data')
provider = ProviderConfig(name='lmstudio', model='fixture',
                         base_url='http://127.0.0.1:9/v1', api_key='local')
for repeat in range(2):
    with TestClient(create_app(config, (provider,))) as client:
        health = client.get('/api/health')
        assert health.status_code == 200, health.text
        page = client.get('/')
        assert page.status_code == 200 and 'chat-job-status' in page.text
        js = client.get('/static/app.js')
        assert js.status_code == 200 and len(js.content) > 1000
        assert client.get('/api/jobs').status_code == 200
print(json.dumps({'origin_in_clean_venv': True, 'web_restart_passes': 2}))
"""
    result = json.loads(run(["-c", script], "web"))
    result.update({"wheel": wheel.name, "pip_check": "PASS", "cli": "PASS"})
    (root / "acceptance.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
