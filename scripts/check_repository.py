#!/usr/bin/env python3
"""Check a candidate Git tree or a distribution directory without printing data."""

import argparse
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


LOCAL_NAMES = {"CLAUDE.md", "AGENTS.md", "GEMINI.md", ".env", "local.env",
               ".dev.vars", ".DS_Store"}
LOCAL_DIRS = {".claude", ".codex", ".agents", ".cursor", ".gemini", ".idea",
              ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
              ".wrangler", ".jekyll-cache", "target", "build", "dist", "_site"}
SECRETS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential-url": re.compile(rb"https?://[^\s/:@]+:[^\s/@]+@"),
}
TAXPAYER = re.compile(rb"\b[A-Z][0-9]{9}[A-Z]\b")


def git(root, *args, data=None):
    return subprocess.check_output(["git", "-C", str(root), *args], input=data)


def git_entries(root, ref):
    if ref:
        rows = git(root, "ls-tree", "-rz", "--full-tree", ref).split(b"\0")
        entries = []
        for row in filter(None, rows):
            meta, name = row.split(b"\t", 1)
            mode, kind, oid = meta.split()
            entries.append((name.decode("utf-8"), mode.decode(), oid.decode()))
    else:
        entries = []
        for row in filter(None, git(root, "ls-files", "--stage", "-z").split(b"\0")):
            meta, name = row.split(b"\t", 1)
            mode, oid, stage = meta.split()
            if stage != b"0":
                raise ValueError("unmerged index entries must be resolved")
            entries.append((name.decode("utf-8"), mode.decode(), oid.decode()))
    blob_ids = [oid for _, mode, oid in entries if mode != "160000"]
    sizes = {}
    if blob_ids:
        response = git(root, "cat-file", "--batch-check", data=("\n".join(blob_ids) + "\n").encode())
        for line in response.splitlines():
            oid, kind, size = line.split()
            if kind != b"blob":
                raise ValueError("candidate contains an invalid blob")
            sizes[oid.decode()] = int(size)
    return [(name, mode, oid, sizes.get(oid, 0)) for name, mode, oid in entries]


def path_violations(name, source=True):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in name):
        return ["unsafe-path"]
    violations = []
    if path.name in LOCAL_NAMES or any(part in LOCAL_DIRS for part in path.parts):
        violations.append("local-or-build-artifact")
    if any(part.endswith(".egg-info") for part in path.parts):
        violations.append("package-metadata")
    if path.name.startswith(".env.") and not path.name.endswith((".example", ".sample")):
        violations.append("environment-file")
    if re.search(r"(?:_transcript_.*\.json|(?:^|[-_])handoff(?:[-_.]|$)|(?:^|/)PHASE\d+_VERIFICATION\.md$|(?:^|/)phase\d+-verification\.md$|withholding-phase\d+-verification\.md$)", name, re.I):
        violations.append("private-transcript-or-handoff")
    if source and path.suffix.lower() in {".zip", ".gz", ".class", ".pyc", ".log"}:
        violations.append("generated-archive-or-log")
    return violations


def content_violations(data, policy, identifier_exception=False):
    violations = [name for name, pattern in SECRETS.items() if pattern.search(data)]
    if not identifier_exception:
        examples = set(policy.get("example_taxpayer_ids", []))
        if any(value.decode() not in examples for value in TAXPAYER.findall(data)):
            violations.append("unreviewed-taxpayer-identifier")
    return violations


def check_source(root, policy, ref):
    problems = []
    exceptions = policy.get("large_blob_exceptions", {})
    identifiers = policy.get("immutable_identifier_exceptions", {})
    for name, mode, oid, size in git_entries(root, ref):
        rules = path_violations(name)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in policy.get("forbidden_paths", [])):
            rules.append("retired-or-private-path")
        if mode == "160000":
            rules.append("unexpected-gitlink")
        elif mode not in {"100644", "100755"}:
            rules.append("unsupported-file-mode")
        else:
            exception = exceptions.get(name)
            if exception and oid != exception["git_blob"]:
                rules.append("changed-pinned-blob")
            if size > policy["max_blob_bytes"]:
                if not exception or oid != exception["git_blob"]:
                    rules.append("unapproved-large-blob")
            else:
                data = git(root, "cat-file", "blob", oid)
                rules.extend(content_violations(data, policy, identifiers.get(name, {}).get("git_blob") == oid))
        problems.extend((name, rule) for rule in rules)
    return problems


def check_artifact(directory, policy):
    problems = []
    if not directory.is_dir():
        raise ValueError("artifact directory does not exist")
    for file in sorted(directory.rglob("*")):
        name = file.relative_to(directory).as_posix()
        if file.is_symlink():
            problems.append((name, "artifact-symlink"))
        elif file.is_file():
            problems.extend((name, rule) for rule in path_violations(name, source=False))
            if file.stat().st_size > policy["max_blob_bytes"]:
                exception = policy.get("artifact_blob_exceptions", {}).get(name)
                if not exception or hashlib.sha256(file.read_bytes()).hexdigest() != exception["sha256"]:
                    problems.append((name, "unapproved-large-artifact"))
            else:
                problems.extend((name, rule) for rule in content_violations(file.read_bytes(), policy))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="inspect this commit instead of the staged index")
    parser.add_argument("--index", action="store_true", help="inspect the staged index (default)")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--policy", type=Path, default=Path(".github/repository-policy.json"))
    args = parser.parse_args()
    if args.ref and args.index:
        parser.error("choose --ref or --index")
    root = Path.cwd()
    try:
        if args.artifact_dir:
            policy = json.loads(args.policy.read_text())
        else:
            spec = f"{args.ref}:{args.policy.as_posix()}" if args.ref else f":{args.policy.as_posix()}"
            policy = json.loads(git(root, "show", spec))
        problems = (check_artifact(args.artifact_dir, policy) if args.artifact_dir
                    else check_source(root, policy, args.ref))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Repository hygiene could not complete: {type(error).__name__}", file=sys.stderr)
        return 2
    for name, rule in problems:
        print(f"{name}: {rule}", file=sys.stderr)
    if problems:
        print(f"Repository hygiene failed: {len(problems)} violation(s).", file=sys.stderr)
        return 1
    print("Repository hygiene passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
