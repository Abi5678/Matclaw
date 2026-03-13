# Report Generator

## Purpose
Compile a **Mobile Report** from MemoryManager: all "lessons learned" for a project plus the latest plot into a single Markdown file (or PDF). Send the file to the user via the Telegram gateway.

## When to Use
- User asks for a project report or summary.
- After a session: "Send me the report for project X."
- Remote command (e.g. `/report ProjectID`).

## Inputs
- **project_id** (or **query**): string used to query MemoryManager (e.g. "January project", "noise-filtering constants", or a formal ID).
- **output_format**: `"markdown"` (default) or `"pdf"` (optional; requires extra dependency).
- **telegram_handler**: TelegramHandler instance to send the file (or None to only generate file).

## Outputs
- **report_path**: path to the generated .md (or .pdf) file.
- **sent**: whether the file was sent via Telegram.
- **summary**: short description of what was included.

## Constraints
- Use Pydantic for inputs/outputs.
- If generating PDF, do not overwrite user files without .bak (per .cursorrules).
- All data from MemoryManager must be read-only.

## RPI Loop
1. **Research**: Query MemoryManager with project_id/query; gather lessons and any artifact metadata (e.g. plot paths).
2. **Plan**: Decide report sections (lessons learned, latest plot reference, timestamps).
3. **Execute**: Write Markdown file (and optionally convert to PDF), then send via Telegram send_document (or send_alert with path).
