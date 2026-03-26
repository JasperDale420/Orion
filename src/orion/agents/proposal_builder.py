import os
from datetime import UTC, datetime
from typing import Any

import yaml

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


class ProposalBuilder:
    """
    Persists agent proposals as auditable artifacts.
    PRD 13.4: All proposals must be auditable (YAML/Patch files).
    """

    def __init__(self, output_dir: str = "proposals"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _validate_proposal(self, proposal: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        PRD §13.4: proposals must be auditable and include evidence pointers + test plan.
        We fail-closed: invalid proposals are not saved.
        """
        missing: list[str] = []
        for key in ("type", "rationale", "evidence_pointers", "test_plan"):
            if key not in proposal:
                missing.append(key)
        if "evidence_pointers" in proposal and not isinstance(proposal.get("evidence_pointers"), dict):
            missing.append("evidence_pointers(dict)")
        if "test_plan" in proposal and not isinstance(proposal.get("test_plan"), list):
            missing.append("test_plan(list)")

        p_type = proposal.get("type")
        if p_type == "solver_edit":
            for key in ("target_solver_id", "ops"):
                if key not in proposal:
                    missing.append(key)
            if "ops" in proposal and not isinstance(proposal.get("ops"), list):
                missing.append("ops(list)")
        elif p_type in {"config_patch", "pr_patch"}:
            if "changes" not in proposal:
                missing.append("changes")
        elif p_type == "do_not_trade":
            if "recommendation" not in proposal:
                missing.append("recommendation")

        return (not missing), missing

    def save_proposal(
        self,
        proposal: dict[str, Any],
        date_str: str,
        run_id: str,
        *,
        input_snapshot_path: str | None = None,
        report_path: str | None = None,
    ) -> str | None:
        """
        Saves a single proposal to disk.
        Returns the file path.
        """
        try:
            ok, missing = self._validate_proposal(proposal)
            if not ok:
                logger.error(
                    "Invalid proposal; refusing to save",
                    event_type="EOD_PROPOSAL_INVALID",
                    missing=missing,
                    proposal_type=proposal.get("type"),
                )
                return None

            p_type = proposal.get("type", "unknown")
            target = proposal.get("target") or proposal.get("target_solver_id") or "general"

            # Construct filename: YYYY-MM-DD_type_target_hash.yaml
            timestamp = datetime.now(UTC).strftime("%H%M%S")
            filename = f"{date_str}_{timestamp}_{p_type}_{target}.yaml"
            # Sanitize filename
            filename = "".join(c for c in filename if c.isalnum() or c in ("_", "-", ".")).strip()

            file_path = os.path.join(self.output_dir, filename)

            # Add metadata
            artifact = {
                "meta": {
                    "run_id": run_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "agent": "EODReviewAgent",
                    "status": "PROPOSED",  # Needs human/gate approval
                    "input_snapshot_path": input_snapshot_path,
                    "report_path": report_path,
                },
                "proposal": proposal,
            }

            with open(file_path, "w") as f:
                yaml.safe_dump(artifact, f, sort_keys=False)

            logger.info(f"Saved proposal artifact: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to save proposal artifact: {e}")
            return None
