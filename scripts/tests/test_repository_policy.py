import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("repository_policy", Path(__file__).parents[1] / "check_repository.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class RepositoryPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_git("init", "-q")
        self.policy = {"max_blob_bytes": 1024, "example_taxpayer_ids": ["A000000000Z"]}

    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL)

    def stage(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.run_git("add", "-f", "--", name)

    def test_force_added_ignored_prompt_is_rejected(self):
        self.stage(".gitignore", b"CLAUDE.md\n")
        self.stage("CLAUDE.md", b"personal instructions")
        self.assertIn(("CLAUDE.md", "local-or-build-artifact"), CHECK.check_source(self.root, self.policy, None))

    def test_staged_bytes_are_scanned_even_when_working_copy_is_clean(self):
        self.stage("example.txt", b"-----BEGIN " + b"PRIVATE KEY-----")
        (self.root / "example.txt").write_text("clean")
        self.assertIn(("example.txt", "private-key"), CHECK.check_source(self.root, self.policy, None))

    def test_unstaged_secret_is_not_claimed_to_be_in_candidate(self):
        self.stage("example.txt", b"clean")
        (self.root / "example.txt").write_bytes(b"-----BEGIN " + b"PRIVATE KEY-----")
        self.assertEqual([], CHECK.check_source(self.root, self.policy, None))

    def test_unknown_identifier_rejected_but_reviewed_synthetic_example_passes(self):
        self.stage("fixture.json", b'{"tin":"A000000000Z"}')
        self.assertEqual([], CHECK.check_source(self.root, self.policy, None))
        self.stage("fixture.json", b'{"tin":"' + b"Q" + b"987654321" + b"Z" + b'"}')
        self.assertIn(("fixture.json", "unreviewed-taxpayer-identifier"), CHECK.check_source(self.root, self.policy, None))

    def test_unexpected_gitlink_is_rejected(self):
        self.run_git("update-index", "--add", "--cacheinfo", "160000," + "f" * 40 + ",other-repository")
        self.assertIn(("other-repository", "unexpected-gitlink"), CHECK.check_source(self.root, self.policy, None))

    def test_large_blob_exception_only_accepts_exact_existing_blob(self):
        self.stage("vendor.bin", b"approved" * 200)
        oid = self.run_git("hash-object", "vendor.bin").decode().strip()
        self.policy["large_blob_exceptions"] = {"vendor.bin": {"git_blob": oid}}
        self.assertEqual([], CHECK.check_source(self.root, self.policy, None))
        self.stage("vendor.bin", b"modified" * 200)
        self.assertIn(("vendor.bin", "unapproved-large-blob"), CHECK.check_source(self.root, self.policy, None))

    def test_symlinks_and_handoffs_rejected(self):
        (self.root / "linked").symlink_to("/outside")
        self.run_git("add", "linked")
        self.stage("docs/PHASE4_VERIFICATION.md", b"handoff")
        problems = CHECK.check_source(self.root, self.policy, None)
        self.assertIn(("linked", "unsupported-file-mode"), problems)
        self.assertIn(("docs/PHASE4_VERIFICATION.md", "private-transcript-or-handoff"), problems)

    def test_immutable_exception_does_not_allow_later_identifier_changes(self):
        self.stage("migration.sql", b"Q" + b"987654321" + b"Z")
        oid = self.run_git("hash-object", "migration.sql").decode().strip()
        self.policy["immutable_identifier_exceptions"] = {"migration.sql": {"git_blob": oid}}
        self.assertEqual([], CHECK.check_source(self.root, self.policy, None))
        self.stage("migration.sql", b"changed Q" + b"987654321" + b"Z")
        self.assertIn(("migration.sql", "unreviewed-taxpayer-identifier"), CHECK.check_source(self.root, self.policy, None))

    def test_artifact_policy_checks_files_and_secret_content(self):
        site = self.root / "site"
        site.mkdir()
        (site / "index.html").write_text("public page")
        self.assertEqual([], CHECK.check_artifact(site, self.policy))
        (site / ".env").write_bytes(b"-----BEGIN " + b"PRIVATE KEY-----")
        problems = CHECK.check_artifact(site, self.policy)
        self.assertIn((".env", "local-or-build-artifact"), problems)
        self.assertIn((".env", "private-key"), problems)


if __name__ == "__main__":
    unittest.main()
