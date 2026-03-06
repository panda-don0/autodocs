Central prompts directory for docs-sync-engine.

Recommended convention:
- Keep one file per prompt block or generation pass.
- Track prompt edits here so all consumer repos receive the same behavior after engine ref updates.
- If scripts load prompt files dynamically, keep fallback defaults in code to avoid runtime breaks.
