"""
Automated tests for NexusSLA (v0.4.0), using GenLayer Testing Suite's
Direct Mode -- runs the contract's Python in-memory, in milliseconds,
without needing GenLayer Studio/Docker running.

Includes coverage for all corrected paths requested by steward:
1. Enforceable time-based dispute period before finalize_claim
2. Inconclusive dispute retry when evidence is unavailable or invalid
3. Strict incident timing validation (ordering, SLA bounds, future prevention)
4. Strict impact and agreeing-source count validation before settlement

Run with:
    pip install genlayer-test pytest
    gltest tests/test_nexus_sla.py
"""
import json
import pytest
from genlayer import *
from nexus_sla import NexusSLA


# ---------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------

EVIDENCE_DOMAINS = ["status.example.com", "monitor.example-thirdparty.com"]
QUORUM = 2
START = 1_700_000_000
END = 1_800_000_000
BOND = 1_000_000_000_000_000_000  # 1 GEN, wei-scale
TIER_THRESHOLDS = [9990, 9900, 9500]
TIER_PENALTIES = [500, 1500, 4000]
DISPUTE_PERIOD = 86400  # 24 hours


def deploy_default(direct_deploy, provider, client, dispute_period=DISPUTE_PERIOD):
    return direct_deploy(
        NexusSLA,
        args=[
            provider,
            client,
            json.dumps(EVIDENCE_DOMAINS),
            QUORUM,
            START,
            END,
            BOND,
            json.dumps(TIER_THRESHOLDS),
            json.dumps(TIER_PENALTIES),
            dispute_period,
        ],
    )


def fund(direct_vm, contract, provider):
    with direct_vm.prank(provider):
        contract.deposit_bond(value=BOND)


# ---------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------

def test_constructor_rejects_same_provider_and_client(direct_vm, direct_deploy, direct_alice):
    with direct_vm.expect_revert("Provider dan client harus berbeda"):
        deploy_default(direct_deploy, direct_alice, direct_alice)


def test_constructor_rejects_zero_bond(direct_vm, direct_deploy, direct_alice, direct_bob):
    with direct_vm.expect_revert("Bond amount harus positif"):
        direct_deploy(
            NexusSLA,
            args=[
                direct_alice, direct_bob, json.dumps(EVIDENCE_DOMAINS), QUORUM,
                START, END, 0, json.dumps(TIER_THRESHOLDS), json.dumps(TIER_PENALTIES),
                DISPUTE_PERIOD,
            ],
        )


def test_constructor_rejects_end_before_start(direct_vm, direct_deploy, direct_alice, direct_bob):
    with direct_vm.expect_revert("end harus setelah start"):
        direct_deploy(
            NexusSLA,
            args=[
                direct_alice, direct_bob, json.dumps(EVIDENCE_DOMAINS), QUORUM,
                END, START, BOND, json.dumps(TIER_THRESHOLDS), json.dumps(TIER_PENALTIES),
                DISPUTE_PERIOD,
            ],
        )


def test_constructor_rejects_quorum_above_source_count(direct_vm, direct_deploy, direct_alice, direct_bob):
    with direct_vm.expect_revert("quorum_required tidak valid"):
        direct_deploy(
            NexusSLA,
            args=[
                direct_alice, direct_bob, json.dumps(EVIDENCE_DOMAINS), 3,
                START, END, BOND, json.dumps(TIER_THRESHOLDS), json.dumps(TIER_PENALTIES),
                DISPUTE_PERIOD,
            ],
        )


def test_constructor_rejects_nonpositive_dispute_period(direct_vm, direct_deploy, direct_alice, direct_bob):
    with direct_vm.expect_revert("dispute_period_seconds harus positif"):
        direct_deploy(
            NexusSLA,
            args=[
                direct_alice, direct_bob, json.dumps(EVIDENCE_DOMAINS), QUORUM,
                START, END, BOND, json.dumps(TIER_THRESHOLDS), json.dumps(TIER_PENALTIES),
                0,
            ],
        )


# ---------------------------------------------------------------------
# deposit_bond access control and validation
# ---------------------------------------------------------------------

def test_only_provider_can_deposit(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    with direct_vm.prank(direct_bob):  # client, not provider
        with direct_vm.expect_revert("Hanya provider yang boleh deposit"):
            contract.deposit_bond(value=BOND)


def test_deposit_wrong_amount_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Deposit harus sama dengan bond_amount"):
            contract.deposit_bond(value=BOND - 1)


def test_deposit_activates_contract(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    state = json.loads(contract.get_state())
    assert state["state"] == "ACTIVE"
    assert state["remaining_bond"] == BOND


def test_double_deposit_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Bond sudah disetor"):
            contract.deposit_bond(value=BOND)


# ---------------------------------------------------------------------
# file_claim access control and input validation
# ---------------------------------------------------------------------

def test_only_client_can_file_claim(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_alice):  # provider, not client
        with direct_vm.expect_revert("Hanya client yang boleh mengajukan klaim"):
            contract.file_claim(json.dumps(["https://status.example.com/history"]))


def test_file_claim_requires_quorum_count_of_urls(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Jumlah URL kurang dari kuorum yang disyaratkan"):
            contract.file_claim(json.dumps(["https://status.example.com/history"]))  # only 1, need 2


def test_file_claim_rejects_unregistered_domain(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Domain tidak terdaftar"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://evil.com/?x=monitor.example-thirdparty.com",
            ]))


def test_file_claim_rejects_duplicate_domain(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Dua URL mengarah ke domain yang sama"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://status.example.com/incidents",
            ]))


