"""Execute the real navigation JavaScript against an in-memory Obsidian API."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "navigate.py"
SPEC = importlib.util.spec_from_file_location("navigate", SCRIPT)
navigate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(navigate)
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is needed only for the in-memory Obsidian test harness")
class NavigationBehaviorTests(unittest.TestCase):
    def run_note(self, note, *, line=None, text=None, context=None, path=None,
                 mode="preview", active_path="Study/Note.md", view_type="markdown",
                 other_notes=None, active_leaf=True, scroll_supported=True, editor_content=None,
                 render_reset_waits=0, repeated_render_reset=False):
        notes = {"Study/Note.md": note, **(other_notes or {})}
        fixture = {"notes": notes, "active_path": active_path, "mode": mode,
                   "view_type": view_type, "active_leaf": active_leaf,
                   "scroll_supported": scroll_supported, "editor_content": editor_content,
                   "render_reset_waits": render_reset_waits,
                   "repeated_render_reset": repeated_render_reset,
                   "code": navigate.build_code(path, line, text, context)}
        harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const calls = [], reads = [];
let writes = 0, mode = input.mode, reportedScroll = 0, pendingRenderWaits = 0, waits = 0;
const files = Object.fromEntries(Object.keys(input.notes).map(path =>
  [path, {path, extension: path.split('.').pop()}]));
const file = files[input.active_path] || null;
const previewMode = {
  applyScroll: async position => {calls.push(['preview_scroll', position]); reportedScroll = position;},
  getScroll: () => reportedScroll + 0.002
};
const editor = {
  getValue: () => input.editor_content ?? input.notes[view.file?.path],
  setCursor: position => calls.push(['cursor', position]),
  scrollIntoView: (range, center) => calls.push(['editor_scroll', range, center])
};
const view = {
  file,
  getViewType: () => input.view_type,
  getMode: () => mode,
  previewMode: input.scroll_supported ? previewMode : undefined,
  editor: input.scroll_supported ? editor : undefined
};
const leaf = {
  view,
  getViewState: () => ({type: 'markdown', state: {file: view.file?.path, mode, source: true}}),
  openFile: async target => {
    calls.push(['open', target.path]);
    view.file = target;
    mode = 'source';
  },
  setViewState: async state => {
    calls.push(['state', state]);
    mode = state.state.mode;
    pendingRenderWaits = input.render_reset_waits;
  }
};
const rejectWrite = () => {++writes; throw new Error('Navigation attempted a vault write');};
global.app = {
  workspace: {
    activeLeaf: input.active_leaf ? leaf : null,
    getActiveFile: () => file
  },
  vault: {
    getAbstractFileByPath: path => files[path] || null,
    cachedRead: async target => {reads.push(target.path); return input.notes[target.path];},
    read: () => {throw new Error('Use cachedRead for current note content');},
    modify: rejectWrite, process: rejectWrite, create: rejectWrite,
    delete: rejectWrite, rename: rejectWrite, append: rejectWrite
  }
};
// A renderer completing after setViewState can reset a scroll applied too soon.
global.setTimeout = callback => {
  ++waits;
  if (input.repeated_render_reset || (pendingRenderWaits > 0 && --pendingRenderWaits === 0))
    reportedScroll = 0;
  callback();
  return 0;
};
(async () => {
  const result = JSON.parse(await eval(input.code));
  console.log(JSON.stringify({result, calls, reads, writes, waits, notes: input.notes}));
})().catch(error => {console.error(error); process.exit(1);});
"""
        completed = subprocess.run([NODE, "-e", harness], input=json.dumps(fixture),
                                   capture_output=True, text=True, check=True)
        actual = json.loads(completed.stdout)
        self.assertEqual(actual["notes"], notes)
        self.assertEqual(actual["writes"], 0)
        return actual

    def test_reading_view_uses_active_note_and_one_based_source_line(self):
        actual = self.run_note("# Heading\n\nThird source line.\n", line=3)
        result = actual["result"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], "Study/Note.md")
        self.assertEqual(result["line"], 3)
        self.assertEqual(result["mode"], "preview")
        self.assertIsNone(result["search"])
        self.assertFalse(result["opened_note"])
        self.assertEqual(result["scroll"]["requested"], 2)
        self.assertAlmostEqual(result["scroll"]["reported"], 2.002)
        self.assertEqual(actual["calls"], [["preview_scroll", 2]])
        self.assertEqual(actual["reads"], ["Study/Note.md"])

    def test_literal_unicode_and_multiline_text_locates_start_line(self):
        for text, note, expected_line in [
                ('中文 🐱 café [x].* "quoted"', 'First.\n中文 🐱 café [x].* "quoted"\nLast.', 2),
                ("first\nsecond", "Header\nfirst\nsecond\nFooter", 2),
                ("first\r\nsecond", "Header\r\nfirst\r\nsecond\r\nFooter", 2)]:
            with self.subTest(text=text):
                actual = self.run_note(note, text=text)
                self.assertTrue(actual["result"]["ok"])
                self.assertEqual(actual["result"]["line"], expected_line)
                self.assertEqual(actual["result"]["search"]["matches"][0]["line"], expected_line)
                self.assertEqual(actual["calls"], [["preview_scroll", expected_line - 1]])

    def test_missing_or_ambiguous_text_does_not_open_or_scroll(self):
        for text, expected_error, expected_matches in [
                ("missing", "text_not_found", 0), ("repeat", "ambiguous_text", 2),
                ("r.*t", "text_not_found", 0)]:
            with self.subTest(text=text):
                actual = self.run_note("Current note.", path="Other.md", text=text,
                                       other_notes={"Other.md": "repeat\nrepeat"})
                self.assertFalse(actual["result"]["ok"])
                self.assertEqual(actual["result"]["error"], expected_error)
                self.assertEqual(actual["result"]["search"]["match_count"], expected_matches)
                self.assertEqual(actual["calls"], [])

    def test_context_must_identify_one_context_and_one_target(self):
        original = "A: repeat.\nB: repeat."
        selected = self.run_note(original, text="repeat", context="B: repeat.")
        self.assertTrue(selected["result"]["ok"])
        self.assertEqual(selected["result"]["line"], 2)
        self.assertEqual(selected["result"]["search"]["total_matches"], 2)
        self.assertEqual(selected["result"]["search"]["match_count"], 1)
        for note, context, expected_error in [
                (original, "Missing: repeat.", "context_not_found"),
                ("Same: repeat.\nSame: repeat.", "Same: repeat.", "ambiguous_context"),
                ("Same: repeat repeat.", "Same: repeat repeat.", "ambiguous_text")]:
            with self.subTest(note=note):
                actual = self.run_note(note, text="repeat", context=context)
                self.assertEqual(actual["result"]["error"], expected_error)
                self.assertEqual(actual["calls"], [])

    def test_line_bounds_reject_before_opening_and_accept_final_empty_line(self):
        for line in [0, -1, 4]:
            actual = self.run_note("Current note.", path="Other.md", line=line,
                                   other_notes={"Other.md": "one\ntwo\n"})
            self.assertEqual(actual["result"]["error"], "line_out_of_range")
            self.assertEqual(actual["calls"], [])
        self.assertTrue(self.run_note("one\ntwo\n", line=3)["result"]["ok"])
        self.assertTrue(self.run_note("", line=1)["result"]["ok"])

    def test_other_note_opens_in_existing_leaf_and_keeps_reading_mode(self):
        actual = self.run_note("Current.", path="Other.md", text="destination",
                               other_notes={"Other.md": "Heading\ndestination"})
        self.assertTrue(actual["result"]["ok"])
        self.assertTrue(actual["result"]["opened_note"])
        self.assertEqual(actual["result"]["path"], "Other.md")
        self.assertEqual(actual["result"]["mode"], "preview")
        self.assertEqual([call[0] for call in actual["calls"]], ["open", "state", "preview_scroll"])
        self.assertEqual(actual["calls"][1][1]["state"]["file"], "Other.md")
        self.assertEqual(actual["calls"][1][1]["state"]["mode"], "preview")

    def test_new_reading_note_waits_for_renderer_before_scrolling(self):
        actual = self.run_note("Short current note.", path="Long.md", line=50,
                               other_notes={"Long.md": "\n".join(f"line {n}" for n in range(1, 61))},
                               render_reset_waits=1)
        self.assertTrue(actual["result"]["ok"])
        self.assertAlmostEqual(actual["result"]["scroll"]["reported"], 49.002)
        self.assertEqual(actual["result"]["scroll"]["attempts"], 1)
        self.assertEqual(actual["waits"], 2)

    def test_slower_renderer_gets_only_one_bounded_scroll_retry(self):
        actual = self.run_note("Short current note.", path="Long.md", line=50,
                               other_notes={"Long.md": "\n".join(f"line {n}" for n in range(1, 61))},
                               render_reset_waits=2)
        self.assertAlmostEqual(actual["result"]["scroll"]["reported"], 49.002)
        self.assertEqual(actual["result"]["scroll"]["attempts"], 2)
        self.assertEqual(actual["waits"], 3)
        self.assertEqual([call for call in actual["calls"] if call[0] == "preview_scroll"],
                         [["preview_scroll", 49], ["preview_scroll", 49]])

    def test_persistent_scroll_reset_does_not_loop_forever(self):
        actual = self.run_note("Short current note.", path="Long.md", line=50,
                               other_notes={"Long.md": "\n".join(f"line {n}" for n in range(1, 61))},
                               repeated_render_reset=True)
        self.assertEqual(actual["result"]["scroll"]["attempts"], 2)
        self.assertAlmostEqual(actual["result"]["scroll"]["reported"], 0.002)
        self.assertEqual(actual["waits"], 3)

    def test_source_view_uses_public_editor_navigation(self):
        actual = self.run_note("Current.", path="Other.md", line=2, mode="source",
                               other_notes={"Other.md": "Heading\nDestination"})
        self.assertTrue(actual["result"]["ok"])
        self.assertEqual(actual["result"]["mode"], "source")
        self.assertEqual(actual["calls"][-2:], [
            ["cursor", {"line": 1, "ch": 0}],
            ["editor_scroll", {"from": {"line": 1, "ch": 0}, "to": {"line": 1, "ch": 0}}, True]])

    def test_active_source_view_resolves_against_unsaved_editor_buffer(self):
        draft = "Inserted draft line.\nAnother new line.\nHeading\nDestination"
        actual = self.run_note("Heading\nDestination", text="Destination", mode="source",
                               editor_content=draft)
        self.assertTrue(actual["result"]["ok"])
        self.assertEqual(actual["result"]["line"], 4)
        self.assertEqual(actual["reads"], [])
        self.assertEqual(actual["calls"][0], ["cursor", {"line": 3, "ch": 0}])
        at_line = self.run_note("Heading\nDestination", line=4, mode="source", editor_content=draft)
        self.assertTrue(at_line["result"]["ok"])

    def test_absent_or_unsupported_target_is_a_clear_noop(self):
        for kwargs, expected_error in [
                ({"active_leaf": False}, "unsupported_view"),
                ({"view_type": "pdf"}, "unsupported_view"),
                ({"active_path": None}, "no_active_note"),
                ({"path": "Missing.md"}, "missing_note"),
                ({"active_path": "File.pdf", "other_notes": {"File.pdf": "pdf"}}, "not_markdown"),
                ({"scroll_supported": False}, "unsupported_scroll")]:
            with self.subTest(kwargs=kwargs):
                actual = self.run_note("Original.", line=1, **kwargs)
                self.assertEqual(actual["result"]["error"], expected_error)
                self.assertEqual(actual["calls"], [])


