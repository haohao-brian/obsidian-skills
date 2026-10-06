#!/usr/bin/env python3
"""Highlight one exact prose span in a running Obsidian vault via its CLI."""

import argparse
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys


RESULT_TAG = "obsidian-highlight-v1"

# Targeting happens inside process(), rather than against an earlier CLI read.
# These conservative guards cover common Markdown hazards, not every extension.
JAVASCRIPT = r"""
(async () => {
  const input = __INPUT__;
  const fail = (error, message) => { throw { error, message }; };
  const result = (fields) => JSON.stringify({ tag: 'obsidian-highlight-v1', ...fields });
  try {
    const file = app.vault.getAbstractFileByPath(input.path);
    if (!file || file.extension?.toLowerCase() !== 'md')
      fail('missing_note', 'The exact Markdown note path was not found.');
    const editors = () => app.workspace.getLeavesOfType('markdown')
      .filter(leaf => leaf.view.file?.path === input.path && leaf.view.editor)
      .map(leaf => leaf.view.editor.getValue());
    const checkEditors = (data) => {
      if (editors().some(value => value !== data))
        fail('unsaved_editor', 'An open editor differs from the saved note. Save it, then retry.');
    };
    const occurrences = (data, text) => {
      const found = [];
      for (let pos = data.indexOf(text); pos !== -1; pos = data.indexOf(text, pos + 1))
        found.push(pos);
      return found;
    };
    const protectedRanges = (data) => {
      const ranges = [];
      const lines = data.match(/.*(?:\r?\n|$)/g).filter(Boolean);
      let offset = 0, fence = null, frontmatter = false;
      lines.forEach((line, index) => {
        const plain = line.replace(/\r?\n$/, '').replace(/^\uFEFF/, '');
        if (index === 0 && plain === '---') frontmatter = true;
        if (frontmatter) {
          ranges.push([offset, offset + line.length]);
          if (index > 0 && /^(---|\.\.\.)\s*$/.test(plain)) frontmatter = false;
        } else {
          const marker = plain.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
          if (fence) {
            ranges.push([offset, offset + line.length]);
            if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length
                && marker[2].trim() === '') fence = null;
          } else if (marker) {
            fence = marker[1];
            ranges.push([offset, offset + line.length]);
          } else if (/^( {4}|\t)/.test(plain)) {
            ranges.push([offset, offset + line.length]);
          }
        }
        offset += line.length;
      });
      for (const pattern of [/<!--[\s\S]*?(?:-->|$)/g, /%%[\s\S]*?(?:%%|$)/g,
                             /(`+)[\s\S]*?\1/g,
                             /^ {0,3}\[[^\]\r\n]+\]:[^\r\n]*(?:\r?\n|$)/gm]) {
        for (const match of data.matchAll(pattern)) ranges.push([match.index, match.index + match[0].length]);
      }
      return ranges;
    };
    const linkRanges = (data) => {
      const ranges = [];
      const referenceLabel = text => text.trim().replace(/\s+/g, ' ').toLowerCase();
      const references = new Set(Array.from(data.matchAll(/^ {0,3}\[([^\]\r\n]+)\]:/gm),
        match => referenceLabel(match[1])));
      for (const pattern of [/!?\[\[[^\r\n]*?\]\]/g,
                             /<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^<>\s]*|[^<>\s@]+@[^<>\s@]+)>/g]) {
        for (const match of data.matchAll(pattern)) ranges.push([match.index, match.index + match[0].length]);
      }
      // Balance delimiters only to guard link tokens; this is not a Markdown renderer.
      const closing = (start, opening, end) => {
        let depth = 1, quote = null;
        for (let i = start + 1; i < data.length && data[i] !== '\n'; ++i) {
          if (data[i] === '\\') { ++i; continue; }
          if (quote) { if (data[i] === quote) quote = null; continue; }
          if (opening === '(' && /["']/.test(data[i]) && /\s/.test(data[i - 1])) {
            quote = data[i]; continue;
          }
          if (data[i] === opening) ++depth;
          if (data[i] === end && --depth === 0) return i;
        }
        return -1;
      };
      for (const match of data.matchAll(/!?\[/g)) {
        const bracket = match.index + (match[0][0] === '!' ? 1 : 0);
        const labelEnd = closing(bracket, '[', ']');
        if (labelEnd < 0) continue;
        const next = data[labelEnd + 1];
        if (next !== '(' && next !== '[') {
          if (references.has(referenceLabel(data.slice(bracket + 1, labelEnd))))
            ranges.push([match.index, labelEnd + 1]);
          continue;
        }
        const tokenEnd = closing(labelEnd + 1, next, next === '(' ? ')' : ']');
        if (tokenEnd >= 0) ranges.push([match.index, tokenEnd + 1]);
      }
      return ranges;
    };
    const plan = (data) => {
      let positions = occurrences(data, input.text);
      if (input.context) {
        // Accept only the exact supplied context and versions with this target
        // wrapped once. Existing syntax elsewhere in the context stays literal.
        const variants = [input.context];
        for (const pos of occurrences(input.context, input.text))
          variants.push(input.context.slice(0, pos) + '==' + input.text + '=='
            + input.context.slice(pos + input.text.length));
        const contexts = variants.flatMap(variant => occurrences(data, variant)
          .map(contextStart => [contextStart, contextStart + variant.length]));
        positions = positions.filter(start => contexts.some(([a, b]) =>
          start >= a && start + input.text.length <= b));
      }
      if (!positions.length) fail('text_not_found', 'The exact text was not found in the requested context.');
      if (positions.length !== 1) fail('ambiguous_text', 'The text occurs more than once. Provide a unique surrounding context.');
      const start = positions[0], end = start + input.text.length;
      const protectedSpans = protectedRanges(data);
      const overlaps = ([a, b]) => start < b && end > a;
      if (protectedSpans.some(overlaps))
        fail('unsafe_target', 'The target touches frontmatter, code, or a hidden comment. Choose prose only.');
      if (linkRanges(data).some(([a, b]) => overlaps([a, b]) && !(start <= a && end >= b)))
        fail('partial_link', 'The target cuts through a link. Include its complete Markdown token or choose surrounding prose.');
      const escaped = pos => {
        let count = 0;
        for (let i = pos - 1; i >= 0 && data[i] === '\\'; --i) ++count;
        return count % 2 === 1;
      };
      const marks = occurrences(data, '==').filter(pos => !escaped(pos)
        && !protectedSpans.some(([a, b]) => pos >= a && pos < b));
      for (let i = 0; i < marks.length; i += 2) {
        const a = marks[i], b = i + 1 < marks.length ? marks[i + 1] + 2 : data.length;
        if (start === a + 2 && end === b - 2 && i + 1 < marks.length)
          return { content: data, already_highlighted: true };
        if (overlaps([a, b]) || end === a || start === b)
          fail('overlapping_highlight', 'The target overlaps or adjoins an existing highlight. Choose an unmarked whole span.');
      }
      if (escaped(start) || escaped(end) || data[start - 1] === '=' || data[end] === '=')
        fail('unsafe_boundary', 'The target boundary touches an escape or an equals sign. Choose a complete prose span.');
      return { content: data.slice(0, start) + '==' + input.text + '==' + data.slice(end),
               already_highlighted: false };
    };
    let planned, original;
    if (input.dry_run) {
      original = await app.vault.read(file);
      checkEditors(original);
      planned = plan(original);
    } else {
      // An unsaved buffer must never be replaced by a disk-based transformation.
      checkEditors(await app.vault.read(file));
      await app.vault.process(file, current => {
        checkEditors(current);
        original = current;
        planned = plan(current);
        return planned.content;
      });
      if (await app.vault.read(file) !== planned.content)
        fail('verification_failed', 'The saved note changed during verification. Inspect the note before retrying.');
    }
    return result({ ok: true, path: input.path, changed: !input.dry_run && planned.content !== original,
      already_highlighted: planned.already_highlighted, dry_run: input.dry_run,
      would_change: planned.content !== original });
  } catch (error) {
    return result({ ok: false, path: input.path, error: error.error || 'obsidian_error',
      message: error.message || String(error) });
  }
})()
"""


