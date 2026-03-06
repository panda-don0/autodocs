import argparse
import base64
import html
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import requests
import yaml


CONFIG_PATH = "docs-sync-config.yml"
MODEL_OUTPUT_DELIMITER = "CONFLUENCE_SUMMARY:"
REQUEST_FILES_MARKER = "REQUEST_FILES:"
DEFAULT_MAX_REQUEST_FILES = 12
NO_UPDATE_MARKER = "NO_UPDATE"
SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".envrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_PATH_TOKENS = [
    "/.env",
    "/secrets/",
    "/secret/",
    "/vault/",
    "/credentials/",
    "/private/",
    "/keys/",
]
SENSITIVE_SUFFIXES = [
    ".pem",
    ".p12",
    ".pfx",
    ".key",
]
TECH_README_SCRATCH_GUIDANCE = (
    "If technical-readme.md is missing/empty, generate it from scratch using these rules:\n"
    "Goal: produce a high-context, implementation-accurate technical baseline specific to this repository and runtime/deployment model.\n"
    "Requirements:\n"
    "1) Inspect repository context provided (code/config/workflows/docs/scripts) before drafting.\n"
    "2) Use only facts inferable from repository context; if unclear, do not invent details.\n"
    "3) Do not include secrets or secret values.\n"
    "4) Write as a natural-flow technical guide, not a checklist dump.\n"
    "5) Be concrete: components, deployment behavior, auth/authz, env vars (names only), data flows, operational behavior.\n"
    "6) Include only relevant sections; omit irrelevant ones.\n"
    "7) Call out actual gaps when true (tests/health/monitoring etc).\n"
    "8) Keep language concise and operationally useful.\n"
    "9) Ensure consistency: no unsupported claims, no placeholder fluff, no contradictions.\n"
    "Suggested structure (adapt as needed):\n"
    "- System purpose/business context\n"
    "- Runtime architecture\n"
    "- End-to-end request/data flow\n"
    "- Authentication/authorization\n"
    "- External dependencies/integrations\n"
    "- Environment/stage differences\n"
    "- Configuration/environment variables\n"
    "- Local development\n"
    "- Build/deploy/release\n"
    "- Logging/monitoring/error handling/health checks\n"
    "- Security/network constraints\n"
    "- Backup/rollback/recovery\n"
    "- Known limitations/common failure points\n"
    "- Troubleshooting\n"
    "Quality bar:\n"
    "- If a maintainer reads only this file, they should understand how to run, deploy, operate, and debug the system without reverse-engineering the codebase.\n"
    "- No generic cloud-agnostic filler if the repository is clearly platform-specific.\n"
)
SENSITIVE_DATA_POLICY_BLOCK = (
    "Sensitive-data policy:\n"
    "- Never request, include, or infer secret values.\n"
    "- Treat .env and credential/key files as forbidden even if listed in repository files.\n"
)
ASSUMPTION_POLICY_BLOCK = (
    "Assumption policy:\n"
    "- Do not assume meanings of acronyms, service names, domains, organizations, or business context.\n"
    "- If uncertain or not directly inferable from provided repository context, do not define or expand the term.\n"
    "- Keep uncertain terms as-is and continue using surrounding context without inventing details.\n"
    "- Never expand an acronym unless the expansion is explicitly present in provided context.\n"
)
FINAL_OUTPUT_CONTENT_POLICY_BLOCK = (
    "Final output content policy:\n"
    "- Technical readme must be implementation-accurate engineering documentation of current state.\n"
    "- Confluence summary must be high-level, less technical, focused on what the service does and how user/admin personas use it.\n"
    "- Do NOT mention PRs, commits, diffs, changelogs, or automation internals.\n"
    "- Do NOT mention docs-sync, this script, or CI automation in final docs.\n"
    "- Use PR diff only as change signal, never as narrative output.\n"
    "- If bootstrap mode is true, generate complete initial content.\n"
    "- If bootstrap mode is false, update only sections affected by meaningful service changes.\n"
)
SECTION_FORMAT_POLICY_BLOCK = (
    "Per-target format policy:\n"
    f"- Technical section (before {MODEL_OUTPUT_DELIMITER}) must be plain markdown text for technical-readme.md.\n"
    "- Technical section must NOT be wrapped in an outer markdown code fence.\n"
    f"- Confluence section (after {MODEL_OUTPUT_DELIMITER}) must be Confluence storage XHTML/HTML content.\n"
    "- Confluence section must use HTML-style tags for structure (for example: <p>, <h2>, <ul>, <li>, <table>, <tr>, <th>, <td>).\n"
    "- Confluence section must NOT use markdown formatting syntax (no **bold**, no # headings, no pipe-table markdown).\n"
)
NO_META_OUTPUT_POLICY_BLOCK = (
    "No process/rationale text policy:\n"
    "- Final documentation content must contain only the target document body.\n"
    "- Do NOT include planning, reasoning, or response meta text.\n"
    "- Do NOT include first-person assistant narration (for example: 'I have enough context', 'I will', 'I cannot').\n"
    "- Do NOT include control/protocol tokens in prose (for example: REQUEST_FILES, NO_UPDATE, delimiter explanations).\n"
    "- Do NOT include lead-in/explanatory lines before the document body.\n"
    "- Technical section must begin immediately with a markdown heading line ('# ...').\n"
    "- Confluence section must begin immediately with storage XHTML/HTML tag content ('<...>').\n"
)
MISSING_CONTEXT_POLICY_BLOCK = (
    "Missing-context policy:\n"
    "- In both pass planning and final output, account for dependencies that may live outside this repository.\n"
    "- Treat missing-context notes already present in existing technical/confluence content as prior flags from earlier runs.\n"
    "- If you identify dependency context not present in this repository/mapping, add a concise "
    "'Missing Context / External Dependencies' note in technical output.\n"
    "- If service-mapping.yml changed in this PR, assume new dependency context may have been provided and "
    "perform an additional explicit re-check of previously flagged items against updated mapping + provided files/context.\n"
    "- For each previously flagged item after that re-check: if context is now sufficient, update the relevant "
    "documentation and remove the flag; if still insufficient, keep the flag concise and specific.\n"
)


