#!/usr/bin/env python3
"""Scroll a running Obsidian Markdown view to an original source line or text."""

import argparse
import json
from pathlib import PurePosixPath
import subprocess
import sys


RESULT_TAG = "obsidian-navigate-v1"

# Preview scroll positions have been checked against source lines in Obsidian,
# but the public API does not guarantee an exact line-to-rendered-pixel mapping.
JAVASCRIPT = r"""
(async () => {
  const input = __INPUT__;
  let path = input.path, line = null, mode = null, search = null, opened_note = false;
  const fail = (error, message) => { throw { error, message }; };
  const result = (fields) => JSON.stringify({ tag: 'obsidian-navigate-v1',
    path, line, mode, search, opened_note, ...fields });
  try {
    const leaf = app.workspace.activeLeaf;
    if (!leaf || leaf.view?.getViewType() !== 'markdown')
      fail('unsupported_view', 'The active tab must be a Markdown note.');
    mode = leaf.view.getMode();
    if (!['source', 'preview'].includes(mode))
      fail('unsupported_mode', 'The active Markdown view has no supported reading or editor mode.');
    const file = input.path ? app.vault.getAbstractFileByPath(input.path)
      : app.workspace.getActiveFile();
    if (!file) fail(input.path ? 'missing_note' : 'no_active_note',
      input.path ? 'The exact Markdown note path was not found.' : 'There is no active Markdown note.');
    path = file.path;
    if (file.extension?.toLowerCase() !== 'md')
      fail('not_markdown', 'The requested file must be a Markdown note.');
    // Source mode can contain a draft that is newer than the vault's contents.
    const data = mode === 'source' && leaf.view.file?.path === path
        && typeof leaf.view.editor?.getValue === 'function'
      ? leaf.view.editor.getValue() : await app.vault.cachedRead(file);
    const lineCount = data.split('\n').length;
    const occurrences = (text) => {
      const found = [];
      for (let pos = data.indexOf(text); pos !== -1; pos = data.indexOf(text, pos + 1))
        found.push(pos);
      return found;
    };
    const position = (offset) => {
      const before = data.slice(0, offset).split('\n');
      return { line: before.length, column: before[before.length - 1].length + 1 };
    };
    if (input.line !== null) {
      if (!Number.isInteger(input.line) || input.line < 1 || input.line > lineCount)
        fail('line_out_of_range', `The source line must be between 1 and ${lineCount}.`);
      line = input.line;
    } else {
      let candidates = occurrences(input.text);
      search = { text: input.text, context: input.context,
        total_matches: candidates.length, match_count: candidates.length,
        matches: candidates.map(position) };
      if (input.context !== null) {
        const contexts = occurrences(input.context);
        search.context_matches = contexts.length;
        if (!contexts.length)
          fail('context_not_found', 'The exact surrounding context was not found.');
        if (contexts.length !== 1)
          fail('ambiguous_context', 'The surrounding context occurs more than once. Supply a unique context.');
        const start = contexts[0], end = start + input.context.length;
        candidates = candidates.filter(offset => offset >= start && offset + input.text.length <= end);
        search.matches = candidates.map(position);
      }
      search.match_count = candidates.length;
      if (!candidates.length) fail('text_not_found', 'The exact text was not found in the requested context.');
      if (candidates.length !== 1)
        fail('ambiguous_text', 'The exact text occurs more than once. Supply a unique surrounding context.');
      line = position(candidates[0]).line;
    }
    // Complete file lookup and target resolution before any view changes.
    const canScroll = (view) => mode === 'preview'
      ? typeof view.previewMode?.applyScroll === 'function'
      : typeof view.editor?.scrollIntoView === 'function' && typeof view.editor?.setCursor === 'function';
    if (!canScroll(leaf.view))
      fail('unsupported_scroll', 'The active Markdown view does not expose the required scroll method.');
    if (leaf.view.file?.path !== path) {
      const state = leaf.getViewState();
      await leaf.openFile(file);
      opened_note = true;
      await leaf.setViewState({ ...state, type: 'markdown',
        state: { ...state.state, file: path, mode } });
    }
    const view = leaf.view;
    if (view.file?.path !== path || view.getMode() !== mode || !canScroll(view))
      fail('view_not_ready', 'The requested note did not open in the expected Markdown mode.');
    let scroll;
    if (mode === 'preview') {
      const settle = () => new Promise(resolve => setTimeout(resolve, 600));
      const readScroll = () => typeof view.previewMode.getScroll === 'function'
        ? view.previewMode.getScroll() : null;
      // openFile/setViewState can finish before Reading view finishes rendering.
      if (opened_note) await settle();
      const requested = line - 1;
      let attempts = 1;
      await view.previewMode.applyScroll(requested);
      await settle();
      let reported = readScroll();
      // A later render may reset the first scroll. Reapply once, with a bound;
      // reported positions can also differ when a short note clamps at its end.
      if (opened_note && Number.isFinite(reported) && Math.abs(reported - requested) > 1) {
        await view.previewMode.applyScroll(requested);
        attempts = 2;
        await settle();
        reported = readScroll();
      }
      scroll = { method: 'previewMode.applyScroll', requested, reported, attempts };
    } else {
      const target = { line: line - 1, ch: 0 };
      view.editor.setCursor(target);
      view.editor.scrollIntoView({ from: target, to: target }, true);
      scroll = { method: 'editor.scrollIntoView', requested: line - 1, reported: null };
    }
    return result({ ok: true, scroll });
  } catch (error) {
    return result({ ok: false, error: error.error || 'obsidian_error',
      message: error.message || String(error) });
  }
})()
"""


