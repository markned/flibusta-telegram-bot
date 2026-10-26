"""Offline checks for discovery and malformed configuration failure paths."""
import json
from pathlib import Path

from scripts.check_codex_skills import frontmatter, validate


def project(tmp_path: Path) -> Path:
    skill = tmp_path / ".agents/skills/example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: A focused test skill.\n---\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/codex-routing.json").write_text(json.dumps({
        "routes": [{"id": "small", "skills": ["example"]}],
        "smoke": [{"route": "small", "expected": ["example"]}],
    }))
    return tmp_path


def test_offline_discovery_and_routing(tmp_path):
    assert validate(project(tmp_path)) == ([], 1, 0)


def test_required_frontmatter_and_folded_description():
    assert frontmatter("---\nname: x\ndescription: >-\n  line one\n  line two\n---\n")["description"] == "line one line two"


def test_missing_reference_is_reported_but_examples_are_ignored(tmp_path):
    root = project(tmp_path)
    manifest = root / ".agents/skills/example/SKILL.md"
    with manifest.open("a") as stream:
        stream.write("[missing](missing.md)\n```md\n[example](invented.md)\n```\n[web](https://example.com)\n[anchor](#here)\n")
    errors, _, _ = validate(root)
    assert len(errors) == 1
    assert "missing.md" in errors[0]


def test_routing_rejects_uninstalled_skills(tmp_path):
    root = project(tmp_path)
    path = root / "docs/codex-routing.json"
    data = json.loads(path.read_text())
    data["routes"][0]["skills"] = ["absent"]
    path.write_text(json.dumps(data))
    errors, _, _ = validate(root)
    assert any("unknown skill absent" in error for error in errors)
    assert any("smoke mismatch" in error for error in errors)


def test_escaping_symlink_and_invalid_agent(tmp_path):
    root = project(tmp_path)
    (root / ".agents/skills/example/escape").symlink_to(root.parent)
    agents = root / ".codex/agents"
    agents.mkdir(parents=True)
    (agents / "broken.toml").write_text('name = "broken"\n')
    errors, _, _ = validate(root)
    assert any("symlink escapes" in error for error in errors)
    assert any("missing developer_instructions" in error for error in errors)


def test_lock_detects_changed_resources_and_dependency_cycle(tmp_path):
    root = project(tmp_path)
    (root / "docs/LICENSE").write_text("test license")
    (root / "docs/codex-upstream.json").write_text(json.dumps({"skills": [{
        "name": "example", "commit": "a" * 40, "license": "docs/LICENSE",
        "dependencies": ["example"], "installed_sha256": {"SKILL.md": "0" * 64},
    }]}))
    errors, _, _ = validate(root)
    assert any("modified/missing locked resource" in error for error in errors)
    assert any("dependency cycle" in error for error in errors)
