"""One-shot SP023 scope-register assembly.

Reads the pinned inputs (reviewed contracts, bindings, source issues, the
schema-2 reviewed_metadata_only source ledger, the SP009 source queues, the
related-materials input, the overseas review candidates, the acceptance file
and the two frozen plan documents), verifies every pin byte-for-byte before
parsing, and writes the initial ``docs/capabilities/scope-register.json``.

This is a single initialization script, not a maintenance framework: no SDK
imports, no harness reuse, no network, no environment reads, no test runner.
"""

import argparse
import errno
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

BASELINE_COMMIT = "641d6cc10010b11a49c65c9d6783f92eefda00d3"
SCOPE_REVISION = "2026-11-17-r1"

# ---------------------------------------------------------------------------
# Frozen layout facts (audited 2026-11-17; the script asserts they hold).
# ---------------------------------------------------------------------------
EXPECTED_SOURCES = 4051
EXPECTED_OBSERVATIONS = 3019
EXPECTED_CONTRACTS = 203
EXPECTED_CANDIDATES = 49
EXPECTED_NEW_SLOTS = 45
EXPECTED_RELATIONS = 4
DOMESTIC_PORTAL_TOTAL = 2229
OVERSEAS_PORTAL_TOTAL = 52
STATIC_REF_TOTAL = 293
ASSET_TOTAL = 1477
RELATED_MATERIAL_QUEUE = "related_material_review"
VERSION_REVIEW_QUEUE = "related_material_version_review"

# Real SP009 queue ids (the input carries no sp009_ prefix) and their frozen
# unique-source counts; the same ids drive source owners and queues_out.
EXPECTED_QUEUE_COUNTS = {
    "batch_1_A": 326,
    "batch_1_B": 165,
    "batch_1_C": 283,
    "batch_2_A": 315,
    "batch_2_B": 385,
    "batch_2_C": 294,
    "batch_3_A": 340,
    "batch_3_B": 121,
}
# 1273 is a document identity (domestic:1273), never a queue size: its unique
# owner is batch_1_A and both of its recorded memberships are retained.
SOURCE_1273_IDENTITY = "domestic:1273"
SOURCE_1273_OWNER = "batch_1_A"
SOURCE_1273_MEMBERSHIP_COUNT = 2

EXPECTED_DECLARATION_REFS = 47
EXPECTED_PACKAGE_NAMES = frozenset(
    {
        "ezuikit-js",
        "@ezviz/openplatform-sdk",
        "@ezuikit/multi-screen",
        "ezuikit-flv",
        "@ezuikit/player-hls",
        "@ezuikit/utils-logger",
        "ertc-web",
    }
)
EXPECTED_PACKAGE_DECLARATIONS = 11
EXPECTED_DISCREPANCIES = 3
EXPECTED_ACCEPTANCE_CASES = 12
AUTHORIZED_OUTPUT_REL = "docs/capabilities/scope-register.json"

# The single legacy text selector with an explicit reviewed mapping: the case
# keeps its original literal selector and region_cluster strings; the fixed
# mapping resolves to binding public_entry "settings.list_timezones", binding
# region "overseas", and registry operation_id global.settings.list_timezones.
TIMEZONE_CASE = "ACC-OS-PUBLIC-TIMEZONE-001"
TIMEZONE_SELECTOR_LITERAL = "settings.list_timezones (unauthenticated public catalog)"
TIMEZONE_REGION_CLUSTER_LITERAL = "overseas: singapore (isgpopen.ezvizlife.com)"
TIMEZONE_PUBLIC_ENTRY = "settings.list_timezones"
TIMEZONE_OPERATION = "global.settings.list_timezones"
TIMEZONE_REGION = "overseas"

# Repository input paths, keyed by the logical input name used in `inputs`
# and by the pins file.
INPUT_PATHS = {
    "reviewed_contracts": "contracts/reviewed-transport-contracts.json",
    "sdk_bindings": "contracts/sdk-bindings.json",
    "source_issues": "contracts/source-issues.json",
    "acceptance": "docs/validation/sdk-product-acceptance.json",
    "overseas_review_candidates": "docs/capabilities/overseas-review-candidates.json",
    "plan_scope_register": "docs/plans/2026-11-17-capability-scope-register.md",
    "plan_public_area": "docs/plans/2026-11-17-public-area-directory.md",
}

PHASE_2_AUTHORIZED = (
    "app_adapter",
    "selective_telemetry",
    "independent_model_evaluation",
    "controlled_rsi",
)

# Fixed queue order over the real input ids: execution order, not a scope
# reduction. The same ids are used for source owners and queues_out.
QUEUE_ORDER = (
    "batch_1_A",
    "batch_1_B",
    "batch_1_C",
    "batch_2_A",
    "batch_2_B",
    "batch_2_C",
    "batch_3_A",
    "batch_3_B",
    "overseas_portal",
    RELATED_MATERIAL_QUEUE,
    VERSION_REVIEW_QUEUE,
)

# The four candidate records that map onto existing contracts instead of new
# capability slots: candidate_id -> (contract operation_id, relation kind).
EXPECTED_SOURCE_RELATIONS = {
    "candidate:overseas:469:1.2": ("global.token.get", "additional_source"),
    "candidate:overseas:469:1.10": ("global.devices.bind", "additional_source"),
    "candidate:overseas:534:1.2": ("global.playback.invalidate", "source_alignment"),
    "candidate:overseas:2868:d0": (
        "global.security.update_password",
        "source_alignment",
    ),
}

PUBLIC_AREAS_CAPABILITY = "candidate:overseas:469:1.12"
PUBLIC_AREAS_COHORT = "PUBLIC-AREAS-001"
PUBLIC_AREAS_CASES = (
    "ACC-OS-PUBLIC-AREAS-OFFLINE-001",
    "ACC-OS-PUBLIC-AREAS-SDK-001",
)


class AssemblyError(ValueError):
    """Fixed validation failure; messages never echo raw input values."""


def _err(reason):
    return AssemblyError(f"assemble_scope_register: {reason}")


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Input loading with pin verification.
# ---------------------------------------------------------------------------

def _read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        raise _err("input cannot be read") from None


def _load_pins(pins_arg):
    """Read the pins JSON: {input_name: {"path": ..., "sha256": ...}}."""
    raw = _read_bytes(pins_arg)
    try:
        pins = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _err("pins file is not valid JSON") from None
    if not isinstance(pins, dict):
        raise _err("pins file must be a JSON object")
    required = set(INPUT_PATHS) | {"ledger", "queues", "materials"}
    for name in required:
        if name not in pins:
            raise _err(f"missing pin for input {name}")
    for name, entry in pins.items():
        if not isinstance(entry, dict):
            raise _err("pins entry must be an object")
        path = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(path, str) or not path:
            raise _err("pins entry path missing")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise _err("pins entry sha256 malformed")
    return pins