def validate_request(vault=None, path=None, line=None, text=None, context=None):
    """Reject invalid requests before invoking the desktop CLI."""
    if vault is not None and not vault.strip():
        raise ValueError("--vault must identify a vault when supplied.")
    if path is not None:
        parsed = PurePosixPath(path)
        if (not path or parsed.is_absolute() or "\\" in path or ":" in path
                or any(part in (".", "..", "") for part in path.split("/"))
                or parsed.suffix.lower() != ".md"
                or parsed.parts[0] in (".obsidian", ".trash")):
            raise ValueError("--path must be a vault-relative Markdown note path.")
    if (line is None) == (text is None):
        raise ValueError("Supply exactly one of --line or --text.")
    if line is not None and (type(line) is not int or line < 1):
        raise ValueError("--line must be a positive, 1-based source line number.")
    if text is not None and not text.strip():
        raise ValueError("--text must be a nonempty literal phrase from the note.")
    if context is not None and (text is None or text not in context):
        raise ValueError("--context requires --text and must contain that exact text.")


def build_code(path=None, line=None, text=None, context=None):
    payload = {"path": path, "line": line, "text": text, "context": context}
    return JAVASCRIPT.replace("__INPUT__", json.dumps(payload, ensure_ascii=True), 1)


def parse_cli_result(output, error_output=""):
    """An Obsidian CLI Error is failure even when its exit status is zero."""
    lines = (output + "\n" + error_output).splitlines()
    for raw in lines:
        candidate = raw.removeprefix("=> ").strip()
        if candidate.startswith("Error:"):
            raise ValueError(candidate)
    for raw in output.splitlines():
        candidate = raw.removeprefix("=> ").strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, str):
                value = json.loads(value)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get("tag") == RESULT_TAG:
            return value
    raise ValueError("Obsidian did not return a navigation result. Check that the selected vault is open and CLI is enabled.")


def navigate(vault=None, path=None, line=None, text=None, context=None):
    validate_request(vault, path, line, text, context)
    command = ["obsidian"]
    if vault is not None:
        command.append(f"vault={vault}")
    command.extend(["eval", f"code={build_code(path, line, text, context)}"])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                               check=False, timeout=30)
    if completed.returncode:
        raise RuntimeError(f"Obsidian CLI failed with exit status {completed.returncode}.")
    return parse_cli_result(completed.stdout, completed.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", help="Vault name; omit to use the CLI's current vault")
    parser.add_argument("--path", help="Exact vault-relative .md path; omit for the active note")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--line", type=int, help="Positive, 1-based original Markdown line")
    target.add_argument("--text", help="Exact literal original text, including any Markdown syntax")
    parser.add_argument("--context", help="Unique exact surrounding text containing --text")
    args = parser.parse_args()
    try:
        result = navigate(args.vault, args.path, args.line, args.text, args.context)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        result = {"tag": RESULT_TAG, "ok": False, "path": args.path, "line": args.line,
                  "mode": None, "search": None, "opened_note": False,
                  "error": "cli_error", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