# ---------------------------------------------------------------------
# Strict incident timing, impact, and agreeing-source count validation
# ---------------------------------------------------------------------

NO_INCIDENT_RESPONSE = json.dumps({
    "consensus_reached": False, "incident_id": "", "start_time_unix": 0,
    "end_time_unix": 0, "impact": "none", "sources_agreeing": 0,
})

VALID_INCIDENT_RESPONSE = json.dumps({
    "consensus_reached": True, "incident_id": "test-incident-001",
    "start_time_unix": START + 1000, "end_time_unix": START + 4600,
    "impact": "major", "sources_agreeing": QUORUM,
})


def test_file_claim_reverts_when_incident_start_after_end(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", json.dumps({
        "consensus_reached": True, "incident_id": "inverted-times",
        "start_time_unix": START + 5000, "end_time_unix": START + 1000,
        "impact": "major", "sources_agreeing": QUORUM,
    }))
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Waktu mulai insiden tidak boleh setelah waktu selesai"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))


def test_file_claim_reverts_when_incident_before_sla_start(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", json.dumps({
        "consensus_reached": True, "incident_id": "too-early",
        "start_time_unix": START - 1000, "end_time_unix": START + 1000,
        "impact": "major", "sources_agreeing": QUORUM,
    }))
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Waktu insiden sebelum periode SLA dimulai"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))


def test_file_claim_reverts_when_incident_after_sla_end(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", json.dumps({
        "consensus_reached": True, "incident_id": "too-late",
        "start_time_unix": START + 1000, "end_time_unix": END + 1000,
        "impact": "major", "sources_agreeing": QUORUM,
    }))
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Waktu insiden setelah periode SLA berakhir"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))


def test_file_claim_reverts_when_incident_in_future(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    # block timestamp simulated clock is around 1_750_000_000
    future_time = int(gl.block.timestamp) + 100_000
    direct_vm.mock_llm(r".*", json.dumps({
        "consensus_reached": True, "incident_id": "future-incident",
        "start_time_unix": future_time, "end_time_unix": future_time + 3600,
        "impact": "major", "sources_agreeing": QUORUM,
    }))
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Waktu insiden tidak boleh di masa depan"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))


def test_file_claim_reverts_on_invalid_impact(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", json.dumps({
        "consensus_reached": True, "incident_id": "bad-impact",
        "start_time_unix": START + 1000, "end_time_unix": START + 2000,
        "impact": "catastrophic", "sources_agreeing": QUORUM,
    }))
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Level impact tidak valid"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))


def test_file_claim_dismissed_when_agreeing_sources_below_quorum(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    # Only 1 source agrees, but QUORUM is 2 -> must be DISMISSED without affecting settlement!
    direct_vm.mock_llm(r".*", json.dumps({
        "consensus_reached": True, "incident_id": "under-quorum",
        "start_time_unix": START + 1000, "end_time_unix": START + 2000,
        "impact": "major", "sources_agreeing": 1,
    }))
    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    state = json.loads(contract.get_state())
    assert state["state"] == "ACTIVE"  # remains ACTIVE, no payout pending
    assert len(state["history"]) == 1
    assert state["history"][0]["status"] == "DISMISSED"
    assert "Kuorum sumber tidak terpenuhi" in state["history"][0]["reason"]


def test_file_claim_dismissed_when_agreeing_sources_exceed_total(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    # 5 sources reported agreeing when only 2 were provided -> hallucination rejected
    direct_vm.mock_llm(r".*", json.dumps({
        "consensus_reached": True, "incident_id": "over-total",
        "start_time_unix": START + 1000, "end_time_unix": START + 2000,
        "impact": "major", "sources_agreeing": 5,
    }))
    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    state = json.loads(contract.get_state())
    assert state["state"] == "ACTIVE"
    assert len(state["history"]) == 1
    assert state["history"][0]["status"] == "DISMISSED"


# ---------------------------------------------------------------------
# Time-based dispute period enforcement
# ---------------------------------------------------------------------

def test_finalize_claim_reverts_before_dispute_period_expires(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob, dispute_period=86400)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    # Attempt immediate finalize without elapsed dispute period
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Periode dispute belum berakhir"):
            contract.finalize_claim()


def test_finalize_claim_succeeds_after_dispute_period_expires(direct_vm, direct_deploy, direct_alice, direct_bob):
    # Set dispute_period to 10 seconds for test
    contract = deploy_default(direct_deploy, direct_alice, direct_bob, dispute_period=10)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    # Advance block timestamp beyond dispute_period
    orig_ts = gl.block.timestamp
    gl.block.timestamp = orig_ts + 20

    try:
        with direct_vm.prank(direct_bob):
            contract.finalize_claim()

        state = json.loads(contract.get_state())
        assert state["state"] == "ACTIVE"
        assert state["history"][-1]["finalized"] is True
    finally:
        gl.block.timestamp = orig_ts


def test_dispute_claim_reverts_after_dispute_period_expires(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob, dispute_period=10)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    # Advance time beyond dispute window
    orig_ts = gl.block.timestamp
    gl.block.timestamp = orig_ts + 20

    try:
        with direct_vm.prank(direct_alice):
            with direct_vm.expect_revert("Periode dispute telah berakhir"):
                contract.dispute_claim(json.dumps([
                    "https://status.example.com/history",
                    "https://monitor.example-thirdparty.com/incidents",
                ]))
    finally:
        gl.block.timestamp = orig_ts


# ---------------------------------------------------------------------
# Inconclusive dispute retry paths
# ---------------------------------------------------------------------

def test_dispute_claim_unavailable_or_inconclusive_evidence_reverts_for_retry(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)

    # First, file a valid claim
    direct_vm.mock_llm(r".*EVIDENCE SOURCES:.*", VALID_INCIDENT_RESPONSE)
    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    # Now simulate inconclusive counter-evidence (e.g. status page unreachable, ambiguous proof)
    direct_vm.mock_llm(r".*COUNTER-EVIDENCE.*", json.dumps({
        "status": "INCONCLUSIVE",
        "upheld": False,
        "revised_impact": "none",
        "sources_agreeing": 0,
    }))

    # Calling dispute_claim MUST produce an inconclusive revert rather than locking the claim or upholding payout
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Bukti dispute tidak tersedia atau tidak konklusif; silakan coba lagi"):
            contract.dispute_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))

    # CRITICAL: Claim must NOT be permanently marked as disputed, and payout remains untouched for retry!
    state = json.loads(contract.get_state())
    assert state["state"] == "CLAIM_PENDING"
    assert state["pending_claim"]["disputed"] is False