def _resolve_input(repo, pins, name, required=True):
    """Return (repo-relative path, raw bytes) for a pinned input.

    Only the pinned path is read; the raw bytes must match the pinned digest
    before any parsing happens.
    """
    entry = pins.get(name)
    if entry is None:
        if required:
            raise _err(f"missing pin for input {name}")
        return None, None
    rel = entry["path"]
    if os.path.isabs(rel) or ".." in rel.split("/"):
        raise _err("pins path must be a safe repository-relative path")
    raw = _read_bytes(Path(repo) / rel)
    if _sha256(raw) != entry["sha256"]:
        raise _err(f"input pin mismatch for {name}")
    return rel, raw


def _parse_json(raw, reason):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _err(reason) from None
    return data


# ---------------------------------------------------------------------------
# Small assembly helpers.
# ---------------------------------------------------------------------------

def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _queue_id(queue):
    """Return the real SP009 queue id; the input carries no added prefix."""
    value = queue.get("id") if isinstance(queue, dict) else None
    if not isinstance(value, str) or not value:
        raise _err("queue record without an id")
    return value


def _source_queue_map(queues_data):
    """Map each domestic source_identity to its unique owning queue id.

    Each identity must occur exactly once across the eight queue source
    lists; a repeated identity is an input defect and raises rather than
    being merged into a multi-owner mapping. The membership view keeps the
    source item's own memberships list (domestic:1273 carries two directory
    records under its single owner batch_1_A).
    """
    mapping = {}
    counts = {}
    memberships = {}
    for queue in queues_data["queues"]:
        qid = _queue_id(queue)
        if not isinstance(queue, dict):
            raise _err("queue record must be an object")
        sources = queue.get("sources")
        if not isinstance(sources, list):
            raise _err("queue record without a sources list")
        counts[qid] = len(sources)
        for item in sources:
            identity = item.get("source_identity") if isinstance(item, dict) else item
            if not isinstance(identity, str) or not identity:
                raise _err("queue source entry without source_identity")
            if identity in mapping:
                raise _err("queue source identity occurs in more than one queue")
            member_list = item.get("memberships") if isinstance(item, dict) else None
            if not isinstance(member_list, list):
                raise _err("queue source entry without a memberships list")
            mapping[identity] = qid
            memberships[identity] = member_list
    if set(counts) != set(EXPECTED_QUEUE_COUNTS):
        raise _err("queues input must carry the eight fixed batch queue ids")
    for qid, expected in EXPECTED_QUEUE_COUNTS.items():
        if counts[qid] != expected:
            raise _err(f"queue {qid} source count differs from the frozen count")
        declared = next(
            q.get("source_count")
            for q in queues_data["queues"]
            if _queue_id(q) == qid
        )
        if declared != expected:
            raise _err(f"queue {qid} declared count differs from the frozen count")
    if len(mapping) != DOMESTIC_PORTAL_TOTAL:
        raise _err("domestic queue source set must contain exactly 2229 identities")
    # domestic:1273 is a document identity: unique owner batch_1_A, and both
    # recorded memberships stay visible rather than being deduplicated away.
    if mapping.get(SOURCE_1273_IDENTITY) != SOURCE_1273_OWNER:
        raise _err("domestic:1273 must be uniquely owned by batch_1_A")
    if len(memberships.get(SOURCE_1273_IDENTITY) or ()) != SOURCE_1273_MEMBERSHIP_COUNT:
        raise _err("domestic:1273 must retain exactly two queue memberships")
    return mapping, counts, memberships


def _contract_capability(contract, binding, source_ref, obs_refs, case_refs):
    """Build one reviewed-contract capability record."""
    operation_id = contract["operation_id"]
    unknowns = []
    if binding is None:
        implementation = "unknown"
        sdk_entry = None
        unknowns.append("binding_record_missing")
    else:
        implementation = "implemented"
        sdk_entry = {
            "public_entry": binding.get("public_entry"),
            "module": binding.get("module"),
            "symbols": list(binding.get("symbols") or []),
        }
    if contract.get("body_encoding") in (None, "unknown"):
        unknowns.append("body_encoding")
    if not isinstance(contract.get("read_only"), bool):
        unknowns.append("read_only")
    unknowns.extend(
        [
            "account_permission_scope",
            "device_prerequisites",
            "layer_applicability",
        ]
    )
    # An existing binding only means a binding record exists; its status
    # fields are recorded input evidence. This initialization runs no AST,
    # offline or live checks, and produces no new passed results.
    evidence = None
    if binding is not None:
        evidence = {
            "binding_record_ref": {
                "input": "sdk_bindings",
                "entity_id": operation_id,
            },
            "recorded_offline_status": binding.get("offline_status"),
            "recorded_live_status": binding.get("live_status"),
            "recorded_live_evidence": binding.get("live_evidence"),
            "evidence_kind": "recorded_binding_input",
            "executed_this_round": False,
        }
    return {
        "capability_id": f"contract:{operation_id}",
        "capability_kind": "reviewed_contract",
        "origin_record_ref": {
            "input": "reviewed_contracts",
            "entity_id": operation_id,
        },
        "scenario": None,
        "family": None,
        "nature": "server_api",
        "requirement": "required",
        "region_scope": contract.get("region"),
        "origin_policy_ref": contract.get("base_url"),
        "account_permission_scope": None,
        "device_prerequisites": None,
        "protocol_ref": {
            "input": "reviewed_contracts",
            "entity_id": operation_id,
        },
        "sdk_choice_refs": [],
        "sdk_entry": sdk_entry,
        "implementation": {
            "status": implementation,
            "evidence": evidence,
            "binding_record_present": binding is not None,
            "ast_checked_this_round": False,
            "offline_checked_this_round": False,
            "live_checked_this_round": False,
            "new_passed_evidence_this_round": False,
        },
        "review": "needs_review",
        "disposition": "implement",
        "source_refs": [source_ref] if source_ref is not None else [],
        "observation_refs": obs_refs,
        "acceptance_case_refs": case_refs,
        "unknown_fields": sorted(set(unknowns)),
        "gaps": [],
        "completion_criteria": "layer acceptance case refs met with applicable evidence; source and contract semantics dispositioned",
        "owner_queue": None,  # filled by caller
        "cohort_id": None,
        "depends_on": [],
    }


