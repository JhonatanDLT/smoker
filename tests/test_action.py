from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"


def load_action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def test_action_is_composite_and_declares_outputs() -> None:
    action = load_action()
    assert action["runs"]["using"] == "composite"
    assert set(action["outputs"]) == {"verdict", "dataset-hash", "report-path"}


def test_required_inputs_exist() -> None:
    action = load_action()
    inputs = action["inputs"]
    assert inputs["source-dsn"]["required"] is True
    assert inputs["target-dsn"]["required"] is True
    for name in ["rows", "seed", "spec", "fail-on", "max-passes", "format", "out"]:
        assert name in inputs


def test_run_step_uses_the_cli_flags_we_document() -> None:
    action = load_action()
    run_steps = [step for step in action["runs"]["steps"] if step.get("id") == "run"]
    assert run_steps, "the composite action needs a step with id=run"
    script = run_steps[0]["run"]
    for flag in [
        "--source-env",
        "--target-env",
        "--rows",
        "--seed",
        "--max-passes",
        "--format",
        "--fail-on",
        "--out",
    ]:
        assert flag in script, f"action is missing {flag}"


def test_action_never_echoes_the_dsn() -> None:
    action = load_action()
    script = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
    assert "echo $SMOKER_SOURCE_DSN" not in script
    assert "echo $SMOKER_TARGET_DSN" not in script
