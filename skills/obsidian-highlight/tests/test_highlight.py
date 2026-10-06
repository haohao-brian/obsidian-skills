"""Behavioral tests of the exact JavaScript sent to Obsidian; no real vault writes."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "highlight.py"
SPEC = importlib.util.spec_from_file_location("highlight", SCRIPT)
highlight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(highlight)
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is needed only for the in-memory Obsidian test harness")
class HighlightBehaviorTests(unittest.TestCase):
    def run_note(self, note, text, context=None, dry_run=False, editor=None, current=None):
        # process_current models a file changing after the initial read but before
        # process acquires its write lock. This tests fresh targeting, not a stubbed plan.
        fixture = {"note": note, "editor": editor, "process_current": current,
                   "code": highlight.build_code("Study/Note.md", text, context, dry_run)}
        harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let content = input.note, writes = 0;
const file = {path: 'Study/Note.md', extension: 'md'};
global.app = {
  workspace: {getLeavesOfType: () => input.editor === null ? [] :
    [{view: {file, editor: {getValue: () => input.editor}}}]},
  vault: {
    getAbstractFileByPath: path => path === file.path ? file : null,
    read: async () => content,
    process: async (_, callback) => {
      if (input.process_current !== null) content = input.process_current;
      const next = callback(content);
      content = next;
      ++writes;
    }
  }
};
(async () => {
  const result = JSON.parse(await eval(input.code));
  console.log(JSON.stringify({result, content, writes}));
})().catch(error => {console.error(error); process.exit(1);});
"""
        completed = subprocess.run([NODE, "-e", harness], input=json.dumps(fixture),
                                   capture_output=True, text=True, check=True)
        return json.loads(completed.stdout)

    def test_exact_span_preserves_unicode_and_all_other_bytes(self):
        original = "---\ntitle: 測試\n---\n# 學習\n\n前綴：中文 🐱 and café；後綴。\r\n"
        actual = self.run_note(original, "中文 🐱 and café")
        self.assertEqual(actual["content"], original.replace("中文 🐱 and café", "==中文 🐱 and café=="))
        self.assertTrue(actual["result"]["changed"])

    def test_ambiguous_or_missing_span_never_writes(self):
        for note, text, error in [("repeat repeat", "repeat", "ambiguous_text"),
                                  ("original", "missing", "text_not_found")]:
            actual = self.run_note(note, text)
            self.assertEqual(actual["result"]["error"], error)
            self.assertEqual(actual["content"], note)
            self.assertEqual(actual["writes"], 0)

    def test_context_selects_only_one_exact_occurrence(self):
        actual = self.run_note("A: repeated. B: repeated.", "repeated", "B: repeated.")
        self.assertEqual(actual["content"], "A: repeated. B: ==repeated==.")
        ambiguous = self.run_note("A: repeated repeated.", "repeated", "A: repeated repeated.")
        self.assertEqual(ambiguous["result"]["error"], "ambiguous_text")

    def test_existing_exact_highlight_is_idempotent(self):
        actual = self.run_note("Before ==already marked== after.", "already marked")
        self.assertEqual(actual["content"], "Before ==already marked== after.")
        self.assertTrue(actual["result"]["already_highlighted"])
        self.assertFalse(actual["result"]["changed"])

    def test_original_context_stays_idempotent_after_inserting_highlights(self):
        original = "First paragraph: Original sentence. Second paragraph: Original sentence."
        text = "Original sentence."
        context = "First paragraph: Original sentence."
        first = self.run_note(original, text, context)
        self.assertEqual(first["content"], "First paragraph: ==Original sentence.== Second paragraph: Original sentence.")
        second = self.run_note(first["content"], text, context)
        self.assertTrue(second["result"]["already_highlighted"])
        self.assertFalse(second["result"]["changed"])
        self.assertEqual(second["content"], first["content"])
        ambiguous = self.run_note("Same: ==phrase== phrase.", "phrase", "Same: phrase phrase.")
        self.assertEqual(ambiguous["result"]["error"], "ambiguous_text")

    def test_partial_or_adjacent_highlights_are_rejected(self):
        for note, text in [("==long phrase==", "phrase"),
                           ("new==existing==", "new"),
                           ("==existing==new", "new"),
                           ("==open and target", "target")]:
            actual = self.run_note(note, text)
            self.assertEqual(actual["result"]["error"], "overlapping_highlight")
            self.assertEqual(actual["content"], note)

    def test_protected_markdown_regions_are_rejected(self):
        for note in ["---\ntitle: target\n---\nBody.", "```c\ntarget\n```", "~~~\ntarget\n~~~",
                     "    target\n", "Use `target`.", "%% target %%", "<!-- target -->",
                     "[reference]: https://target.example"]:
            actual = self.run_note(note, "target")
            self.assertEqual(actual["result"]["error"], "unsafe_target")
            self.assertEqual(actual["writes"], 0)

    def test_partial_links_cannot_change_destinations_or_labels(self):
        for note, text in [("See [[Existing Link]].", "Existing Link"),
                           ("See [[Destination|Label]].", "Label"),
                           ("See [Label](https://example.test/target).", "target"),
                           ("See [Target Label][ref].", "Target Label"),
                           ("See [Nested [Label]](https://example.test/a(b)c).", "Label"),
                           ("See [Label](https://example.test \"a ) title\").", "title"),
                           ("See <https://example.test/target>.", "target"),
                           ("See ![[Picture.png]].", "Picture")]:
            actual = self.run_note(note, text)
            self.assertEqual(actual["result"]["error"], "partial_link")
            self.assertEqual(actual["content"], note)
            self.assertEqual(actual["writes"], 0)
        shortcut = self.run_note("See [Existing Link].\n\n[Existing Link]: https://example.test",
                                 "Existing", "See [Existing Link].")
        self.assertEqual(shortcut["result"]["error"], "partial_link")
        self.assertEqual(shortcut["writes"], 0)

    def test_complete_links_inside_prose_are_preserved(self):
        text = "See [[Existing Link]] and [Label](https://example.test/a(b)c)."
        original = "Prefix. " + text + " Suffix."
        actual = self.run_note(original, text)
        self.assertEqual(actual["content"], "Prefix. ==" + text + "== Suffix.")

    def test_dry_run_reports_plan_without_writing(self):
        actual = self.run_note("Read this phrase.", "this phrase", dry_run=True)
        self.assertTrue(actual["result"]["would_change"])
        self.assertFalse(actual["result"]["changed"])
        self.assertEqual(actual["content"], "Read this phrase.")
        self.assertEqual(actual["writes"], 0)

    def test_unsaved_editor_is_never_overwritten(self):
        actual = self.run_note("Saved target.", "target", editor="Saved target. My new draft.")
        self.assertEqual(actual["result"]["error"], "unsaved_editor")
        self.assertEqual(actual["content"], "Saved target.")
        self.assertEqual(actual["writes"], 0)

    def test_process_uses_latest_text_instead_of_overwriting_new_content(self):
        actual = self.run_note("Old target.", "target", current="Fresh paragraph and target.")
        self.assertEqual(actual["content"], "Fresh paragraph and ==target==.")
        ambiguous = self.run_note("Old target.", "target", current="New target target.")
        self.assertEqual(ambiguous["result"]["error"], "ambiguous_text")
        self.assertEqual(ambiguous["content"], "New target target.")
        self.assertEqual(ambiguous["writes"], 0)


