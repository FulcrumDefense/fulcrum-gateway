import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "operator_qa_ci.py"


def _run_script(tmp_path, *, env):
    full_env = os.environ.copy()
    full_env.update(env)
    full_env["PATH"] = f"{tmp_path}:{full_env['PATH']}"
    full_env["AX_QA_ARTIFACT_DIR"] = str(tmp_path / "artifacts")
    full_env["AX_QA_CONFIG_DIR"] = str(tmp_path / "ax-config")
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=full_env,
        check=False,
    )


def _write_fake_axctl(tmp_path, *, matrix_ok=True):
    script = tmp_path / "axctl"
    script.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] == ["auth", "doctor"]:
    env = args[args.index("--env") + 1]
    space = args[args.index("--space-id") + 1]
    print(json.dumps({{"ok": True, "selected_env": env, "effective": {{"space_id": space}}}}))
    raise SystemExit(0)

if args[:2] == ["qa", "preflight"]:
    env = args[args.index("--env") + 1]
    space = args[args.index("--space-id") + 1]
    artifact = Path(args[args.index("--artifact") + 1])
    payload = {{"ok": True, "environment": env, "space_id": space, "preflight": {{"passed": True}}, "checks": []}}
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(payload))
    print(json.dumps(payload))
    raise SystemExit(0)

if args[:2] == ["qa", "matrix"]:
    artifact_dir = Path(args[args.index("--artifact-dir") + 1])
    payload = {{"ok": {str(matrix_ok)}, "envs": []}}
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "matrix.json").write_text(json.dumps(payload))
    print(json.dumps(payload))
    raise SystemExit(0 if payload["ok"] else 1)

raise SystemExit("unexpected command: " + " ".join(args))
"""
    )
    script.chmod(0o755)


def test_operator_qa_ci_skips_without_config(tmp_path):
    result = _run_script(tmp_path, env={"AX_QA_ENVS": "dev,next"})

    assert result.returncode == 3, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["skipped"] is True
    assert payload["configured_envs"] == []
    assert (tmp_path / "artifacts" / "operator-qa-summary.json").exists()


def test_operator_qa_ci_can_fail_closed_when_required_config_is_missing(tmp_path):
    result = _run_script(tmp_path, env={"AX_QA_ENVS": "dev", "AX_QA_REQUIRE_MATRIX": "true"})

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["skipped"] is True
    assert payload["skipped_envs"][0]["missing"] == [
        "AX_QA_DEV_TOKEN",
        "AX_QA_DEV_BASE_URL",
        "AX_QA_DEV_SPACE_ID",
    ]


def test_operator_qa_ci_runs_doctor_preflight_matrix_and_writes_artifacts(tmp_path):
    _write_fake_axctl(tmp_path)

    result = _run_script(
        tmp_path,
        env={
            "AX_QA_ENVS": "dev,next",
            "AX_QA_DEV_TOKEN": "axp_u_dev.secret",
            "AX_QA_DEV_BASE_URL": "https://dev.paxai.app",
            "AX_QA_DEV_SPACE_ID": "dev-space",
            "AX_QA_NEXT_TOKEN": "axp_u_next.secret",
            "AX_QA_NEXT_BASE_URL": "https://next.paxai.app",
            "AX_QA_NEXT_SPACE_ID": "next-space",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["configured_envs"] == ["dev", "next"]
    assert (tmp_path / "artifacts" / "dev-doctor.json").exists()
    assert (tmp_path / "artifacts" / "dev-preflight.json").exists()
    assert (tmp_path / "artifacts" / "next-doctor.json").exists()
    assert (tmp_path / "artifacts" / "next-preflight.json").exists()
    assert (tmp_path / "artifacts" / "matrix" / "matrix.json").exists()
    assert (tmp_path / "artifacts" / "operator-qa-summary.json").exists()
    assert "axp_u_dev.secret" not in result.stdout
    assert "axp_u_next.secret" not in result.stdout


def test_operator_qa_ci_fails_when_configured_matrix_fails(tmp_path):
    _write_fake_axctl(tmp_path, matrix_ok=False)

    result = _run_script(
        tmp_path,
        env={
            "AX_QA_ENVS": "dev",
            "AX_QA_DEV_TOKEN": "axp_u_dev.secret",
            "AX_QA_DEV_BASE_URL": "https://dev.paxai.app",
            "AX_QA_DEV_SPACE_ID": "dev-space",
        },
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["matrix_ok"] is False