def strict_output_rules_pass1(max_request_files: int) -> str:
    return (
        "Strict output formatting rules (must follow exactly):\n"
        f"- If requesting files, first non-empty line must be exactly: {REQUEST_FILES_MARKER}\n"
        "- Then only bullet file paths, one per line, prefixed with '- '.\n"
        f"- You may list more than {max_request_files}; the system keeps only the first {max_request_files}.\n"
        f"- If returning final output, include exactly one delimiter token: {MODEL_OUTPUT_DELIMITER}\n"
        f"- If no updates required, output exactly: {NO_UPDATE_MARKER}\n"
        "- Do not wrap whole response in code fences.\n"
    )


def strict_output_rules_pass2() -> str:
    return (
        "Strict output formatting rules (must follow exactly):\n"
        f"- Return either exactly {NO_UPDATE_MARKER}, OR exactly two sections separated once by {MODEL_OUTPUT_DELIMITER}.\n"
        "- In two-section mode: first section is technical markdown, second section is Confluence-ready summary.\n"
        f"- For single-target changes, set the unchanged section to exactly {NO_UPDATE_MARKER}.\n"
        "- Do not add extra section markers, JSON wrappers, XML wrappers, or leading explanatory text.\n"
        "- Do not wrap the full response in markdown code fences.\n"
    )


def is_truthy_env(name: str) -> bool:
    value = os.getenv(name, "")
    return value.lower() in {"1", "true", "yes", "on"}


DEBUG_ENABLED = is_truthy_env("DOCS_SYNC_DEBUG")


def debug(message: str) -> None:
    if DEBUG_ENABLED:
        print(f"[docs_sync][debug] {message}")


def preview(value: str, length: int = 400) -> str:
    text = value.replace("\n", "\\n")
    if len(text) <= length:
        return text
    return f"{text[:length]}...<truncated>"


def fail(message: str) -> None:
    print(f"[docs_sync] ERROR: {message}")
    sys.exit(1)

def warn(message: str) -> None:
    print(f"[docs_sync] WARNING: {message}")

def start_group(title: str) -> None:
    print(f"::group::{title}")

def end_group() -> None:
    print("::endgroup::")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        fail(f"Required file is missing: {path}")
    return path.read_text(encoding="utf-8")


def read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    if not path.is_file():
        fail(f"Expected file path but found non-file: {path}")
    return path.read_text(encoding="utf-8")


def limited(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars]


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        fail(f"Environment variable {name} must be an integer if set.")
    if value <= 0:
        fail(f"Environment variable {name} must be > 0.")
    return value

def build_update_reference(pr_number: str, commit_sha: str) -> str:
    short_sha = commit_sha[:12] if commit_sha else ""
    if pr_number and short_sha:
        return f"PR {pr_number} / commit {short_sha}"
    if pr_number:
        return f"PR {pr_number}"
    if short_sha:
        return f"commit {short_sha}"
    return "unknown ref"


