import json
import os
import sys
import time
import urllib.error
import urllib.request

import yaml


CONFIG_PATH = "docs-sync-config.yml"


def fail(message: str) -> None:
    print(f"[wait_required_checks] ERROR: {message}")
    sys.exit(1)


def github_get(url: str, token: str) -> dict:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="ignore")
        fail(f"GitHub API HTTP {error.code} for {url}. Response: {details}")
    except Exception as error:
        fail(f"GitHub API request failed for {url}: {error}")
    raise RuntimeError("Unreachable")


def main() -> None:
    print("[wait_required_checks] Starting required checks monitor.")
    token = os.getenv("GITHUB_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY")
    head_sha = os.getenv("PR_MERGE_SHA")
    current_workflow_name = os.getenv("GITHUB_WORKFLOW")
    current_job_name = os.getenv("GITHUB_JOB")
    if not token:
        fail("Missing GITHUB_TOKEN environment variable.")
    if not repository:
        fail("Missing GITHUB_REPOSITORY environment variable.")
    if not head_sha:
        fail("Missing PR_MERGE_SHA environment variable.")
    if not current_workflow_name:
        fail("Missing GITHUB_WORKFLOW environment variable.")
    if not current_job_name:
        fail("Missing GITHUB_JOB environment variable.")

    if not os.path.exists(CONFIG_PATH):
        fail(f"Missing required config file: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as file_handle:
        config = yaml.safe_load(file_handle)
    if not isinstance(config, dict):
        fail("docs-sync-config.yml must contain a top-level mapping/object.")

    wait_enabled = config.get("wait_for_required_checks")
    if not isinstance(wait_enabled, bool):
        fail("docs-sync-config.yml must define boolean wait_for_required_checks.")

    required_checks_mode = config.get("required_checks_mode")
    if required_checks_mode not in {"explicit", "auto"}:
        fail("docs-sync-config.yml must define required_checks_mode as 'explicit' or 'auto'.")

    required_checks = config.get("required_checks")
    if not isinstance(required_checks, list):
        fail("docs-sync-config.yml must define required_checks as a list.")
    if any((not isinstance(item, str) or not item.strip()) for item in required_checks):
        fail("Every required_checks item must be a non-empty string.")

    excluded_check_names = config.get("excluded_check_names")
    if not isinstance(excluded_check_names, list):
        fail("docs-sync-config.yml must define excluded_check_names as a list.")
    if any((not isinstance(item, str) or not item.strip()) for item in excluded_check_names):
        fail("Every excluded_check_names item must be a non-empty string.")

    timeout_seconds = config.get("check_wait_timeout_seconds")
    poll_seconds = config.get("check_poll_interval_seconds")
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        fail("check_wait_timeout_seconds must be a positive integer.")
    if not isinstance(poll_seconds, int) or poll_seconds <= 0:
        fail("check_poll_interval_seconds must be a positive integer.")

    if not wait_enabled:
        if required_checks_mode != "explicit":
            fail("required_checks_mode must be 'explicit' when wait_for_required_checks is false.")
        if required_checks:
            fail("required_checks must be empty when wait_for_required_checks is false.")
        if excluded_check_names:
            fail("excluded_check_names must be empty when wait_for_required_checks is false.")
        print("[wait_required_checks] Waiting is disabled by config. Proceeding.")
        return

    if required_checks_mode == "explicit" and not required_checks:
        fail("wait_for_required_checks is true and explicit mode requires non-empty required_checks.")

    checks_url = f"https://api.github.com/repos/{repository}/commits/{head_sha}/check-runs?per_page=100"
    start_time = time.time()
    print(f"[wait_required_checks] Monitoring checks for commit {head_sha}.")
    print(f"[wait_required_checks] Required checks mode: {required_checks_mode}")
    if required_checks_mode == "explicit":
        print(f"[wait_required_checks] Explicit required checks: {required_checks}")
    else:
        print(f"[wait_required_checks] Auto mode enabled. Excluded checks: {excluded_check_names}")

    while True:
        payload = github_get(checks_url, token)
        check_runs = payload.get("check_runs", [])
        state_by_name = {run.get("name"): run for run in check_runs if run.get("name")}
        workflow_self_prefix = f"{current_workflow_name} /"

        effective_required_checks = required_checks
        if required_checks_mode == "auto":
            candidate_checks = []
            for check_name in state_by_name.keys():
                if check_name == current_job_name:
                    continue
                if check_name == current_workflow_name:
                    continue
                if check_name == f"{current_workflow_name} / {current_job_name}":
                    continue
                if check_name.startswith(workflow_self_prefix):
                    continue
                if check_name in excluded_check_names:
                    continue
                candidate_checks.append(check_name)
            if not candidate_checks:
                elapsed = int(time.time() - start_time)
                if elapsed >= timeout_seconds:
                    fail(
                        f"Timed out after {timeout_seconds}s waiting for non-self checks to appear on commit {head_sha}."
                    )
                print(
                    "[wait_required_checks] No non-self checks discovered yet. "
                    f"Elapsed={elapsed}s. Sleeping {poll_seconds}s."
                )
                time.sleep(poll_seconds)
                continue
            effective_required_checks = candidate_checks
            print(f"[wait_required_checks] Auto-discovered checks: {effective_required_checks}")

        missing = [name for name in effective_required_checks if name not in state_by_name]
        if missing:
            fail(f"Required checks not found on commit {head_sha}: {missing}")

        pending = []
        failed = []
        for check_name in effective_required_checks:
            check = state_by_name[check_name]
            status = check.get("status")
            conclusion = check.get("conclusion")
            if status != "completed":
                pending.append(check_name)
                continue
            if conclusion != "success":
                failed.append(f"{check_name} (conclusion={conclusion})")

        if failed:
            fail(f"Required checks failed: {failed}")
        if not pending:
            print("[wait_required_checks] All required checks completed successfully.")
            return

        elapsed = int(time.time() - start_time)
        if elapsed >= timeout_seconds:
            fail(f"Timed out after {timeout_seconds}s waiting for checks: {pending}")

        print(f"[wait_required_checks] Pending checks: {pending}. Elapsed={elapsed}s. Sleeping {poll_seconds}s.")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
