---
name: obsidian-cli
description: Interact with a running Obsidian instance using the Obsidian CLI for vault discovery, search and reading, tasks, properties, backlinks, short atomic note mutations, and plugin or theme development. Use for live Obsidian application operations, CLI troubleshooting, screenshots, DOM inspection, and post-write verification. Do not use as the primary skill for authoring, rewriting, or replacing complete Markdown notes; use the local obsidian-markdown skill for full-note content.
---

# Obsidian CLI

Use the `obsidian` CLI to interact with a running Obsidian instance. Requires Obsidian to be open.

## Responsibility boundary

Use the local `obsidian-markdown` skill first whenever the task creates, rewrites, or substantially edits an Obsidian `.md` note. That skill owns frontmatter, wikilinks, embeds, callouts, tags, and the complete note body.

Use this skill to identify the target vault, inspect existing notes, perform short atomic operations, and verify that Obsidian indexed the result. Persist a complete note as a Markdown file with `apply_patch` or a narrowly scoped file copy; do not use CLI `content=` as the transport for the whole document.

CLI-only use remains appropriate for search, read, tasks, backlinks, properties, daily-note operations, plugin reload, JavaScript evaluation, screenshots, and DOM or console inspection.

## Command reference

Run `obsidian help` to see all available commands. This is always up to date. Full docs: https://help.obsidian.md/cli

## Syntax

**Parameters** take a value with `=`. Quote values with spaces:

```bash
obsidian create name="My Note" content="Hello world"
```

**Flags** are boolean switches with no value:

```bash
obsidian create name="My Note" silent overwrite
```

For multiline content use `\n` for newline and `\t` for tab.

## File targeting

Many commands accept `file` or `path` to target a file. Without either, the active file is used.

- `file=<name>` — resolves like a wikilink (name only, no path or extension needed)
- `path=<path>` — exact path from vault root, e.g. `folder/note.md`

## Vault targeting

Commands target the most recently focused vault by default. Use `vault=<name>` as the first parameter to target a specific vault:

```bash
obsidian vault="My Vault" search query="test"
```

## Common patterns

```bash
obsidian read file="My Note"
obsidian create name="New Note" content="# Hello" template="Template" silent
obsidian append file="My Note" content="New line"
obsidian search query="search term" limit=10
obsidian daily:read
obsidian daily:append content="- [ ] New task"
obsidian property:set name="status" value="done" file="My Note"
obsidian tasks daily todo
obsidian tags sort=count counts
obsidian backlinks file="My Note"
```

Use `--copy` on any command to copy output to clipboard. Use `silent` to prevent files from opening. Use `total` on list commands to get a count.

## Complex content safety on Windows

Use `create content=` and `append content=` only for short, simple text. Do not pass a complete Markdown note through one CLI argument when it contains multiline frontmatter, double quotes, wikilinks, callouts, code fences, non-ASCII text, or a large body.

This unsafe pattern can cause the Obsidian main process to reject a malformed IPC payload:

```text
A JavaScript error occurred in the main process
SyntaxError: ... is not valid JSON
at JSON.parse
at Socket...
```

If this occurs:

1. Stop CLI mutation calls; do not retry the same payload or split it into more CLI appends.
2. Check the exact target path for no file, a partial file, or a complete file.
3. Restart Obsidian if later CLI probes also time out.
4. Recreate the note with `obsidian-markdown`, write it as a file, and verify its title/frontmatter and SHA-256.

Separate failures into three layers: CLI executable availability, connection to the running Obsidian instance, and mutation-payload serialization.

## Plugin development

### Develop/test cycle

After making code changes to a plugin or theme, follow this workflow:

1. **Reload** the plugin to pick up changes:
   ```bash
   obsidian plugin:reload id=my-plugin
   ```
2. **Check for errors** — if errors appear, fix and repeat from step 1:
   ```bash
   obsidian dev:errors
   ```
3. **Verify visually** with a screenshot or DOM inspection:
   ```bash
   obsidian dev:screenshot path=screenshot.png
   obsidian dev:dom selector=".workspace-leaf" text
   ```
4. **Check console output** for warnings or unexpected logs:
   ```bash
   obsidian dev:console level=error
   ```

### Additional developer commands

Run JavaScript in the app context:

```bash
obsidian eval code="app.vault.getFiles().length"
```

Inspect CSS values:

```bash
obsidian dev:css selector=".workspace-leaf" prop=background-color
```

Toggle mobile emulation:

```bash
obsidian dev:mobile on
```

Run `obsidian help` to see additional developer commands including CDP and debugger controls.