def validate_request(vault, path, text, context=None):
    """Require explicit note targeting and literal original prose."""
    parsed = PurePosixPath(path)
    if not vault.strip():
        raise ValueError("--vault must identify a vault explicitly.")
    if (not path or parsed.is_absolute() or "\\" in path or ":" in path
            or any(part in (".", "..", "") for part in path.split("/"))
            or parsed.suffix.lower() != ".md"
            or parsed.parts[0] in (".obsidian", ".trash")):
        raise ValueError("--path must be a vault-relative Markdown note path.")
    if not text.strip() or "==" in text or re.search(r"\n[ \t]*\r?\n", text):
        raise ValueError("--text must be unmarked, nonempty prose from one paragraph.")
    if context is not None and text not in context:
        raise ValueError("--context must contain the exact --text.")


def build_code(path, text, context=None, dry_run=False):
    payload = {"path": path, "text": text, "context": context, "dry_run": dry_run}
    return JAVASCRIPT.replace("__INPUT__", json.dumps(payload, ensure_ascii=True), 1)


def parse_cli_result(output):
    """Obsidian can print an error despite exiting successfully."""
    for line in output.splitlines():
        candidate = line.removeprefix("=> ").strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, str):
                value = json.loads(value)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get("tag") == RESULT_TAG:
            return value
    raise ValueError("Obsidian did not return a highlight result. Check that the selected vault is open and CLI is enabled.")


def highlight(vault, path, text, context=None, dry_run=False):
    validate_request(vault, path, text, context)
    completed = subprocess.run(
        ["obsidian", f"vault={vault}", "eval", f"code={build_code(path, text, context, dry_run)}"],
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=30,
    )
    if completed.returncode:
        raise RuntimeError(f"Obsidian CLI failed with exit status {completed.returncode}.")
    return parse_cli_result(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--path", required=True, help="Exact vault-relative .md path")
    parser.add_argument("--text", required=True, help="Exact original prose to highlight")
    parser.add_argument("--context", help="Exact surrounding text containing --text")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = highlight(args.vault, args.path, args.text, args.context, args.dry_run)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        result = {"ok": False, "path": args.path, "error": "cli_error", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