def _candidate_capability(record, owner_queue):
    """Build one new-candidate capability slot, preserving the lineage ID."""
    facts = record.get("official_facts") or {}
    proposal = record.get("sdk_design_proposal") or {}
    support = record.get("existing_support") or {}
    unknowns = ["layer_applicability"]
    request = record.get("official_facts", {}).get("request_field_facts") or {}
    if request.get("request_schema_status") not in (None, "complete"):
        unknowns.append("request_schema")
    if record.get("source_uncertainties"):
        unknowns.append("source_uncertainties")
    implementation_status = record.get("implementation_status")
    if implementation_status not in (
        "implemented",
        "missing",
        "partial",
        "integration_required",
        "not_applicable",
        "unknown",
    ):
        implementation_status = "unknown"
    return {
        "capability_id": record["capability_id"],
        "capability_kind": "overseas_review_candidate",
        "origin_record_ref": {
            "input": "overseas_review_candidates",
            "entity_id": record["capability_id"],
        },
        "scenario": None,
        "family": None,
        "nature": record.get("nature"),
        "requirement": "pending_scope_review",
        "region_scope": facts.get("region", "overseas"),
        "origin_policy_ref": facts.get("origin"),
        "account_permission_scope": None,
        "device_prerequisites": None,
        "protocol_ref": {
            "input": "overseas_review_candidates",
            "entity_id": record["capability_id"],
        },
        "sdk_choice_refs": [],
        "sdk_entry": {
            "public_entry": proposal.get("entry"),
            "status": proposal.get("status", "proposal"),
            "facade": proposal.get("facade"),
        },
        "implementation": {
            "status": implementation_status,
            "evidence": {
                "recorded_offline_status": record.get("offline_status"),
                "recorded_live_read_status": record.get("live_read_status"),
                "recorded_live_write_status": record.get("live_write_status"),
                "evidence_kind": "candidate_review_status_not_acceptance",
                "executed_this_round": False,
            },
            "binding_record_present": False,
            "ast_checked_this_round": False,
            "offline_checked_this_round": False,
            "live_checked_this_round": False,
            "new_passed_evidence_this_round": False,
        },
        # A candidate stays a proposal until semantic review ratifies it;
        # the reviewed-implement disposition is preserved as the proposal,
        # never as an implemented capability.
        "review": "candidate",
        "disposition": record.get("disposition")
        if record.get("disposition") in ("implement", "needs_review")
        else "needs_review",
        "source_refs": [
            {
                "source_id": ref["source_id"],
                "content_sha256": ref.get("content_sha256"),
                "section": ref.get("section"),
                "declaration": ref.get("declaration"),
                "element_pointers": list(ref.get("element_pointers") or []),
            }
            for ref in record.get("source_refs") or []
            if isinstance(ref, dict) and isinstance(ref.get("source_id"), str)
        ],
        "observation_refs": [],
        "acceptance_case_refs": [],
        "unknown_fields": sorted(set(unknowns)),
        "gaps": list(record.get("blocking_resources") or []),
        "existing_support_status": support.get("status"),
        "completion_criteria": "candidate review dispositioned; per-layer acceptance case refs frozen and met",
        "owner_queue": owner_queue,
        "cohort_id": None,
        "depends_on": [],
    }


# ---------------------------------------------------------------------------
# Main assembly.
# ---------------------------------------------------------------------------

def _rel_arg(repo, path):
    """Normalize a CLI path to a repo-relative string for pin comparison."""
    repo_path = Path(repo).resolve()
    given = Path(path)
    if not given.is_absolute():
        return given.as_posix()
    try:
        return given.resolve().relative_to(repo_path).as_posix()
    except (OSError, RuntimeError, ValueError):
        raise _err("input path must resolve inside the repository") from None


