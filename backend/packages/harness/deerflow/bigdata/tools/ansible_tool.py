"""Ansible Playbook execution tool for Big Data Ops Agent.

Enables the Ops Agent to generate and run Ansible playbooks for idempotent,
multi-node big data component deployments. This is the preferred deployment
mechanism over raw shell scripts because Ansible provides:
- Idempotency (safe to re-run)
- Declarative configuration
- Multi-host orchestration
- Built-in error handling
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState
from deerflow.bigdata.config.bigdata_config import get_bigdata_config

logger = logging.getLogger(__name__)

# Where generated playbooks are stored so users can review them
_PLAYBOOK_OUTPUT_DIR = "/mnt/user-data/workspace/ansible"


def _ensure_playbook_dir() -> Path:
    """Create the playbook output directory if it doesn't exist."""
    path = Path(_PLAYBOOK_OUTPUT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _generate_inventory(hosts: list[dict], host_groups: dict[str, list[str]] | None = None) -> str:
    """Generate an Ansible INI-format inventory from host configs and optional dynamic groups.

    Args:
        hosts: List of host config dicts.
        host_groups: Optional mapping of group names to lists of host names.

    Returns:
        INI-format inventory string.
    """
    lines: list[str] = ["[bigdata]"]
    for h in hosts:
        parts = [h["host"]]
        parts.append(f"ansible_user={h.get('user', 'root')}")
        parts.append(f"ansible_port={h.get('port', 22)}")
        if h.get("key_file"):
            parts.append(f"ansible_ssh_private_key_file={h['key_file']}")
        elif h.get("password"):
            parts.append(f"ansible_password={h['password']}")
        parts.append("ansible_ssh_common_args='-o StrictHostKeyChecking=no'")
        line = " ".join(parts)
        lines.append(f"{h['name']} {line}")

    # Combine static roles with dynamic groups
    combined_groups: dict[str, set[str]] = {}

    # Static roles from config
    for h in hosts:
        for role in h.get("roles", []):
            combined_groups.setdefault(role, set()).add(h["name"])

    # Dynamic groups from the tool call
    if host_groups:
        for group, members in host_groups.items():
            for m in members:
                combined_groups.setdefault(group, set()).add(m)

    for group, members in sorted(combined_groups.items()):
        lines.append(f"\n[{group}]")
        for m in sorted(list(members)):
            lines.append(m)

    return "\n".join(lines)


@tool("ansible_run", parse_docstring=True)
def ansible_run_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    playbook_content: str,
    target_hosts: str = "all",
    host_groups: dict[str, list[str]] | None = None,
    extra_vars: str | None = None,
) -> str:
    """Execute an Ansible playbook against registered big data hosts.

    The playbook YAML content is provided directly. The tool will:
    1. Write the playbook to a file in the workspace.
    2. Generate an inventory from the registered hosts in config.yaml and optional dynamic groups.
    3. Run `ansible-playbook` and return the output.

    **Dynamic Allocation**: Use the `host_groups` parameter to assign specific hosts
    to roles for this execution (e.g., {"namenode": ["host-1"], "datanode": ["host-2"]}).

    Args:
        description: Explain what this playbook does. ALWAYS PROVIDE THIS PARAMETER FIRST.
        playbook_content: The complete Ansible playbook YAML content.
        target_hosts: Comma-separated list of host names or 'all'. Default is 'all'.
        host_groups: Optional mapping of group names to host names for dynamic inventory creation.
        extra_vars: Optional extra variables in 'key=value key2=value2' format.
    """
    config = get_bigdata_config()

    if not config.hosts:
        return textwrap.dedent("""\
            Error: No hosts are configured in the big data inventory.

            Add hosts to the `bigdata.hosts` section in config.yaml:

            bigdata:
              hosts:
                - name: my-server
                  host: 192.168.1.10
                  user: root
                  password: "my-password"
        """)

    # --- Check ansible is available ---
    try:
        version_proc = subprocess.run(
            ["ansible-playbook", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_proc.returncode != 0:
            return "Error: `ansible-playbook` is installed but returned an error. Check your Ansible installation."
    except FileNotFoundError:
        return textwrap.dedent("""\
            Error: `ansible-playbook` command not found.

            Install Ansible first:
              pip install ansible
            or:
              apt-get install ansible
        """)
    except Exception as e:
        return f"Error: Failed to check Ansible availability: {e}"

    # --- Filter target hosts ---
    if target_hosts == "all":
        selected_hosts = config.hosts
    else:
        target_names = {t.strip() for t in target_hosts.split(",")}
        selected_hosts = [h for h in config.hosts if h.name in target_names or h.host in target_names]
        if not selected_hosts:
            allowed = ", ".join(config.host_names)
            return f"Error: None of the specified hosts ({target_hosts}) are in the inventory. Available: {allowed}"

    # --- Write playbook and inventory ---
    try:
        playbook_dir = _ensure_playbook_dir()
        playbook_path = playbook_dir / "playbook.yml"
        inventory_path = playbook_dir / "inventory.ini"

        playbook_path.write_text(playbook_content, encoding="utf-8")

        host_dicts = [h.model_dump() for h in selected_hosts]
        inventory_content = _generate_inventory(host_dicts, host_groups=host_groups)
        inventory_path.write_text(inventory_content, encoding="utf-8")

        logger.info("Ansible: playbook=%s, inventory=%s, targets=%s", playbook_path, inventory_path, target_hosts)

    except Exception as e:
        return f"Error: Failed to write playbook files: {e}"

    # --- Run ansible-playbook ---
    cmd = [
        "ansible-playbook",
        "-i", str(inventory_path),
        str(playbook_path),
    ]
    if extra_vars:
        cmd.extend(["--extra-vars", extra_vars])

    # Add verbosity for better debugging
    cmd.append("-v")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.command_timeout,
            env={**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False"},
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        # Truncate excessive output
        max_len = 6000
        if len(stdout) > max_len:
            stdout = stdout[:max_len] + "\n... (output truncated)"

        parts: list[str] = []
        parts.append(f"**Playbook**: {playbook_path}")
        parts.append(f"**Targets**: {target_hosts}")
        parts.append(f"**Exit Code**: {proc.returncode}")

        if stdout:
            parts.append(f"**Output**:\n```\n{stdout}\n```")
        if stderr and proc.returncode != 0:
            if len(stderr) > 2000:
                stderr = stderr[:2000] + "\n... (truncated)"
            parts.append(f"**Errors**:\n```\n{stderr}\n```")

        if proc.returncode == 0:
            parts.append("**Status**: ✅ Playbook executed successfully")
        else:
            parts.append("**Status**: ❌ Playbook failed — review the output above for details")

        return "\n\n".join(parts)

    except subprocess.TimeoutExpired:
        return f"Error: Ansible playbook timed out after {config.command_timeout}s"
    except Exception as e:
        return f"Error: Ansible execution failed: {e}"