class CLITransportTests(unittest.TestCase):
    def test_untrusted_text_stays_a_single_subprocess_argument_and_json_string(self):
        text = '中文 `$(touch /tmp/never)` "quoted" ; \\ escape'
        reply = {"tag": highlight.RESULT_TAG, "ok": True, "path": "My note.md", "changed": True}
        with patch.object(highlight.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "=> " + json.dumps(reply), "")) as run:
            self.assertTrue(highlight.highlight("My Vault", "My note.md", text)["ok"])
        args, kwargs = run.call_args
        self.assertEqual(args[0][:3], ["obsidian", "vault=My Vault", "eval"])
        self.assertEqual(len(args[0]), 4)
        self.assertNotIn("shell", kwargs)
        encoded = args[0][3].split("const input = ", 1)[1].split(";\n  const fail", 1)[0]
        self.assertEqual(json.loads(encoded)["text"], text)

    def test_exit_zero_cli_error_is_not_success(self):
        with patch.object(highlight.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "Error: Vault not found", "")):
            with self.assertRaisesRegex(ValueError, "did not return"):
                highlight.highlight("Closed Vault", "Note.md", "target")

    def test_request_validation_rejects_non_note_paths_and_marked_text(self):
        for path in ["/tmp/note.md", "../note.md", "folder/../note.md", "C:\\note.md",
                     ".obsidian/settings.md", "note.pdf"]:
            with self.assertRaises(ValueError):
                highlight.validate_request("Vault", path, "target")
        for text in ["", "   ", "==target==", "first\n\nsecond", "first\r\n \r\nsecond"]:
            with self.assertRaises(ValueError):
                highlight.validate_request("Vault", "Note.md", text)


if __name__ == "__main__":
    unittest.main()