def assemble(repo, ledger_path, queues_path, materials_path, pins):
    # --- ledger (schema-2 reviewed_metadata_only only) --------------------
    rel, raw = _resolve_input(repo, pins, "ledger")
    if rel != _rel_arg(repo, ledger_path):
        raise _err("--ledger path must equal the pinned ledger input path")
    ledger = _parse_json(raw, "ledger is not valid JSON")
    if ledger.get("schema_version") != 2:
        raise _err("source ledger must be schema_version 2")
    if ledger.get("publication_profile") != "reviewed_metadata_only":
        raise _err("source ledger must be reviewed_metadata_only")
    sources = ledger.get("sources")
    observations = ledger.get("observations")
    if not isinstance(sources, list) or not isinstance(observations, list):
        raise _err("ledger missing sources/observations")
    if len(sources) != EXPECTED_SOURCES or len(observations) != EXPECTED_OBSERVATIONS:
        raise _err("ledger source/observation count mismatch")
    source_by_id = {}
    for source in sources:
        sid = source.get("source_id")
        if not isinstance(sid, str) or sid in source_by_id:
            raise _err("ledger source identity invalid or duplicated")
        source_by_id[sid] = source
    obs_by_id = {}
    for obs in observations:
        oid = obs.get("observation_id")
        if not isinstance(oid, str) or oid in obs_by_id:
            raise _err("ledger observation identity invalid or duplicated")
        if obs.get("source_id") not in source_by_id:
            raise _err("ledger observation has an unknown source")
        obs_by_id[oid] = obs

    # --- queues ------------------------------------------------------------
    rel_queues, qraw = _resolve_input(repo, pins, "queues")
    if rel_queues != _rel_arg(repo, queues_path):
        raise _err("--queues path must equal the pinned queues input path")
    queues_data = _parse_json(qraw, "queues input is not valid JSON")
    queue_list = queues_data.get("queues")
    if not isinstance(queue_list, list) or len(queue_list) != 8:
        raise _err("queues input must contain exactly 8 queues")
    source_owner, _queue_counts, _queue_memberships = _source_queue_map(queues_data)

    # --- materials -----------------------------------------------------------
    rel_materials, mraw = _resolve_input(repo, pins, "materials")
    if rel_materials != _rel_arg(repo, materials_path):
        raise _err("--materials path must equal the pinned materials input path")
    materials = _parse_json(mraw, "materials input is not valid JSON")
    repo_obs = materials.get("repository_observations") or []
    doc_refs = materials.get("additional_official_document_package_references") or []
    download_links = materials.get("download_or_sdk_link_candidates") or []
    proposals = materials.get("remaining_source_set_proposals") or []
    if len(repo_obs) != 11 or len(download_links) != 34 or len(proposals) != 4 or len(doc_refs) != 5:
        raise _err("materials input counts do not match the frozen layout")

    # --- repository inputs -----------------------------------------------------
    rel_contracts, craw = _resolve_input(repo, pins, "reviewed_contracts")
    contracts_data = _parse_json(craw, "reviewed contracts input is not valid JSON")
    contracts = contracts_data.get("contracts")
    if not isinstance(contracts, list) or len(contracts) != EXPECTED_CONTRACTS:
        raise _err("reviewed contracts must contain exactly 203 entries")

    rel_bindings, braw = _resolve_input(repo, pins, "sdk_bindings")
    bindings_data = _parse_json(braw, "sdk bindings input is not valid JSON")
    bindings = {b["operation_id"]: b for b in bindings_data.get("bindings") or []}

    rel_issues, iraw = _resolve_input(repo, pins, "source_issues")
    issues_data = _parse_json(iraw, "source issues input is not valid JSON")

    rel_accept, araw = _resolve_input(repo, pins, "acceptance")
    acceptance = _parse_json(araw, "acceptance input is not valid JSON")

    rel_cand, cand_raw = _resolve_input(repo, pins, "overseas_review_candidates")
    candidates_data = _parse_json(cand_raw, "overseas candidates input is not valid JSON")
    candidates = candidates_data.get("capabilities")
    if not isinstance(candidates, list) or len(candidates) != EXPECTED_CANDIDATES:
        raise _err("overseas candidates must contain exactly 49 entries")

    rel_plan_scope, _ = _resolve_input(repo, pins, "plan_scope_register")
    rel_plan_area, _ = _resolve_input(repo, pins, "plan_public_area")

    # --- case refs from acceptance ------------------------------------------
    # Structured capability_ids / operation_ids are authoritative. For the
    # fixed legacy text selectors only the single explicit reviewed mapping is
    # applied (timezone: text public_entry -> registry operation_id); free
    # prose and wildcard selectors never expand into bindings.
    acceptance_cases = acceptance.get("cases")
    if not isinstance(acceptance_cases, list) or len(acceptance_cases) != EXPECTED_ACCEPTANCE_CASES:
        raise _err("acceptance input must contain exactly 12 cases")
    acceptance_ids = set()
    for case in acceptance_cases:
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or case_id in acceptance_ids:
            raise _err("acceptance case id invalid or duplicated")
        acceptance_ids.add(case_id)
    for required_case in PUBLIC_AREAS_CASES + (TIMEZONE_CASE,):
        if required_case not in acceptance_ids:
            raise _err("a frozen acceptance case id is absent from the input")
    case_refs_by_op = {}
    case_refs_by_cap = {}
    for case in acceptance_cases:
        case_id = case["case_id"]
        for op in case.get("operation_ids") or []:
            if isinstance(op, str) and op:
                case_refs_by_op.setdefault(op, set()).add(case_id)
        for cap_id in case.get("capability_ids") or []:
            if isinstance(cap_id, str) and cap_id:
                case_refs_by_cap.setdefault(cap_id, set()).add(case_id)
        if case_id == TIMEZONE_CASE:
            # One fixed reviewed mapping: the case keeps its original literal
            # selector/region_cluster strings, and the mapping is verified on
            # the binding record fields (public_entry, region) plus the fixed
            # registry operation_id. No text parsing; nothing else expands.
            if case.get("capability_selector") != TIMEZONE_SELECTOR_LITERAL:
                raise _err("timezone case must keep its original selector literal")
            if case.get("region_cluster") != TIMEZONE_REGION_CLUSTER_LITERAL:
                raise _err("timezone case must keep its original region_cluster literal")
            binding = bindings.get(TIMEZONE_OPERATION)
            if binding is None:
                raise _err("timezone case binding record is absent")
            if binding.get("operation_id") != TIMEZONE_OPERATION:
                raise _err("timezone binding operation_id must be global.settings.list_timezones")
            if binding.get("public_entry") != TIMEZONE_PUBLIC_ENTRY:
                raise _err("timezone binding public_entry must be settings.list_timezones")
            if binding.get("region") != TIMEZONE_REGION:
                raise _err("timezone binding region must be overseas")
            case_refs_by_op.setdefault(TIMEZONE_OPERATION, set()).add(case_id)

    # Index registered source issues by (region, source_doc_id) so unresolved
    # semantics follow the affected capability instead of being dropped.
    issues_by_doc = {}
    for issue in issues_data.get("issues") or []:
        key = (issue.get("region"), issue.get("source_doc_id"))
        issues_by_doc.setdefault(key, []).append(issue.get("id"))

    # --- source_reviews --------------------------------------------------------
    source_reviews = {}
    for sid in sorted(source_by_id):
        source = source_by_id[sid]
        # The ledger's reviewed disposition is authoritative: blocked_source
        # means the body was never usable; it stays a required review item.
        blocked = source.get("disposition") == "blocked_source"
        if source.get("kind") == "portal_document" and source.get("region") == "domestic":
            owner = source_owner.get(sid.split(":", 1)[1])
            if owner is None:
                raise _err("domestic portal source missing from the SP009 identity set")
        elif source.get("kind") == "portal_document" and source.get("region") == "overseas":
            owner = "overseas_portal"
        elif source.get("kind") in ("sdk_reference", "asset"):
            owner = RELATED_MATERIAL_QUEUE
        else:
            raise _err("ledger source has an unrecognized kind/region")
        source_reviews[sid] = {
            "source_ref": sid,
            "content_sha256": source.get("content_sha256"),
            "owner_queue": owner,
            "requirement": "required_review",
            "review_state": "blocked_source" if blocked else "pending",
            "resolution_refs": [],
            "review_record_ref": None,
            "unknowns": list(source.get("unresolved") or []),
        }
        if blocked:
            source_reviews[sid]["blockers"] = ["source_body_unavailable_or_unusable"]

    # domestic portal sources must all carry a queue owner from the SP009 set
    for sid, review in source_reviews.items():
        source = source_by_id[sid]
        if source.get("kind") == "portal_document" and source.get("region") == "domestic":
            if review["owner_queue"] not in QUEUE_ORDER[:8]:
                raise _err("domestic portal source missing a queue owner")
        if source.get("kind") == "portal_document":
            identity = sid.split(":", 1)[1]
            if source.get("region") == "domestic" and identity not in source_owner:
                raise _err("domestic portal identity absent from queues")

    # --- observation_reviews ---------------------------------------------------
    observation_reviews = {}
    for oid in sorted(obs_by_id):
        obs = obs_by_id[oid]
        source = source_by_id[obs["source_id"]]
        owner = source_reviews[obs["source_id"]]["owner_queue"]
        cap_refs = [f"contract:{op}" for op in obs.get("exact_operation_ids") or []]
        state = "pending"
        if source_reviews[obs["source_id"]]["review_state"] == "blocked_source":
            state = "pending"  # still a required review item; inherited block is not a completion
        observation_reviews[oid] = {
            "observation_ref": oid,
            "source_ref": obs["source_id"],
            "owner_queue": owner,
            "kind": obs.get("kind"),
            "review_state": state,
            "capability_refs": cap_refs,
            "material_review_refs": [],
            "resolution_refs": [],
            "review_record_ref": None,
            "unknowns": list(obs.get("reason_codes") or []),
        }

    # Index exact-matched observations by (source_id, operation_id) once.
    exact_obs = {}
    for obs in observations:
        for op in obs.get("exact_operation_ids") or []:
            exact_obs.setdefault((obs["source_id"], op), []).append(obs["observation_id"])

    # --- capabilities --------------------------------------------------------
    capabilities = {}
    for contract in contracts:
        op = contract["operation_id"]
        binding = bindings.get(op)
        sid = f"portal-2026-11-17:{contract['region']}:{contract['source_doc_id']}"
        if sid not in source_by_id:
            sid = None
        obs_refs = sorted(exact_obs.get((sid, op), ())) if sid is not None else []
        queue_owner = None
        if sid is not None:
            queue_owner = source_reviews[sid]["owner_queue"]
        cap = _contract_capability(
            contract,
            binding,
            {
                "source_id": sid,
                "content_sha256": contract.get("source_content_sha256"),
            }
            if sid is not None
            else None,
            sorted(obs_refs),
            sorted(case_refs_by_op.get(op, ())),
        )
        cap["owner_queue"] = queue_owner or "overseas_portal"
        issue_ids = sorted(issues_by_doc.get((contract["region"], contract["source_doc_id"]), ()))
        if issue_ids:
            cap["source_issue_refs"] = issue_ids
            if "source_semantics_unresolved" not in cap["unknown_fields"]:
                cap["unknown_fields"] = sorted(set(cap["unknown_fields"]) | {"source_semantics_unresolved"})
        capabilities[cap["capability_id"]] = cap

    # --- candidates -> new capability slots + source relations ---------------
    relation_records = {}
    for record in candidates:
        cid = record["capability_id"]
        expected = EXPECTED_SOURCE_RELATIONS.get(cid)
        if expected is not None:
            contract_op, kind = expected
            support = record.get("existing_support") or {}
            if support.get("contract") != contract_op:
                raise _err(f"candidate {cid} does not map to its frozen contract")
            relation_records[cid] = record
            continue
        owner = "overseas_portal"
        cap = _candidate_capability(record, owner)
        if cid in capabilities:
            raise _err("candidate capability id collides")
        capabilities[cid] = cap
    if len(relation_records) != EXPECTED_RELATIONS:
        raise _err("exactly four source relations are required")

    # Link each candidate/relation source ref to the ledger observation it
    # declares: after checking the source_id exists and the content_sha256
    # pin equals the ledger source pin and the target observation's
    # inventory pin, dN maps to (source_id, kind=declaration, ordinal=N).
    # Element-pointer (eN) intersections are only supplementary review hints;
    # unresolved refs become explicit capability unknowns, never fabricated
    # HTTP observations, and linking never promotes review state.
    decl_by_key = {}
    for obs in observations:
        if obs.get("kind") == "declaration" and isinstance(obs.get("ordinal"), int):
            decl_by_key[(obs["source_id"], obs["ordinal"])] = obs
    obs_by_source = {}
    for obs in observations:
        obs_by_source.setdefault(obs["source_id"], []).append(obs)

    def _verify_candidate_ref_pin(sid, pin_sha):
        source = source_by_id.get(sid)
        if source is None:
            raise _err("candidate source_ref names an unknown ledger source")
        if not _is_sha256(pin_sha):
            raise _err("candidate source_ref is missing its content pin")
        ledger_pin = source.get("content_sha256")
        if not _is_sha256(ledger_pin) or pin_sha != ledger_pin:
            raise _err("candidate source_ref pin differs from the ledger pin")

    declaration_ref_count = 0
    for record in candidates:
        cid = record["capability_id"]
        target = (
            f"contract:{EXPECTED_SOURCE_RELATIONS[cid][0]}"
            if cid in relation_records
            else cid
        )
        unresolved = set()
        linked = set()
        for index, ref in enumerate(record.get("source_refs") or []):
            if not isinstance(ref, dict):
                continue
            sid = ref.get("source_id")
            if not isinstance(sid, str) or sid not in source_by_id:
                unresolved.add(f"unresolvable_source_ref_{index}")
                continue
            _verify_candidate_ref_pin(sid, ref.get("content_sha256"))
            declaration = ref.get("declaration")
            match = (
                re.fullmatch(r"d([0-9]+)", declaration)
                if isinstance(declaration, str)
                else None
            )
            if match is not None:
                obs = decl_by_key.get((sid, int(match.group(1))))
                if obs is not None:
                    inventory_pin = obs.get("inventory_content_sha256")
                    if not _is_sha256(inventory_pin) or inventory_pin != ref["content_sha256"]:
                        raise _err("declaration observation pin differs from the candidate pin")
                    linked.add(obs["observation_id"])
                    declaration_ref_count += 1
                else:
                    unresolved.add(f"unresolvable_declaration_ref_{index}")
            pointers = set()
            for pointer in ref.get("element_pointers") or []:
                if (
                    isinstance(pointer, str)
                    and pointer.startswith("e")
                    and pointer[1:].isdigit()
                ):
                    pointers.add(int(pointer[1:]))
            if pointers:
                for obs in obs_by_source.get(sid, ()):
                    if pointers & set(obs.get("element_indices") or []):
                        linked.add(obs["observation_id"])
        if linked:
            capabilities[target]["observation_refs"] = sorted(
                set(capabilities[target]["observation_refs"]) | linked
            )
            for oid in sorted(linked):
                review = observation_reviews[oid]
                if target not in review["capability_refs"]:
                    review["capability_refs"].append(target)
        if unresolved:
            capability = capabilities[target]
            capability["unknown_fields"] = sorted(
                set(capability["unknown_fields"]) | unresolved
            )
    if declaration_ref_count != EXPECTED_DECLARATION_REFS:
        raise _err("declaration refs present in the fixed input must total 47")
    for review in observation_reviews.values():
        review["capability_refs"].sort()

    source_relations = []
    for cid, record in relation_records.items():
        contract_op, kind = EXPECTED_SOURCE_RELATIONS[cid]
        ref = (record.get("source_refs") or [{}])[0]
        source_relations.append(
            {
                "relation_id": f"relation:{cid}",
                "candidate_ref": cid,
                "target_capability": f"contract:{contract_op}",
                "relation": kind,
                "source_ref": {
                    "source_id": ref.get("source_id"),
                    "content_sha256": ref.get("content_sha256"),
                    "section": ref.get("section"),
                    "declaration": ref.get("declaration"),
                    "element_pointers": list(ref.get("element_pointers") or []),
                },
                "review_state": "pending",
                "notes": (record.get("sdk_design_proposal") or {}).get("notes"),
            }
        )

    # The public-areas candidate is the only cohort member; keep its lineage.
    # Its two fixed cases must arrive through the structured capability_ids
    # refs already resolved above; the cohort is still planned, not admitted.
    public_area_cap = capabilities.get(PUBLIC_AREAS_CAPABILITY)
    if public_area_cap is None:
        raise _err("public areas candidate is missing")
    public_area_cap["acceptance_case_refs"] = sorted(
        set(public_area_cap["acceptance_case_refs"])
        | case_refs_by_cap.get(PUBLIC_AREAS_CAPABILITY, set())
    )
    if set(public_area_cap["acceptance_case_refs"]) != set(PUBLIC_AREAS_CASES):
        raise _err("public areas candidate must resolve exactly its two frozen cases")
    public_area_cap["cohort_id"] = PUBLIC_AREAS_COHORT
    public_area_cap["review"] = "candidate"
    public_area_cap["disposition"] = "implement"

    # --- material_reviews ------------------------------------------------------
    material_reviews = []
    for index, entry in enumerate(repo_obs):
        material_reviews.append(
            {
                "material_id": f"material:repository:{index}",
                "source_refs": [
                    {"input": "materials", "kind": "repository_observations", "index": index}
                ],
                "owner_queue": VERSION_REVIEW_QUEUE,
                "requirement": "required_review",
                "upstream_component": entry.get("repository"),
                "selected_version_or_commit": None,
                "artifact_sha256": None,
                "license_evidence_ref": None,
                "compatibility_scope": None,
                "reuse_mode": "pending",
                "capability_refs": [],
                "acceptance_case_refs": [],
                "review_state": "pending",
                "unknowns": ["selected_version", "artifact_sha256", "license_evidence", "compatibility_scope"],
            }
        )

    # Package review: the six repository package declarations (5 head + 1
    # release) and the five document package references are grouped by exact
    # package name into seven review objects. Every declaration keeps its own
    # version/license/verification fields; nothing upgrades to verified.
    package_declarations = {}
    for index, entry in enumerate(repo_obs):
        for field, role in (
            ("head_package_declaration", "head"),
            ("release_package_declaration", "release"),
        ):
            declaration = entry.get(field)
            if not isinstance(declaration, dict):
                continue
            name = declaration.get("name")
            if not isinstance(name, str) or not name:
                raise _err("package declaration without a name")
            package_declarations.setdefault(name, []).append(
                {
                    "input_ref": "materials",
                    "array": "repository_observations",
                    "record_index": index,
                    "field": field,
                    "role": role,
                    "declared_version": declaration.get("version"),
                    "declared_license": declaration.get("license"),
                    "distribution_registry_verified": declaration.get(
                        "distribution_registry_verified"
                    )
                    is True,
                }
            )
    for index, entry in enumerate(doc_refs):
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise _err("document package reference without a name")
        package_declarations.setdefault(name, []).append(
            {
                "input_ref": "materials",
                "array": "additional_official_document_package_references",
                "record_index": index,
                "role": "document",
                "declared_version": None,
                "declared_license": None,
                "distribution_registry_verified": entry.get(
                    "distribution_registry_verified"
                )
                is True,
            }
        )
    if set(package_declarations) != EXPECTED_PACKAGE_NAMES:
        raise _err("package review must cover exactly the seven frozen package names")
    if sum(len(refs) for refs in package_declarations.values()) != EXPECTED_PACKAGE_DECLARATIONS:
        raise _err("package review must retain all eleven declaration pointers")
    for name in sorted(package_declarations):
        refs = package_declarations[name]
        material_reviews.append(
            {
                "material_id": f"material:package:{name}",
                "source_refs": refs,
                "owner_queue": RELATED_MATERIAL_QUEUE,
                "requirement": "required_review",
                "upstream_component": name,
                "selected_version_or_commit": None,
                "artifact_sha256": None,
                "license_evidence_ref": None,
                "compatibility_scope": None,
                "reuse_mode": "pending",
                "capability_refs": [],
                "acceptance_case_refs": [],
                "review_state": "pending",
                "registry_distribution_verified": False,
                "unknowns": [
                    "selected_version",
                    "license_evidence",
                    "compatibility_scope",
                    "registry_distribution",
                ],
            }
        )

    discrepancies = materials.get("source_version_discrepancies") or []
    if len(discrepancies) != EXPECTED_DISCREPANCIES:
        raise _err("materials must carry exactly three source version discrepancies")
    for index, entry in enumerate(discrepancies):
        material_reviews.append(
            {
                "material_id": f"material:version_discrepancy:{index}",
                "source_refs": [
                    {
                        "input": "materials",
                        "kind": "source_version_discrepancies",
                        "index": index,
                        "topic": entry.get("topic"),
                    }
                ],
                "owner_queue": VERSION_REVIEW_QUEUE,
                "requirement": "required_review",
                "upstream_component": entry.get("topic"),
                "selected_version_or_commit": None,
                "artifact_sha256": None,
                "license_evidence_ref": None,
                "compatibility_scope": None,
                "reuse_mode": "pending",
                "capability_refs": [],
                "acceptance_case_refs": [],
                "review_state": "pending",
                "unknowns": ["frozen_portal_vs_head_vs_release"],
            }
        )

    for index, entry in enumerate(download_links):
        material_reviews.append(
            {
                "material_id": f"material:download_candidate:{index}",
                "source_refs": [
                    {
                        "input": "materials",
                        "kind": "download_or_sdk_link_candidates",
                        "index": index,
                        "source_file_sha256": entry.get("source_file_sha256"),
                    }
                ],
                "owner_queue": VERSION_REVIEW_QUEUE,
                "requirement": "required_review",
                # classification is only a category, not a verified upstream
                # component identity; the record pointer above keeps lineage.
                "upstream_component": None,
                "classification_hint": entry.get("classification"),
                "selected_version_or_commit": None,
                "artifact_sha256": None,
                "license_evidence_ref": None,
                "compatibility_scope": None,
                "reuse_mode": "pending",
                "capability_refs": [],
                "acceptance_case_refs": [],
                "review_state": "pending",
                "unknowns": ["upstream_component", "selected_version", "artifact_sha256", "license_evidence", "version_and_binary_hash_verified"],
            }
        )
    for index, entry in enumerate(proposals):
        material_reviews.append(
            {
                "material_id": f"material:source_set_proposal:{index}",
                "source_refs": [
                    {
                        "input": "materials",
                        "kind": "remaining_source_set_proposals",
                        "index": index,
                        "name": entry.get("name"),
                    }
                ],
                "owner_queue": RELATED_MATERIAL_QUEUE,
                "requirement": "required_review",
                "upstream_component": entry.get("name"),
                "selected_version_or_commit": None,
                "artifact_sha256": None,
                "license_evidence_ref": None,
                "compatibility_scope": None,
                "reuse_mode": "pending",
                "capability_refs": [],
                "acceptance_case_refs": [],
                "review_state": "pending",
                "unknowns": ["admission", "required_pin_fields"],
            }
        )

    # --- queues ----------------------------------------------------------------
    queues_out = []
    for qid in QUEUE_ORDER[:8]:
        queue = next(q for q in queue_list if _queue_id(q) == qid)
        queues_out.append(
            {
                "queue_id": qid,
                "batch": queue.get("batch"),
                "worker": queue.get("worker"),
                "owner": queue.get("owner"),
                "scope": queue.get("scope"),
                "source_count": queue.get("source_count", len(queue.get("sources") or [])),
                "component_families": list(queue.get("component_families") or []),
                "classification_counts": dict(queue.get("classification_counts") or {}),
            }
        )
    queues_out.append(
        {
            "queue_id": "overseas_portal",
            "batch": "overseas",
            "worker": None,
            "owner": None,
            "scope": "overseas portal sources (52) plus the 49 reviewed candidates",
            "source_count": OVERSEAS_PORTAL_TOTAL,
            "component_families": [],
            "classification_counts": {},
        }
    )
    queues_out.append(
        {
            "queue_id": RELATED_MATERIAL_QUEUE,
            "batch": "related_material",
            "worker": None,
            "owner": None,
            "scope": "static/guide reference (293) + declared assets (1477) review responsibility",
            "source_count": STATIC_REF_TOTAL + ASSET_TOTAL,
            "component_families": [],
            "classification_counts": {},
        }
    )
    queues_out.append(
        {
            "queue_id": VERSION_REVIEW_QUEUE,
            "batch": "related_material_version",
            "worker": None,
            "owner": None,
            "scope": "repository pins / distribution reviews / binary/download source pointers / source-set proposals",
            "source_count": 0,
            "component_families": [],
            "classification_counts": {},
        }
    )

    # --- cohorts -----------------------------------------------------------------
    cohorts = [
        {
            "cohort_id": PUBLIC_AREAS_COHORT,
            "admission_state": "planned",
            "admitted": False,
            "capability_refs": [PUBLIC_AREAS_CAPABILITY],
            "runtime_name": "settings.list_areas",
            "registry_operation_id": "global.settings.list_areas",
            "sdk_entry_proposal": "settings.list_areas",
            "acceptance_case_refs": list(PUBLIC_AREAS_CASES),
            "plan_refs": [rel_plan_area, rel_plan_scope],
            "notes": "planned only; self-admission is not permitted even when material appears sufficient",
        }
    ]

    # --- summary -----------------------------------------------------------------
    review_states = {}
    for review in source_reviews.values():
        review_states[review["review_state"]] = review_states.get(review["review_state"], 0) + 1
    summary = {
        "source_count": len(source_reviews),
        "observation_count": len(observation_reviews),
        "capability_count": len(capabilities),
        "source_relation_count": len(source_relations),
        "material_review_count": len(material_reviews),
        "queue_count": len(queues_out),
        "cohort_count": len(cohorts),
        "source_review_states": review_states,
        "phase_2_deferred": list(PHASE_2_AUTHORIZED),
        "counts_are_not_api_totals": True,
    }

    # --- conservation assertions -------------------------------------------------
    if len(source_reviews) != EXPECTED_SOURCES or set(source_reviews) != set(source_by_id):
        raise _err("source review conservation failed")
    if len(observation_reviews) != EXPECTED_OBSERVATIONS or set(observation_reviews) != set(obs_by_id):
        raise _err("observation review conservation failed")
    contract_caps = {
        cid for cid, c in capabilities.items() if c["capability_kind"] == "reviewed_contract"
    }
    if len(contract_caps) != EXPECTED_CONTRACTS:
        raise _err("reviewed-contract capability set conservation failed")
    expected_new = {r["capability_id"] for r in candidates} - set(EXPECTED_SOURCE_RELATIONS)
    new_caps = {
        cid
        for cid, c in capabilities.items()
        if c["capability_kind"] == "overseas_review_candidate"
    }
    if new_caps != expected_new or len(new_caps) != EXPECTED_NEW_SLOTS:
        raise _err("new-candidate capability set conservation failed")
    if set(relation_records) != set(EXPECTED_SOURCE_RELATIONS):
        raise _err("source-relation identity set conservation failed")
    if sorted(q["queue_id"] for q in queues_out[:8]) != sorted(EXPECTED_QUEUE_COUNTS):
        raise _err("queues_out must carry the eight real batch queue ids")
    for entry in queues_out[:8]:
        if entry["source_count"] != EXPECTED_QUEUE_COUNTS[entry["queue_id"]]:
            raise _err("queues_out counts must equal the frozen queue counts")
    # A structured operation_id resolves to an existing contract:<op> or to
    # the unique planned registry_operation_id a cohort declares (frozen,
    # not yet implemented; no contract slot is fabricated). The planned
    # branch requires: unique across cohorts, all cohort capability_refs
    # resolve, this case's capability_ids hit the cohort, and this case id
    # is a cohort acceptance_case_ref. Other unregistered ops still fail.
    planned_ops = {}
    for cohort in cohorts:
        planned_op = cohort.get("registry_operation_id")
        if isinstance(planned_op, str) and planned_op:
            if planned_op in planned_ops:
                raise _err("planned registry_operation_id is not unique across cohorts")
            planned_ops[planned_op] = cohort
    for case in acceptance_cases:
        case_caps = set(case.get("capability_ids") or [])
        for cap_id in case_caps:
            if isinstance(cap_id, str) and cap_id and cap_id not in capabilities:
                raise _err("structured acceptance capability_id does not resolve")
        for op in case.get("operation_ids") or []:
            if not isinstance(op, str) or not op or f"contract:{op}" in capabilities:
                continue
            cohort = planned_ops.get(op)
            if (
                cohort is None
                or any(ref not in capabilities for ref in cohort["capability_refs"])
                or not case_caps & set(cohort["capability_refs"])
                or case["case_id"] not in cohort["acceptance_case_refs"]
            ):
                raise _err("structured acceptance operation_id does not resolve")
    for cap in capabilities.values():
        for case_id in cap["acceptance_case_refs"]:
            if case_id not in acceptance_ids:
                raise _err("capability acceptance case ref does not resolve")
    for cid, record in relation_records.items():
        ref = (record.get("source_refs") or [{}])[0]
        sid = ref.get("source_id") if isinstance(ref, dict) else None
        if not isinstance(sid, str) or sid not in source_by_id:
            raise _err("relation source ref does not resolve")
    domestic_owned = sum(
        1
        for s in source_reviews.values()
        if s["owner_queue"] in QUEUE_ORDER[:8]
    )
    if domestic_owned != DOMESTIC_PORTAL_TOTAL:
        raise _err("domestic ownership conservation failed")
    ledger_domestic = {
        sid.split(":", 1)[1]
        for sid, s in source_by_id.items()
        if s.get("kind") == "portal_document" and s.get("region") == "domestic"
    }
    if set(source_owner) != ledger_domestic:
        raise _err("SP009 identity set must equal the ledger domestic portal set")
    if sum(1 for s in source_reviews.values() if s["owner_queue"] == "overseas_portal") != OVERSEAS_PORTAL_TOTAL:
        raise _err("overseas ownership conservation failed")
    related = sum(
        1
        for s in source_reviews.values()
        if s["owner_queue"] == RELATED_MATERIAL_QUEUE
    )
    if related != STATIC_REF_TOTAL + ASSET_TOTAL:
        raise _err("related-material ownership conservation failed")
    by_kind = {}
    for s in source_by_id.values():
        by_kind[s.get("kind")] = by_kind.get(s.get("kind"), 0) + 1
    if by_kind.get("sdk_reference") != STATIC_REF_TOTAL or by_kind.get("asset") != ASSET_TOTAL:
        raise _err("static/asset source counts do not match the frozen layout")
    package_refs = [
        ref
        for review in material_reviews
        if isinstance(review.get("material_id"), str)
        and review["material_id"].startswith("material:package:")
        for ref in review["source_refs"]
    ]
    if len(package_refs) != EXPECTED_PACKAGE_DECLARATIONS:
        raise _err("package declaration pointer conservation failed")
    discrepancy_refs = [
        review
        for review in material_reviews
        if isinstance(review.get("material_id"), str)
        and review["material_id"].startswith("material:version_discrepancy:")
    ]
    if len(discrepancy_refs) != EXPECTED_DISCREPANCIES:
        raise _err("version discrepancy review conservation failed")
    for oid, review in observation_reviews.items():
        for cap_ref in review["capability_refs"]:
            if cap_ref not in capabilities:
                raise _err("observation capability ref does not resolve")
    for cap in capabilities.values():
        if cap["owner_queue"] is None:
            raise _err("capability without a unique owner queue")

    # --- inputs --------------------------------------------------------------------
    inputs = {
        "reviewed_contracts": {"path": rel_contracts, "sha256": pins["reviewed_contracts"]["sha256"], "schema": contracts_data.get("schema_version")},
        "sdk_bindings": {"path": rel_bindings, "sha256": pins["sdk_bindings"]["sha256"], "schema": bindings_data.get("schema_version")},
        "source_issues": {"path": rel_issues, "sha256": pins["source_issues"]["sha256"], "schema": issues_data.get("schema_version")},
        "source_ledger": {"path": rel, "sha256": pins["ledger"]["sha256"], "schema": ledger.get("schema_version"), "publication_profile": ledger.get("publication_profile")},
        "queues": {"path": rel_queues, "sha256": pins["queues"]["sha256"], "schema": queues_data.get("schema_version")},
        "materials": {"path": rel_materials, "sha256": pins["materials"]["sha256"], "schema": materials.get("schema_version")},
        "overseas_review_candidates": {"path": rel_cand, "sha256": pins["overseas_review_candidates"]["sha256"], "schema": candidates_data.get("schema_version")},
        "acceptance": {"path": rel_accept, "sha256": pins["acceptance"]["sha256"], "schema": acceptance.get("schema_version")},
        "plan_scope_register": {"path": rel_plan_scope, "sha256": pins["plan_scope_register"]["sha256"]},
        "plan_public_area": {"path": rel_plan_area, "sha256": pins["plan_public_area"]["sha256"]},
    }

    register = {
        "schema_version": 1,
        "scope_revision": SCOPE_REVISION,
        "baseline_commit": BASELINE_COMMIT,
        "inputs": inputs,
        "scope_state": {
            "accountability_registered": True,
            "scope_and_priority_ratified": False,
            "all_semantics_dispositioned": False,
            "required_acceptance_complete": False,
            "product_complete": False,
        },
        "queues": queues_out,
        "capabilities": [capabilities[key] for key in sorted(capabilities)],
        "source_relations": source_relations,
        "source_reviews": [source_reviews[key] for key in sorted(source_reviews)],
        "observation_reviews": [observation_reviews[key] for key in sorted(observation_reviews)],
        "material_reviews": material_reviews,
        "cohorts": cohorts,
        "summary": summary,
    }
    return register


