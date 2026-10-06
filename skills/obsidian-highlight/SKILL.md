---
name: obsidian-highlight
description: Locate original passages in Obsidian Markdown notes and apply highlights in the vault. Use when the user asks an agent to highlight a paragraph, mark important sentences, or 畫螢光筆/標重點 in Obsidian.
---

# Obsidian Highlight

Find the intended passage and actually save `==highlights==` in the user's note. Preserve the original wording and surrounding content. This skill edits Markdown notes; PDF annotations require a PDF workflow.

Requires a running Obsidian instance with its CLI enabled and Python 3. The bundled helper performs the edit through Obsidian's Vault API.

## Locate the note and passage

1. Resolve the vault and note from the user's title, path, current note, or reading context. For “this paragraph,” inspect the active note and editor selection first:

   ```bash
   obsidian eval 'code=JSON.stringify({vault:app.vault.getName(),path:app.workspace.getActiveFile()?.path,selection:app.workspace.activeEditor?.editor?.getSelection()??""})'
   ```

   With a named vault, put `vault=<name>` before the command. Read the resolved note with `obsidian vault="My Vault" read path="folder/note.md"`. Search that vault when the note is named indirectly; open and read the matching note rather than treating search snippets as the full passage.

2. Match the user's selected text or quoted sentence to its exact original text. When the user describes an idea instead of quoting it, read the relevant section, identify the sentence that expresses that idea, and use its original wording. Correct obvious speech transcription errors by matching the note, not by rewriting it.

3. Establish a unique location before editing. If the sentence repeats, pass a distinctive surrounding paragraph as `--context`. If the note or passage remains ambiguous, ask one focused question identifying the candidate headings or passages. An empty selection alone is not ambiguity when the reading context already identifies the passage.

When the user asks the agent to choose key points, highlight the smallest passages that carry the requested claims or definitions within the requested section. In a stepwise reading session, work on the current section and advance when the user requests the next one.

## Apply the highlights

Use the bundled helper at `scripts/highlight.py`, resolving its absolute location from this skill's directory:

```bash
python3 /path/to/obsidian-highlight/scripts/highlight.py \
  --vault "My Vault" \
  --path "folder/note.md" \
  --text "The exact original sentence."
```

For repeated text, add `--context "A unique surrounding paragraph containing the exact sentence."`. Use `--dry-run` when a preview is requested. Pass arguments as a structured subprocess list when the note contains quotes, backticks, dollar signs, or newlines; note text must not become shell code.

Apply separate highlights for separate passages. Keep YAML properties, code, links, comments, and paragraph structure intact. Retain existing highlights; highlighting an already marked passage should make no change. Use Obsidian's native highlight appearance unless the user separately requests custom styling.

The helper resolves against current content using `app.vault.process`, rejects absent or ambiguous matches and unsafe ranges, and verifies the saved result. If an open editor has unsaved changes, preserve that buffer and have it saved before retrying. A helper error is a failed edit, even if the underlying CLI exits successfully.

## Verify and report

Read back the edited range. Confirm that only the requested original passages gained highlight markers and that a repeated invocation does not add nested markers. If showing the result in Obsidian, inspect its Reading view or Live Preview and distinguish saved markup from a visually verified highlight.

Report the note and heading, what was highlighted, and whether it was saved or already highlighted. A returned Markdown string alone does not complete a request to edit a vault note. Put any explanation beside the passage only when the user asks for annotations; otherwise leave the note's prose unchanged.

## Sources

- [Obsidian highlight syntax](https://help.obsidian.md/syntax)
- [Obsidian CLI](https://help.obsidian.md/cli)
- [Vault processing and concurrent edits](https://docs.obsidian.md/Plugins/Vault)
