---
name: obsidian-navigate
description: Navigate the Obsidian desktop app to a Markdown source line or find a keyword or original sentence and scroll to its location. Use for 跳到第幾行、搜尋後跳轉、捲到某句 in a note.
---

# Obsidian Navigate

Move the user's Obsidian reading or editing view to the requested passage. This changes the displayed position and leaves note content intact. Requires a running Obsidian instance with its CLI enabled and Python 3.

## Resolve the destination

For the current note, inspect the active file and vault:

```bash
obsidian eval 'code=JSON.stringify({vault:app.vault.getName(),path:app.workspace.getActiveFile()?.path})'
```

For a named note or an ongoing reading session, resolve that note's vault-relative Markdown path with the Obsidian CLI rather than assuming the currently active tab is the intended file. Read the note when the user describes an idea rather than quoting its words. The helper can open a specifically requested note and bring its destination into view.

When a section is named, locate its heading and read that section first. Identify the requested paragraph and its original key sentence; use the surrounding paragraph as `--context` if the sentence repeats elsewhere. Without a usable section cue, search the whole note for the original phrase. A supplied line number can be used directly even when no section is identified; an absent phrase without a supplied line still needs a new location cue.

- **Line number:** use the user's 1-based Markdown source line. Wrapped lines in Reading view are not source line numbers.
- **Keyword or sentence:** use a literal phrase from the original Markdown. For an idea such as “the reproduction goal,” identify its original wording first. The helper searches the current content and computes the matching source line before scrolling.
- **Repeated text:** use a unique surrounding paragraph with `--context`. If context still leaves multiple destinations, ask which heading or occurrence the user means.

For the active note in Editing view, positions refer to its current editor buffer, including unsaved changes. Reading view and other notes resolve against vault content.

## Navigate

Resolve the helper's absolute path from this skill's directory:

```bash
python3 /path/to/obsidian-navigate/scripts/navigate.py --line 50
```

Or search first and jump to the matching line:

```bash
python3 /path/to/obsidian-navigate/scripts/navigate.py \
  --vault "My Vault" \
  --path "folder/note.md" \
  --text "The exact original phrase."
```

Omitting `--path` targets the active note; omitting `--vault` uses the CLI's current vault. For repeated phrases, add `--context "A unique paragraph containing that phrase."`. Pass arguments as a structured subprocess list when they contain quotes, backticks, dollar signs, or newlines.

An absent or ambiguous phrase, missing file, or invalid line is a failed navigation. Resolve the destination before moving the view. Keep the current Markdown view mode; opening a specifically requested different note is part of navigation. Navigation does not require adding highlight markers or saving an editor draft.

During a reading session where the user requested highlighting and navigation, finish the full workflow for each requested passage: identify the original sentence, use `obsidian-highlight` to mark and verify it, then navigate to that same phrase in the same note and confirm it is visible. This applies both to section-guided lookup and to a whole-note search; a line number is an intermediate location, not the end of that reading workflow. For a supplied line, read that source line to identify the original prose to highlight. Honor a subsequent request for navigation only as navigation only.

If disambiguation uses `--context`, read it again after highlighting: native `==...==` markers change the literal Markdown context, even though the sentence's words stay the same.

## Verify and report

Check the helper's returned note, source line, and mode. Inspect Reading view when the user needs to see the passage; successful line resolution alone does not establish that a sentence is visible. Report the phrase and 1-based line reached.

Reading view uses Obsidian's preview scroll position. It has been tested with source-line positions in Obsidian 1.13.7, but the public API does not define a scroll-position-to-source-line mapping. A sentence may appear within its rendered paragraph rather than at the viewport's first pixel. If the installed version fails to display the target, report that limitation instead of calling the jump successful.

## Sources

- [Obsidian CLI](https://help.obsidian.md/cli)
- [Obsidian public API types](https://github.com/obsidianmd/obsidian-api/blob/master/obsidian.d.ts)