def _write_output(output_path, text, authorized_path):
    """Write to a brand-new path only; never overwrite an existing file.

    The resolved target must equal the single authorized register path under
    --repo (docs/capabilities/scope-register.json); any other path, whether
    new or existing, is rejected before any write is attempted.
    """
    output = Path(output_path)
    try:
        resolved = output.resolve()
    except (OSError, RuntimeError):
        raise _err("output path cannot be resolved") from None
    if resolved != authorized_path:
        raise _err("output path must be docs/capabilities/scope-register.json under --repo")
    if output.exists() or os.path.lexists(output):
        raise _err("output path already exists")
    tmp = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=".scope-register-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp = Path(handle.name)
            handle.write(text)
        try:
            os.link(tmp, output)
        except FileExistsError:
            raise _err("output path already exists") from None
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise _err("output path already exists") from None
            raise _err("cannot write the output register") from None
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="assemble_scope_register",
        description="One-shot SP023 scope-register assembly (initialization data only).",
    )
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--queues", required=True, type=Path)
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--pins", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        pins = _load_pins(args.pins)
        register = assemble(
            args.repo,
            str(args.ledger),
            str(args.queues),
            str(args.materials),
            pins,
        )
        text = json.dumps(register, ensure_ascii=False, separators=(",", ":")) + "\n"
        authorized = (args.repo.resolve() / AUTHORIZED_OUTPUT_REL).resolve()
        _write_output(args.output, text, authorized)
        summary = register["summary"]
        sys.stdout.write(
            "scope-register written: "
            f"sources={summary['source_count']} "
            f"observations={summary['observation_count']} "
            f"capabilities={summary['capability_count']} "
            f"relations={summary['source_relation_count']} "
            f"materials={summary['material_review_count']} "
            f"queues={summary['queue_count']} "
            f"cohorts={summary['cohort_count']}\n"
        )
        return 0
    except AssemblyError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except Exception:
        sys.stderr.write("assemble_scope_register: unexpected failure\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