class CLITransportTests(unittest.TestCase):
    def test_untrusted_arguments_are_structured_and_json_encoded(self):
        text = '中文 `$(touch /tmp/never)` "quoted" ; \\ escape\nnext'
        path = 'Folder/My "note" $(ignored).md'
        reply = {"tag": navigate.RESULT_TAG, "ok": True, "path": path,
                 "line": 8, "mode": "preview", "search": {"match_count": 1}}
        with patch.object(navigate.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "=> " + json.dumps(json.dumps(reply)), "")) as run:
            self.assertTrue(navigate.navigate("My Vault", path, text=text)["ok"])
        args, kwargs = run.call_args
        self.assertEqual(args[0][:3], ["obsidian", "vault=My Vault", "eval"])
        self.assertEqual(len(args[0]), 4)
        self.assertNotIn("shell", kwargs)
        encoded = args[0][3].split("const input = ", 1)[1].split(";\n  let path", 1)[0]
        self.assertEqual(json.loads(encoded)["text"], text)
        self.assertEqual(json.loads(encoded)["path"], path)

    def test_optional_vault_uses_default_cli_target(self):
        reply = {"tag": navigate.RESULT_TAG, "ok": True}
        with patch.object(navigate.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(reply), "")) as run:
            navigate.navigate(line=1)
        self.assertEqual(run.call_args.args[0][:2], ["obsidian", "eval"])

    def test_exit_zero_error_in_either_stream_is_failure_even_with_result(self):
        reply = json.dumps({"tag": navigate.RESULT_TAG, "ok": True})
        for stdout, stderr in [("Error: Vault not found", ""),
                               (reply, "Error: Evaluation failed"),
                               ("Error: Evaluation failed\n" + reply, "")]:
            with self.subTest(stdout=stdout, stderr=stderr):
                with patch.object(navigate.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, stdout, stderr)):
                    with self.assertRaisesRegex(ValueError, "Error:"):
                        navigate.navigate(line=1)

    def test_invalid_local_requests_never_invoke_obsidian(self):
        invalid = [{"line": 0}, {"line": -1}, {"line": True}, {},
                   {"line": 1, "text": "target"}, {"text": "   "},
                   {"line": 1, "context": "target"},
                   {"text": "target", "context": "unrelated"},
                   {"line": 1, "vault": " "}]
        invalid.extend({"line": 1, "path": path} for path in [
            "/tmp/note.md", "../note.md", "folder/../note.md", "C:\\note.md",
            ".obsidian/settings.md", ".trash/note.md", "note.pdf", "folder//note.md"])
        with patch.object(navigate.subprocess, "run") as run:
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    navigate.navigate(**kwargs)
            run.assert_not_called()

    def test_main_reports_failure_as_json_and_returns_nonzero(self):
        with patch.object(navigate.sys, "argv", [str(SCRIPT), "--line", "0"]), \
                patch("builtins.print") as output:
            self.assertEqual(navigate.main(), 1)
        result = json.loads(output.call_args.args[0])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "cli_error")
        self.assertIn("positive", result["message"])
        for field in ["path", "line", "mode", "search"]:
            self.assertIn(field, result)


if __name__ == "__main__":
    unittest.main()