def strip_markdown_signature(text: str) -> str:
    pattern = re.compile(
        r"\n---\n_Updated by auto-docs from repo: .*? and (?:PR|commit|unknown ref).*?_\s*\Z",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(pattern, "", text).rstrip()


def append_markdown_signature(text: str, repo: str, update_ref: str) -> str:
    core = strip_markdown_signature(text).strip()
    return f"{core}\n\n---\n_Updated by auto-docs from repo: {repo} and {update_ref}_\n"


def strip_confluence_signature(text: str) -> str:
    pattern = re.compile(
        r"\s*<hr\s*/?>\s*<p><em>Updated by auto-docs from repo: .*? and (?:PR|commit|unknown ref).*?</em></p>\s*\Z",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(pattern, "", text).rstrip()


def append_confluence_signature(text: str, repo: str, update_ref: str) -> str:
    core = strip_confluence_signature(text).strip()
    safe_repo = html.escape(repo)
    safe_ref = html.escape(update_ref)
    signature = (
        "<hr />"
        f"<p><em>Updated by auto-docs from repo: {safe_repo} and {safe_ref}</em></p>"
    )
    return f"{core}\n{signature}"


def is_first_write_confluence_content(body: str) -> bool:
    normalized = body.strip()
    if not normalized:
        return True
    html_without_whitespace = re.sub(r"\s+", "", normalized)
    empty_storage_patterns = [
        r"^<p[^>]*/>$",  # e.g. <p local-id="..." />
        r"^<p[^>]*></p>$",  # e.g. <p></p>
        r"^<br/?>$",  # e.g. <br> or <br/>
        r"^<p[^>]*><br/?></p>$",
    ]
    if any(re.match(pattern, html_without_whitespace, flags=re.IGNORECASE) for pattern in empty_storage_patterns):
        return True
    upper = normalized.upper()
    placeholder_tokens = ["TEST", "PLACEHOLDER", "TBD", "TODO", "DUMMY", "LOREM IPSUM"]
    return any(token in upper for token in placeholder_tokens)


def confluence_headers(user_email: str, api_token: str) -> dict:
    token = base64.b64encode(f"{user_email}:{api_token}".encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def fetch_confluence_page(base_url: str, headers: dict, page_id: str) -> tuple[str, int, str]:
    print(f"[docs_sync] Fetching Confluence page {page_id}.")
    endpoint = f"{base_url}/wiki/rest/api/content/{page_id}?expand=body.storage,version,title"
    debug(f"Confluence GET endpoint: {endpoint}")
    response = requests.get(endpoint, headers=headers, timeout=30)
    if response.status_code == 404:
        fail(f"Confluence page {page_id} does not exist.")
    if response.status_code >= 400:
        fail(f"Confluence fetch failed for page {page_id}: HTTP {response.status_code} {response.text}")
    data = response.json()
    title = data.get("title")
    body = ((data.get("body") or {}).get("storage") or {}).get("value")
    version_number = ((data.get("version") or {}).get("number"))
    if not title or not isinstance(title, str):
        fail(f"Confluence page {page_id} response missing title.")
    if not isinstance(body, str):
        fail(f"Confluence page {page_id} response missing body.storage.value.")
    if not isinstance(version_number, int):
        fail(f"Confluence page {page_id} response missing version.number.")
    return title, version_number, body


def update_confluence_page(base_url: str, headers: dict, page_id: str, title: str, new_body: str, version: int) -> None:
    print(f"[docs_sync] Updating Confluence page {page_id} to version {version + 1}.")
    endpoint = f"{base_url}/wiki/rest/api/content/{page_id}"
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "body": {"storage": {"value": new_body, "representation": "storage"}},
        "version": {"number": version + 1},
    }
    debug(f"Confluence PUT endpoint: {endpoint}")
    debug(
        "Confluence PUT payload metadata: "
        f"id={payload['id']}, "
        f"title={payload['title']!r}, "
        f"version={payload['version']['number']}, "
        f"body_chars={len(new_body)}"
    )
    debug(f"Confluence PUT payload body preview: {preview(new_body, 2000)}")
    response = requests.put(endpoint, headers=headers, json=payload, timeout=30)
    if response.status_code >= 400:
        fail(f"Confluence update failed for page {page_id}: HTTP {response.status_code} {response.text}")
    print(f"[docs_sync] Updated Confluence page {page_id} successfully.")


def call_claude_via_anthropic(api_key: str, model: str, prompt: str) -> str:
    print(f"[docs_sync] Calling Claude model {model}.")
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 3000,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if response.status_code >= 400:
        fail(f"Claude call failed: HTTP {response.status_code} {response.text}")
    payload = response.json()
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        fail("Claude response did not include content blocks.")
    first = content[0]
    text = first.get("text") if isinstance(first, dict) else None
    if not isinstance(text, str) or not text.strip():
        fail("Claude response did not include text output.")
    return text


def call_claude_via_claude_code(model: str, prompt: str) -> str:
    command = os.getenv("CLAUDE_CODE_COMMAND", "claude")
    cmd = shlex.split(command) + ["-p", prompt]
    debug(f"Invoking Claude Code command: {command}")
    debug(f"Configured model hint: {model}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError:
        fail(
            "Claude Code CLI command not found. "
            f"Configured CLAUDE_CODE_COMMAND='{command}'."
        )
    except subprocess.TimeoutExpired:
        fail("Claude Code CLI invocation timed out.")

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        fail(
            "Claude Code CLI failed "
            f"(exit={result.returncode}). "
            f"stderr={preview(stderr, 1000)} stdout={preview(stdout, 500)}"
        )

    text = (result.stdout or "").strip()
    if not text:
        fail("Claude Code CLI returned empty output.")
    debug(f"Claude Code output chars: {len(text)}")
    return text


def call_claude(model: str, prompt: str) -> str:
    provider = os.getenv("DOCS_SYNC_LLM_PROVIDER", "claude_code").strip().lower()
    debug(f"Using LLM provider: {provider}")

    if provider == "anthropic":
        anthropic_api_key = require_env("ANTHROPIC_API_KEY")
        return call_claude_via_anthropic(anthropic_api_key, model, prompt)

    if provider == "claude_code":
        require_env("CLAUDE_CODE_OAUTH_TOKEN")
        return call_claude_via_claude_code(model, prompt)

    fail(
        "Unsupported DOCS_SYNC_LLM_PROVIDER. "
        "Expected one of: 'claude_code', 'anthropic'."
    )
    return ""


def technical_filename(service: str, service_count: int) -> str:
    if service_count == 1:
        return "technical-readme.md"
    return f"technical-readme_{service}.md"


def parse_changed_paths_from_diff(diff_text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for line in diff_text.splitlines():
        if not line.startswith("+++ "):
            continue
        raw = line[4:].strip()
        if raw == "/dev/null" or not raw.startswith("b/"):
            continue
        path = raw[2:]
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def is_docs_change_relevant_for_generation(path: str) -> bool:
    return not should_exclude_from_doc_context(path)

def filter_relevant_changed_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if is_docs_change_relevant_for_generation(p)]

def should_exclude_from_doc_context(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    if is_sensitive_path(p):
        return True
    excluded_substrings = [
        "/.git/",
        "/node_modules/",
        "/dist/",
        "/build/",
        "/.next/",
        "/.sst/",
        "/coverage/",
        "/vendor/",
        "/__pycache__/",
        ".github/workflows/docs-sync",
        "scripts/docs_sync.py",
        "scripts/wait_required_checks.py",
        "docs-sync-config.yml",
        ".github/workflows/docs-sync.yml",
        ".git/docs-sync-commit-msg.txt",
        "pr.diff",
        ".gitmessage",
        "service-mapping.yml",
    ]
    if any(token in p for token in excluded_substrings):
        return True
    excluded_suffixes = [
        ".lock",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".exe",
        ".dll",
        ".so",
        ".class",
        ".jar",
        ".map",
    ]
    return any(p.endswith(suffix) for suffix in excluded_suffixes)


def is_sensitive_path(path: str) -> bool:
    p = path.replace("\\", "/").strip().lower()
    if not p:
        return False
    name = Path(p).name
    if name in SENSITIVE_FILE_NAMES:
        return True
    if any(token in f"/{p}" for token in SENSITIVE_PATH_TOKENS):
        return True
    return any(p.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)

def list_repo_files(root: Path, max_entries: int = 200000) -> list[str]:
    files: list[str] = []
    skip_dirs = {".git", "node_modules", ".sst", "dist", "build", ".next", "coverage", "__pycache__", ".venv", "venv"}
    for current_root, dirnames, filenames in os.walk(root):
        rel_root = Path(current_root).relative_to(root).as_posix()
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".pytest")]
        for filename in filenames:
            rel = f"{rel_root}/{filename}" if rel_root != "." else filename
            files.append(rel)
            if len(files) >= max_entries:
                return sorted(files)
    return sorted(files)


def build_repo_tree(root: Path) -> str:
    lines: list[str] = []
    skip_dirs = {".git", "node_modules", ".sst", "dist", "build", ".next", "coverage", "__pycache__", ".venv", "venv"}
    for current_root, dirnames, filenames in os.walk(root):
        rel_root_path = Path(current_root).relative_to(root)
        depth = len(rel_root_path.parts)
        dirnames[:] = [d for d in sorted(dirnames) if d not in skip_dirs]
        indent = "  " * depth
        folder_name = "." if str(rel_root_path) == "." else rel_root_path.name
        lines.append(f"{indent}{folder_name}/")
        for filename in sorted(filenames):
            lines.append(f"{indent}  {filename}")
    return "\n".join(lines)


def sanitize_requested_path(path: str) -> str | None:
    p = path.strip().replace("\\", "/")
    if not p or p in {".", "/"}:
        return None
    if p.startswith("/"):
        return None
    if ".." in Path(p).parts:
        return None
    if is_sensitive_path(p):
        return None
    return p


def parse_requested_files(output_text: str, max_files: int) -> tuple[list[str], bool]:
    marker_index = output_text.find(REQUEST_FILES_MARKER)
    if marker_index == -1:
        return [], False
    block = output_text[marker_index + len(REQUEST_FILES_MARKER):]
    requested: list[str] = []
    seen: set[str] = set()
    blocked_sensitive_count = 0
    invalid_path_count = 0
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("-"):
            continue
        raw_path = stripped[1:].strip()
        if raw_path.upper() in {"NONE", "NO_FILES"}:
            print("[docs_sync] AI pass-1 requested no additional files.")
            return [], True
        clean = sanitize_requested_path(raw_path)
        if not clean:
            if is_sensitive_path(raw_path):
                blocked_sensitive_count += 1
                warn(f"Blocked sensitive file request from AI: {raw_path}")
            else:
                invalid_path_count += 1
            continue
        if clean in seen:
            continue
        seen.add(clean)
        requested.append(clean)
    if blocked_sensitive_count or invalid_path_count:
        warn(
            "AI file request list contained blocked/invalid paths. "
            f"blocked_sensitive={blocked_sensitive_count}, invalid={invalid_path_count}"
        )
    if len(requested) > max_files:
        trimmed_count = len(requested) - max_files
        warn(
            f"AI requested {len(requested)} files in pass-1; trimming to {max_files} "
            f"(dropped {trimmed_count} lowest-priority file request(s))."
        )
        return requested[:max_files], True
    return requested, True


def is_no_update_text(value: str) -> bool:
    compact = value.strip().upper()
    return compact in {"", NO_UPDATE_MARKER, "UNCHANGED", "NONE", "N/A"}


def parse_generation_output(output_text: str) -> tuple[str | None, str | None, str]:
    stripped = normalize_model_markdown_output(output_text).strip()
    if is_no_update_text(stripped):
        return None, None, "no_update"
    if MODEL_OUTPUT_DELIMITER in stripped:
        readme_part, confluence_part = stripped.split(MODEL_OUTPUT_DELIMITER, 1)
        technical = None if is_no_update_text(readme_part) else strip_outer_markdown_fence(readme_part).strip()
        confluence = None if is_no_update_text(confluence_part) else strip_outer_markdown_fence(confluence_part).strip()
        return technical, confluence, "two_section"
    warn(
        "AI response was not in a recognized output format "
        f"(missing {MODEL_OUTPUT_DELIMITER} and not {NO_UPDATE_MARKER}); treating as no update."
    )
    return None, None, "malformed"

def strip_outer_markdown_fence(value: str) -> str:
    text = value.strip()
    fence_match = re.fullmatch(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```", text, flags=re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def normalize_model_markdown_output(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    fenced_blocks = list(re.finditer(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```", text, flags=re.IGNORECASE))
    if len(fenced_blocks) == 1:
        block = fenced_blocks[0]
        outer_prefix = text[:block.start()].strip()
        outer_suffix = text[block.end():].strip()
        if len(block.group(1)) > 200 and not outer_suffix:
            if not outer_prefix or outer_prefix.lower() in {"# technical readme", "technical readme"}:
                return block.group(1).strip()
    return text
def looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx in range(len(lines) - 1):
        first = lines[idx]
        second = lines[idx + 1]
        if "|" in first and re.match(r"^\|?[\-:\s|]+\|?$", second):
            return True
    return False

def first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def find_output_meta_issues(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    issues: list[str] = []
    lower_all = stripped.lower()
    token_patterns = [
        (REQUEST_FILES_MARKER.lower(), f"contains protocol token {REQUEST_FILES_MARKER}"),
        (NO_UPDATE_MARKER.lower(), f"contains protocol token {NO_UPDATE_MARKER}"),
        (MODEL_OUTPUT_DELIMITER.lower(), f"contains delimiter token {MODEL_OUTPUT_DELIMITER}"),
    ]
    for token, reason in token_patterns:
        if token in lower_all:
            issues.append(reason)
    return issues


def validate_no_meta_output(text: str, target_name: str) -> None:
    issues = find_output_meta_issues(text)
    if issues:
        fail(
            f"{target_name} section includes non-document meta/process text: "
            f"{'; '.join(issues)}. Return only the final document body."
        )


def validate_technical_markdown_output(text: str) -> None:
    value = text.strip()
    if not value:
        fail("Model returned empty technical-readme content.")
    if re.fullmatch(r"```(?:markdown|md)?\s*[\s\S]*```", value, flags=re.IGNORECASE):
        fail("Technical-readme section is wrapped in a markdown code fence. Return plain markdown only.")
    first_line = first_non_empty_line(value)
    if not re.match(r"^#{1,6}\s+\S", first_line):
        fail(
            "Technical-readme section must start directly with a markdown heading line ('# ...'). "
            "Do not prepend explanatory/meta text."
        )
    validate_no_meta_output(value, "Technical-readme")


def validate_confluence_storage_output(text: str) -> None:
    value = text.strip()
    if not value:
        fail("Model returned empty Confluence content.")
    validate_no_meta_output(value, "Confluence")
    first_line = first_non_empty_line(value)
    if not first_line.startswith("<"):
        fail(
            "Confluence section must start directly with storage XHTML/HTML content ('<...>'). "
            "Do not prepend explanatory/meta text."
        )
    has_html_tag = bool(re.search(r"<[a-zA-Z][^>]*>", value))
    has_markdown_heading = bool(re.search(r"(?m)^\s{0,3}#{1,6}\s+\S+", value))
    has_markdown_bold = "**" in value
    has_markdown_table = looks_like_markdown_table(value)
    if not has_html_tag:
        fail(
            "Confluence section is not in storage XHTML/HTML format (no HTML tags detected). "
            "Return Confluence content as storage markup, not markdown."
        )
    if has_markdown_heading or has_markdown_bold or has_markdown_table:
        fail(
            "Confluence section includes markdown syntax (**/#/pipe-table). "
            "Return Confluence section using storage XHTML/HTML tags only."
        )


def find_unmapped_dependency_candidates(
    context_text: str,
    mapped_services: set[str],
    current_service: str,
) -> list[str]:
    candidates: set[str] = set()
    stage_suffix_pattern = re.compile(
        r"\b([a-z0-9]+(?:-[a-z0-9]+)+)-(prod|production|test|staging|stage|sandbox|dev)\b",
        flags=re.IGNORECASE,
    )
    for match in stage_suffix_pattern.finditer(context_text):
        candidates.add(match.group(1).lower())
    service_label_pattern = re.compile(
        r"\b([a-z0-9]+(?:-[a-z0-9]+)+)\s+service\b",
        flags=re.IGNORECASE,
    )
    for match in service_label_pattern.finditer(context_text):
        candidates.add(match.group(1).lower())
    lambda_ref_pattern = re.compile(
        r"`([a-z0-9]+(?:-[a-z0-9]+)+)`",
        flags=re.IGNORECASE,
    )
    for match in lambda_ref_pattern.finditer(context_text):
        token = match.group(1).lower()
        if token.endswith("-lambda"):
            token = token[:-7]
        candidates.add(token)
    ignored = {"api-gateway", "cloud-front", "cloud-watch", "node-js", "eu-west"}
    normalized_mapped = {s.lower() for s in mapped_services}
    normalized_current = current_service.lower()
    unresolved = sorted(
        token
        for token in candidates
        if token not in ignored and token != normalized_current and token not in normalized_mapped
    )
    return unresolved



def read_requested_files_context(
    root: Path,
    requested_paths: list[str],
) -> tuple[str, list[str], dict[str, int]]:
    sections: list[str] = []
    loaded: list[str] = []
    stats = {
        "outside_repo": 0,
        "missing": 0,
        "blocked_sensitive": 0,
        "excluded": 0,
        "binary_or_non_utf8": 0,
    }
    for rel in requested_paths:
        abs_path = (root / rel).resolve()
        try:
            abs_path.relative_to(root.resolve())
        except ValueError:
            sections.append(f"FILE: {rel}\n<skipped: outside repository>")
            stats["outside_repo"] += 1
            continue
        if not abs_path.exists() or not abs_path.is_file():
            sections.append(f"FILE: {rel}\n<missing>")
            stats["missing"] += 1
            continue
        if is_sensitive_path(rel):
            sections.append(f"FILE: {rel}\n<blocked: sensitive file policy>")
            stats["blocked_sensitive"] += 1
            continue
        if should_exclude_from_doc_context(rel):
            sections.append(f"FILE: {rel}\n<skipped: excluded by context filters>")
            stats["excluded"] += 1
            continue
        try:
            text = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            sections.append(f"FILE: {rel}\n<skipped: binary/non-utf8>")
            stats["binary_or_non_utf8"] += 1
            continue
        loaded.append(rel)
        sections.append(f"FILE: {rel}\n{text}")
    return "\n\n".join(sections), loaded, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync technical docs and Confluence from PR context.")
    parser.add_argument("--mapping", required=True, help="Path to service-mapping.yml")
    parser.add_argument("--pr-diff", required=True, help="Path to PR diff file")
    args = parser.parse_args()

    print("[docs_sync] Starting documentation sync.")
    config_path = Path(CONFIG_PATH)
    if not config_path.exists():
        fail(f"Missing required config file: {CONFIG_PATH}")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        fail("docs-sync-config.yml must contain a top-level mapping/object.")

    missing_page_policy = config.get("confluence_missing_page_policy")
    if missing_page_policy != "fail":
        fail("confluence_missing_page_policy must be set to 'fail' for strict mode.")

    model = config.get("claude_model")
    if not isinstance(model, str) or not model.strip():
        fail("docs-sync-config.yml must provide a non-empty claude_model string.")

    max_context_chars = config.get("max_context_chars")
    if not isinstance(max_context_chars, dict):
        fail("docs-sync-config.yml must provide max_context_chars map.")
    for key in ["pr_diff", "readme", "technical_readme", "confluence", "mapping"]:
        value = max_context_chars.get(key)
        if not isinstance(value, int) or value <= 0:
            fail(f"max_context_chars.{key} must be a positive integer.")

    mapping_path = Path(args.mapping)
    mapping = yaml.safe_load(read_text(mapping_path))
    if not isinstance(mapping, dict):
        fail("service-mapping.yml must contain a top-level mapping/object.")
    services = mapping.get("services")
    service_pages = mapping.get("service_pages")
    if not isinstance(services, list) or not services:
        fail("service-mapping.yml must define a non-empty 'services' list.")
    if any((not isinstance(service, str) or not service.strip()) for service in services):
        fail("Every services item must be a non-empty string.")
    if not isinstance(service_pages, dict):
        fail("service-mapping.yml must define 'service_pages' as a mapping.")
    for service in services:
        page_id = service_pages.get(service)
        if not isinstance(page_id, str) or not page_id.strip():
            fail(f"service_pages.{service} must be a non-empty Confluence page ID string.")

    repo_root = Path(".").resolve()
    readme_path = Path("README.md")
    readme_text = read_optional_text(readme_path)
    pr_diff_text = read_text(Path(args.pr_diff))
    mapping_text = read_text(mapping_path)

    confluence_base_url = require_env("CONFLUENCE_BASE_URL").rstrip("/")
    confluence_space_key = require_env("CONFLUENCE_SPACE_KEY")
    confluence_user_email = require_env("CONFLUENCE_USER_EMAIL")
    confluence_api_token = require_env("CONFLUENCE_API_TOKEN")
    pr_number = require_env("PR_NUMBER")
    commit_sha = os.getenv("GITHUB_SHA", "").strip()
    repo_name = os.getenv("GITHUB_REPOSITORY", "").strip() or "unknown-repo"
    update_ref = build_update_reference(pr_number, commit_sha)
    debug(
        "Runtime config: "
        f"repo={os.getenv('GITHUB_REPOSITORY', '')}, "
        f"pr_number={pr_number}, "
        f"confluence_base_url={confluence_base_url}, "
        f"confluence_space_key={confluence_space_key}, "
        f"services_count={len(services)}"
    )

    max_request_files = env_int("DOCS_SYNC_MAX_REQUEST_FILES", DEFAULT_MAX_REQUEST_FILES)

    headers = confluence_headers(confluence_user_email, confluence_api_token)
    service_count = len(services)
    changed_paths = parse_changed_paths_from_diff(pr_diff_text)
    relevant_changed_paths = filter_relevant_changed_paths(changed_paths)
    start_group("docs_sync | repository context")
    print(
        "[docs_sync] Stage: collected repo-level change context "
        f"(changed_files={len(changed_paths)}, relevant_files={len(relevant_changed_paths)})."
    )
    mapping_path_token = mapping_path.as_posix().lower()
    mapping_changed_in_diff = any(
        p.replace("\\", "/").lower() == mapping_path_token or p.replace("\\", "/").lower().endswith("/service-mapping.yml")
        for p in changed_paths
    )
    if not relevant_changed_paths:
        print("[docs_sync] No relevant documentation-impacting file changes detected after exclusions; skipping generation.")
        end_group()
        return
    repo_tree = build_repo_tree(repo_root)
    eligible_repo_files = [p for p in list_repo_files(repo_root) if not should_exclude_from_doc_context(p)]
    end_group()

    for service in services:
        start_group(f"docs_sync | {service} | context collection")
        print(f"[docs_sync] Processing service '{service}'.")
        print(
            f"[docs_sync] Stage for '{service}': collecting generation context "
            "(README, technical readme, Confluence page, mapping, diff signal, repository file index)."
        )
        technical_file = Path(technical_filename(service, service_count))
        technical_existing = read_optional_text(technical_file)
        technical_existing_core = strip_markdown_signature(technical_existing)

        page_id = service_pages[service]
        confluence_title, confluence_version, confluence_body = fetch_confluence_page(confluence_base_url, headers, page_id)
        confluence_existing_core = strip_confluence_signature(confluence_body)
        first_write_mode = is_first_write_confluence_content(confluence_body)
        bootstrap_mode = first_write_mode or not technical_existing.strip()
        scratch_guidance_block = f"{TECH_README_SCRATCH_GUIDANCE}\n\n" if bootstrap_mode else ""
        debug(
            f"Doc mode for service '{service}': "
            f"bootstrap_mode={bootstrap_mode}, "
            f"first_write_confluence={first_write_mode}, "
            f"technical_exists={bool(technical_existing.strip())}"
        )
        missing_context_candidates = find_unmapped_dependency_candidates(
            "\n\n".join(
                [
                    readme_text,
                    technical_existing_core,
                    confluence_existing_core,
                    limited(pr_diff_text, max_context_chars["pr_diff"]),
                ]
            ),
            set(services),
            service,
        )
        if missing_context_candidates:
            debug(
                f"Heuristic only: potential unmapped dependency-like terms for '{service}': "
                f"{', '.join(missing_context_candidates)}. "
                "Advisory signal for prompt context, not a runtime/config error."
            )
        missing_context_block = (
            "Potential unmapped cross-service dependencies detected from repo context:\n"
            f"{chr(10).join(f'- {item}' for item in missing_context_candidates)}\n"
            if missing_context_candidates
            else "Potential unmapped cross-service dependencies detected from repo context:\n- <none detected>\n"
        )
        mapping_review_block = (
            "service-mapping.yml changed in this PR: true\n"
            "You must re-review prior missing-context flags and remove any that are now resolved by updated mapping.\n"
            if mapping_changed_in_diff
            else "service-mapping.yml changed in this PR: false\n"
        )
        end_group()

        start_group(f"docs_sync | {service} | ai pass-1")
        pass1_prompt = (
            "You are planning context retrieval for documentation generation.\n"
            "In this pass, you MAY request repository files OR provide final output directly.\n\n"
            "Allowed response forms:\n"
            f"1) File request format:\n{REQUEST_FILES_MARKER}\n"
            "- path/to/file1\n"
            "- path/to/file2\n"
            "...\n"
            "2) Final documentation output format (technical + CONFLUENCE_SUMMARY)\n"
            f"3) {NO_UPDATE_MARKER} (if neither document needs updates)\n\n"
            f"Rules:\n"
            f"- If requesting files, list most important first.\n"
            "- Paths must be relative repository file paths.\n"
            "- Do not request directories.\n"
            "- Never request secrets or sensitive files (.env*, keys, credentials, vault/secrets paths).\n"
            "- If no extra files are needed, provide final output directly or return NO_UPDATE.\n\n"
            f"{strict_output_rules_pass1(max_request_files)}\n"
            "Selection guidance:\n"
            "- Focus on files needed to understand the current service behavior.\n"
            "- Ignore docs automation internals, CI docs-sync plumbing, and changelog wording concerns.\n"
            "- Treat PR diff only as change signal; target current-state documentation.\n\n"
            f"{FINAL_OUTPUT_CONTENT_POLICY_BLOCK}\n"
            f"{SECTION_FORMAT_POLICY_BLOCK}\n"
            f"{NO_META_OUTPUT_POLICY_BLOCK}\n"
            f"{SENSITIVE_DATA_POLICY_BLOCK}\n"
            f"{ASSUMPTION_POLICY_BLOCK}\n"
            f"{MISSING_CONTEXT_POLICY_BLOCK}\n"
            f"{scratch_guidance_block}"
            f"Repository: {os.getenv('GITHUB_REPOSITORY', '')}\n"
            f"Service: {service}\n"
            f"Bootstrap mode: {str(bootstrap_mode).lower()}\n"
            f"Confluence first-write mode: {str(first_write_mode).lower()}\n\n"
            f"{mapping_review_block}\n"
            f"{missing_context_block}\n"
            f"Changed files from PR diff:\\n{limited(chr(10).join(relevant_changed_paths) or '<none>', max_context_chars['pr_diff'])}\n\n"
            f"Repository files (eligible to request):\n{chr(10).join(eligible_repo_files)}\n\n"
            f"Repository tree snapshot:\n{repo_tree}\n\n"
            f"README.md (optional):\n{limited(readme_text, max_context_chars['readme'])}\n\n"
            f"Existing technical readme ({technical_file.name}):\n"
            f"{limited(technical_existing_core, max_context_chars['technical_readme'])}\n\n"
            f"Current Confluence body:\n{limited(confluence_existing_core, max_context_chars['confluence'])}\n"
        )
        debug(f"Pass-1 prompt BEGIN for service '{service}'")
        debug(pass1_prompt)
        debug(f"Pass-1 prompt END for service '{service}'")
        print(f"[docs_sync] AI pass-1 for '{service}': context planning / file request.")

        pass1_output = call_claude(model, pass1_prompt)
        debug(f"Pass-1 output for service '{service}': {preview(pass1_output, 4000)}")
        requested_paths, pass1_requested_files = parse_requested_files(pass1_output, max_request_files)
        debug(f"Requested paths for service '{service}': {requested_paths}")
        pass1_normalized = normalize_model_markdown_output(pass1_output).strip()
        end_group()
        if requested_paths:
            start_group(f"docs_sync | {service} | requested file loading")
            print(
                f"[docs_sync] AI pass-1 for '{service}' requested {len(requested_paths)} file(s): "
                f"{', '.join(requested_paths)}"
            )
            requested_files_context, loaded_paths, request_stats = read_requested_files_context(
                repo_root,
                requested_paths,
            )
            debug(f"Loaded requested file count={len(loaded_paths)} for service '{service}'")
            if request_stats["missing"] or request_stats["outside_repo"] or request_stats["excluded"] or request_stats["binary_or_non_utf8"] or request_stats["blocked_sensitive"]:
                warn(
                    f"Not all requested files were provided for '{service}'. "
                    f"loaded={len(loaded_paths)}, missing={request_stats['missing']}, "
                    f"outside_repo={request_stats['outside_repo']}, excluded={request_stats['excluded']}, "
                    f"binary_or_non_utf8={request_stats['binary_or_non_utf8']}, "
                    f"blocked_sensitive={request_stats['blocked_sensitive']}"
                )
            else:
                print(
                    f"[docs_sync] Requested file load outcome for '{service}': "
                    f"loaded {len(loaded_paths)} of {len(requested_paths)}."
                )
            end_group()

            start_group(f"docs_sync | {service} | ai pass-2")
            pass2_prompt = (
                "You are updating technical and user-facing documentation for the CURRENT state of the service.\n"
                "This is pass 2 (final generation). Do NOT request more files in this pass.\n\n"
                "Return one of the following:\n"
                "1) Two-section output:\n"
                "- technical readme markdown\n"
                f"- {MODEL_OUTPUT_DELIMITER} followed by Confluence-ready summary\n"
                f"2) {NO_UPDATE_MARKER} if neither document needs updates\n"
                f"3) In two-section output, you may set either section to {NO_UPDATE_MARKER} if only one target needs change\n\n"
                f"{strict_output_rules_pass2()}\n"
                f"{FINAL_OUTPUT_CONTENT_POLICY_BLOCK}\n"
                f"{SECTION_FORMAT_POLICY_BLOCK}\n"
                f"{NO_META_OUTPUT_POLICY_BLOCK}\n"
                f"{SENSITIVE_DATA_POLICY_BLOCK}\n"
                f"{ASSUMPTION_POLICY_BLOCK}\n"
                f"{MISSING_CONTEXT_POLICY_BLOCK}\n"
                f"{scratch_guidance_block}"
                f"Repository: {os.getenv('GITHUB_REPOSITORY', '')}\n"
                f"Service: {service}\n"
                f"Confluence Space: {confluence_space_key}\n"
                f"Bootstrap mode: {str(bootstrap_mode).lower()}\n"
                f"Confluence first-write mode: {str(first_write_mode).lower()}\n\n"
                f"{mapping_review_block}\n"
                f"{missing_context_block}\n"
                f"service-mapping.yml:\n{limited(mapping_text, max_context_chars['mapping'])}\n\n"
                f"README.md:\n{limited(readme_text, max_context_chars['readme'])}\n\n"
                f"Existing technical readme ({technical_file.name}):\n"
                f"{limited(technical_existing_core, max_context_chars['technical_readme'])}\n\n"
                f"Current Confluence body:\n{limited(confluence_existing_core, max_context_chars['confluence'])}\n\n"
                f"Changed files from diff (signal only):\\n{limited(chr(10).join(relevant_changed_paths) or '<none>', max_context_chars['pr_diff'])}\n\n"
                f"Requested files provided ({len(loaded_paths)} loaded):\n{requested_files_context}\n"
            )
            debug(f"Pass-2 prompt BEGIN for service '{service}'")
            debug(pass2_prompt)
            debug(f"Pass-2 prompt END for service '{service}'")
            print(f"[docs_sync] AI pass-2 for '{service}': final generation.")
            llm_output = call_claude(model, pass2_prompt)
            response_source = "pass-2"
            end_group()
        else:
            start_group(f"docs_sync | {service} | pass-1 direct outcome")
            if pass1_requested_files:
                print(f"[docs_sync] AI pass-1 for '{service}' requested files marker but no usable file paths.")
            elif is_no_update_text(pass1_normalized):
                print(f"[docs_sync] AI pass-1 for '{service}' returned {NO_UPDATE_MARKER}.")
            elif MODEL_OUTPUT_DELIMITER in pass1_normalized:
                print(f"[docs_sync] AI pass-1 for '{service}' returned final generation output directly.")
            else:
                warn(
                    f"AI pass-1 for '{service}' returned an unexpected format; "
                    "downstream parser will treat malformed output as no update."
                )
            llm_output = pass1_output
            response_source = "pass-1"
            end_group()

        start_group(f"docs_sync | {service} | apply updates")
        new_technical_readme, new_confluence_summary, parse_status = parse_generation_output(llm_output)
        if new_technical_readme:
            validate_technical_markdown_output(new_technical_readme)
        if new_confluence_summary:
            validate_confluence_storage_output(new_confluence_summary)
        if parse_status == "no_update":
            print(f"[docs_sync] AI {response_source} outcome for '{service}': no updates requested.")
        elif parse_status == "malformed":
            warn(f"AI {response_source} outcome for '{service}': malformed response treated as no update.")
        else:
            print(
                f"[docs_sync] AI {response_source} outcome for '{service}': "
                f"technical_update={'yes' if new_technical_readme else 'no'}, "
                f"confluence_update={'yes' if new_confluence_summary else 'no'}."
            )
        debug(
            f"Model output for service '{service}': "
            f"technical_update={'yes' if new_technical_readme else 'no'}, "
            f"confluence_update={'yes' if new_confluence_summary else 'no'}"
        )
        if new_confluence_summary:
            debug(f"Confluence summary preview for service '{service}': {preview(new_confluence_summary, 2000)}")

        if new_technical_readme:
            new_technical_core = strip_markdown_signature(new_technical_readme).strip()
            validate_no_meta_output(new_technical_core, "Technical-readme")
            if new_technical_core != technical_existing_core.strip():
                normalized_new_technical = append_markdown_signature(
                    new_technical_core,
                    repo_name,
                    update_ref,
                )
                technical_file.write_text(normalized_new_technical, encoding="utf-8")
                print(f"[docs_sync] Wrote technical readme file {technical_file}.")
            else:
                print(f"[docs_sync] No technical readme change detected for {technical_file}.")
        else:
            print(f"[docs_sync] Technical readme update not required for service '{service}'.")

        if new_confluence_summary:
            new_confluence_core = strip_confluence_signature(new_confluence_summary).strip()
            validate_no_meta_output(new_confluence_core, "Confluence")
            if new_confluence_core != confluence_existing_core.strip():
                signed_confluence_summary = append_confluence_signature(
                    new_confluence_core,
                    repo_name,
                    update_ref,
                )
                update_confluence_page(
                    confluence_base_url,
                    headers,
                    page_id,
                    confluence_title,
                    signed_confluence_summary,
                    confluence_version,
                )
            else:
                print(f"[docs_sync] No Confluence change detected for page {page_id}.")
        else:
            print(f"[docs_sync] Confluence update not required for service '{service}'.")
        end_group()

    print("[docs_sync] Documentation sync completed successfully.")


if __name__ == "__main__":
    main()