def test_dispute_claim_can_retry_after_inconclusive_failure(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)

    # File valid claim
    direct_vm.mock_llm(r".*EVIDENCE SOURCES:.*", VALID_INCIDENT_RESPONSE)
    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    # 1st attempt: Inconclusive
    direct_vm.mock_llm(r".*COUNTER-EVIDENCE.*", json.dumps({
        "status": "INCONCLUSIVE", "upheld": False, "revised_impact": "none", "sources_agreeing": 0,
    }))
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Bukti dispute tidak tersedia atau tidak konklusif; silakan coba lagi"):
            contract.dispute_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))

    # 2nd attempt: Successful retry with conclusive counter-evidence proving service was UP
    direct_vm.mock_llm(r".*COUNTER-EVIDENCE.*", json.dumps({
        "status": "DISMISSED", "upheld": False, "revised_impact": "none", "sources_agreeing": QUORUM,
    }))
    with direct_vm.prank(direct_alice):
        contract.dispute_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    # Settlement penalty successfully zeroed!
    state = json.loads(contract.get_state())
    assert state["state"] == "DISPUTE_WINDOW"
    assert state["pending_claim"]["disputed"] is True
    assert state["pending_claim"]["payout_amount"] == 0
    assert state["pending_claim"]["penalty_bps"] == 0


def test_dispute_claim_reverts_on_invalid_revised_impact(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)

    direct_vm.mock_llm(r".*EVIDENCE SOURCES:.*", VALID_INCIDENT_RESPONSE)
    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    # Return invalid revised_impact
    direct_vm.mock_llm(r".*COUNTER-EVIDENCE.*", json.dumps({
        "status": "UPHELD", "upheld": True, "revised_impact": "invalid_tier", "sources_agreeing": QUORUM,
    }))
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Level revised_impact tidak valid"):
            contract.dispute_claim(json.dumps([
                "https://status.example.com/history",
                "https://monitor.example-thirdparty.com/incidents",
            ]))


# ---------------------------------------------------------------------
# Duplicate incident ID, bond capping, and withdrawals
# ---------------------------------------------------------------------

def test_duplicate_incident_id_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob, dispute_period=10)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    urls = json.dumps([
        "https://status.example.com/history",
        "https://monitor.example-thirdparty.com/incidents",
    ])
    with direct_vm.prank(direct_bob):
        contract.file_claim(urls)

    # Fast forward past dispute period and finalize
    orig_ts = gl.block.timestamp
    gl.block.timestamp = orig_ts + 20
    try:
        with direct_vm.prank(direct_bob):
            contract.finalize_claim()
        with direct_vm.prank(direct_bob):
            with direct_vm.expect_revert("Incident ID ini sudah pernah diproses sebelumnya"):
                contract.file_claim(urls)
    finally:
        gl.block.timestamp = orig_ts


def test_withdraw_blocked_while_claim_pending(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Tidak bisa withdraw saat klaim pending"):
            contract.withdraw_remaining_bond()


def test_withdraw_blocked_before_sla_end(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Periode SLA belum berakhir"):
            contract.withdraw_remaining_bond()


def test_only_provider_can_withdraw(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Hanya provider yang boleh menarik sisa bond"):
            contract.withdraw_remaining_bond()
