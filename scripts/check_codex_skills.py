#!/usr/bin/env python3
"""Validate checked-in Codex discovery files; no model or network is invoked."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
from urllib.parse import unquote, urlsplit

NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")


def frontmatter(source: str) -> dict[str, str]:
    """Read required top-level YAML scalars without installing a YAML runtime."""
    lines = source.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("unterminated YAML frontmatter") from exc
    values: dict[str, str] = {}
    for index, line in enumerate(lines[1:end], 1):
        match = re.match(r"^(name|description):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if key in values:
            raise ValueError(f"duplicate {key}")
        if value in {">", ">-", "|", "|-"}:
            continuation = []
            for following in lines[index + 1:end]:
                if following and not following[0].isspace():
                    break
                continuation.append(following.strip())
            value = " ".join(continuation).strip()
        elif value.startswith('"') and value.endswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        values[key] = value
    return values


def prose(source: str) -> str:
    """Drop fenced examples, which may intentionally show invented file paths."""
    result = []
    fence = None
    for line in source.splitlines():
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)[0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
            continue
        if fence is None:
            result.append(line)
    return "\n".join(result)


def validate(root: Path) -> tuple[list[str], int, int]:
    root = root.resolve()
    errors: list[str] = []
    skill_root = root / ".agents/skills"
    manifests = sorted(skill_root.glob("*/SKILL.md"))
    names: set[str] = set()
    if not manifests:
        errors.append("no project skills found in .agents/skills/*/SKILL.md")
    for path in skill_root.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            errors.append(f"{path.relative_to(root)}: symlink escapes repository")
    for path in manifests:
        label = str(path.relative_to(root))
        try:
            metadata = frontmatter(path.read_text())
        except (ValueError, OSError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        name = metadata.get("name", "")
        if not NAME.fullmatch(name) or len(name) > 64:
            errors.append(f"{label}: invalid skill name {name!r}")
        if name != path.parent.name:
            errors.append(f"{label}: name differs from folder")
        if name in names:
            errors.append(f"{label}: duplicate name {name!r}")
        names.add(name)
        if not metadata.get("description", "").strip():
            errors.append(f"{label}: missing description")
    for path in sorted(skill_root.rglob("*.md")):
        for match in LINK.finditer(prose(path.read_text())):
            target = unquote(match.group(1).strip("<>"))
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            if any(token in target for token in ("<", ">", "{", "}", "*", "...")):
                continue
            resolved = (path.parent / parsed.path).resolve()
            if not resolved.is_relative_to(root):
                errors.append(f"{path.relative_to(root)}: link escapes repository: {target}")
            elif not resolved.exists():
                errors.append(f"{path.relative_to(root)}: missing reference: {target}")
    agents = sorted((root / ".codex/agents").glob("*.toml"))
    for path in agents:
        try:
            data = tomllib.loads(path.read_text())
            for key in ("name", "description", "developer_instructions"):
                if not isinstance(data.get(key), str) or not data[key].strip():
                    errors.append(f"{path.relative_to(root)}: missing {key}")
        except (ValueError, OSError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
    routing = root / "docs/codex-routing.json"
    try:
        data = json.loads(routing.read_text())
        routes: dict[str, list[str]] = {}
        for route in data["routes"]:
            route_id, selected = route["id"], route["skills"]
            if route_id in routes:
                errors.append(f"routing: duplicate route {route_id}")
            routes[route_id] = selected
            if not isinstance(selected, list) or not all(isinstance(n, str) for n in selected):
                errors.append(f"routing: invalid skills for {route_id}")
                continue
            if len(set(selected)) != len(selected):
                errors.append(f"routing: repeated skill in {route_id}")
            for name in selected:
                if name not in names:
                    errors.append(f"routing: unknown skill {name} in {route_id}")
        for sample in data["smoke"]:
            if routes.get(sample["route"]) != sample["expected"]:
                errors.append(f"routing: smoke mismatch for {sample['route']}")
        if not data["smoke"]:
            errors.append("routing: no static smoke examples")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        errors.append(f"routing: {exc}")
    lockfile = root / "docs/codex-upstream.json"
    if lockfile.exists():
        lock = json.loads(lockfile.read_text())
        graph = {}
        for entry in lock["skills"]:
            name = entry["name"]
            graph[name] = [d for d in entry["dependencies"] if d in names]
            if not re.fullmatch(r"[0-9a-f]{40}", entry["commit"]):
                errors.append(f"upstream: invalid commit for {name}")
            if not (root / entry["license"]).is_file():
                errors.append(f"upstream: missing license for {name}")
            for relative, expected in entry["installed_sha256"].items():
                resource = skill_root / name / relative
                if not resource.is_file() or hashlib.sha256(resource.read_bytes()).hexdigest() != expected:
                    errors.append(f"upstream: modified/missing locked resource {name}/{relative}")
            for dependency in entry["dependencies"]:
                if dependency.endswith(".md") and not (root / dependency).is_file():
                    errors.append(f"upstream: missing dependency {dependency}")
        def visit(name, stack):
            if name in stack:
                errors.append(f"upstream: dependency cycle at {name}")
                return
            for dependency in graph.get(name, []):
                visit(dependency, stack | {name})
        for name in graph:
            visit(name, set())
    return errors, len(manifests), len(agents)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors, skill_count, agent_count = validate(args.root)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Validated {skill_count} skills, {agent_count} agents, local references and static routing smoke cases.")
    print("Static discovery checks only; actual Codex activation requires a fresh session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
