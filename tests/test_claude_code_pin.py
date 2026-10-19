import pytest

import docs_sync as ds


@pytest.mark.parametrize(
    "command",
    [
        "npx -y @anthropic-ai/claude-code@2.1.290",
        "npx -y @anthropic-ai/claude-code@2.1.290 --verbose",
        "claude",
        "/usr/local/bin/claude",
    ],
)
def test_pinned_or_preinstalled_commands_pass(command):
    """An exact release, or a binary already on the runner, is accepted."""
    ds.require_pinned_claude_code(command)


@pytest.mark.parametrize(
    "command",
    [
        "npx -y @anthropic-ai/claude-code",
        "npx -y @anthropic-ai/claude-code@latest",
        "npx -y @anthropic-ai/claude-code@2",
        "npx -y @anthropic-ai/claude-code@^2.1.0",
        "npx -y @anthropic-ai/claude-code@2.1",
    ],
)
def test_floating_versions_fail(command):
    """A command that would install the newest release stops the run."""
    with pytest.raises(SystemExit):
        ds.require_pinned_claude_code(command)
