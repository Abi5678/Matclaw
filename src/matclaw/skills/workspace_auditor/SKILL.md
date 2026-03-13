# Workspace Auditor

## Purpose
Prevent "Out of Memory" crashes and "License Not Found" errors by scanning the MATLAB workspace and license state before heavy computations or before the user runs large scripts.

## When to Use
- **User asks** about memory, workspace, variables, or license.
- **Before running** a large simulation or script (proactive audit).
- **After an error** that might be OOM or license-related (e.g. "License checkout failed").

## Inputs
- None required. Optional: `warn_if_bytes_above` (number) to flag variables larger than this many bytes.

## Outputs
- `variables`: list of `{ name, bytes, class }` for each workspace variable.
- `total_bytes`: total workspace memory in bytes.
- `license_info`: string or struct from `license('inuse')` (or equivalent).
- `warnings`: list of strings (e.g. "Variable X is > 100MB", "License Toolbox Y in use").

## Constraints
- All MATLAB calls MUST be wrapped in try/except; capture full error stack for the debug agent.
- Use Pydantic models for any data passed between Python and MATLAB.
- Do not modify the user's workspace; read-only.

## RPI Loop
1. **Research**: Run workspace audit (whos + license) via the bridge.
2. **Plan**: Decide if any warnings (OOM risk, license) should be surfaced.
3. **Execute**: Return structured summary to the user (and optionally log).
