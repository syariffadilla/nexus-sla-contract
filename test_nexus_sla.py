"""
Automated tests for NexusSLA (v0.3.0), using GenLayer Testing Suite's
Direct Mode -- runs the contract's Python in-memory, in milliseconds,
without needing GenLayer Studio/Docker running. Confirmed API (as of
writing) against genlayer-test's own PyPI documentation:

    def test_x(direct_vm, direct_deploy): ...
    direct_vm.sender = some_address           # set caller
    with direct_vm.prank(address): ...        # call as a specific address
    with direct_vm.expect_revert("message"):  # assert a call reverts
    direct_vm.mock_llm(r"regex", "response")  # mock gl.nondet.exec_prompt
                                               # (and the eq_principle.*
                                               # variants) by substring/regex
                                               # match against the prompt text
    direct_alice, direct_bob, direct_charlie  # ready-made test addresses
    direct_accounts                           # list of 10 test addresses

NOT independently re-verified by running this suite in this session --
if direct_vm's exact method names differ in your installed genlayer-test
version, fix the helpers at the top of this file first; every test below
goes through them.

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
BOND = 1_000_000_000_000_000_000  # 1 GEN, wei-scale (see formatters.ts note)
TIER_THRESHOLDS = [9990, 9900, 9500]
TIER_PENALTIES = [500, 1500, 4000]


def deploy_default(direct_deploy, provider, client):
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
            ],
        )


def test_constructor_rejects_end_before_start(direct_vm, direct_deploy, direct_alice, direct_bob):
    with direct_vm.expect_revert("end harus setelah start"):
        direct_deploy(
            NexusSLA,
            args=[
                direct_alice, direct_bob, json.dumps(EVIDENCE_DOMAINS), QUORUM,
                END, START, BOND, json.dumps(TIER_THRESHOLDS), json.dumps(TIER_PENALTIES),
            ],
        )


def test_constructor_rejects_quorum_above_source_count(direct_vm, direct_deploy, direct_alice, direct_bob):
    with direct_vm.expect_revert("quorum_required tidak valid"):
        direct_deploy(
            NexusSLA,
            args=[
                direct_alice, direct_bob, json.dumps(EVIDENCE_DOMAINS), 3,
                START, END, BOND, json.dumps(TIER_THRESHOLDS), json.dumps(TIER_PENALTIES),
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
# file_claim access control and input validation (no LLM needed yet --
# these all fail before gl.nondet is ever reached)
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
                "https://evil.com/?x=monitor.example-thirdparty.com",  # spoofed domain
            ]))


def test_file_claim_rejects_duplicate_domain(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Dua URL mengarah ke domain yang sama"):
            contract.file_claim(json.dumps([
                "https://status.example.com/history",
                "https://status.example.com/incidents",  # same domain twice
            ]))


# ---------------------------------------------------------------------
# file_claim with mocked AI consensus -- the part that needed
# direct_vm.mock_llm to test deterministically
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


def test_file_claim_dismissed_without_reverting_when_no_consensus(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A claim the AI can't substantiate should record a DISMISSED entry
    and leave the contract ACTIVE -- not revert the transaction. This is
    the behavior the contract's own docstring calls out as deliberate
    (the client still gets an auditable record, not just a bare revert)."""
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)

    direct_vm.mock_llm(r".*", NO_INCIDENT_RESPONSE)

    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    state = json.loads(contract.get_state())
    assert state["state"] == "ACTIVE"  # not CLAIM_PENDING
    assert len(state["history"]) == 1
    assert state["history"][0]["status"] == "DISMISSED"


def test_file_claim_valid_incident_moves_to_claim_pending(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)

    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    state = json.loads(contract.get_state())
    assert state["state"] == "CLAIM_PENDING"
    assert state["pending_claim"]["incident_id"] == "test-incident-001"
    assert state["pending_claim"]["payout_amount"] > 0


def test_duplicate_incident_id_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    urls = json.dumps([
        "https://status.example.com/history",
        "https://monitor.example-thirdparty.com/incidents",
    ])
    with direct_vm.prank(direct_bob):
        contract.file_claim(urls)
    with direct_vm.prank(direct_bob):
        contract.finalize_claim()  # settle it so state goes back to ACTIVE
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Incident ID ini sudah pernah diproses sebelumnya"):
            contract.file_claim(urls)  # same mocked incident_id again


# ---------------------------------------------------------------------
# finalize_claim: payout math and bond cap
# ---------------------------------------------------------------------

def test_finalize_claim_pays_and_caps_at_remaining_bond(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    direct_vm.mock_llm(r".*", VALID_INCIDENT_RESPONSE)

    with direct_vm.prank(direct_bob):
        contract.file_claim(json.dumps([
            "https://status.example.com/history",
            "https://monitor.example-thirdparty.com/incidents",
        ]))

    state_before = json.loads(contract.get_state())
    expected_payout = state_before["pending_claim"]["payout_amount"]
    assert expected_payout <= BOND  # payout can never exceed the locked bond

    with direct_vm.prank(direct_bob):
        contract.finalize_claim()

    state_after = json.loads(contract.get_state())
    assert state_after["state"] == "ACTIVE"
    assert state_after["remaining_bond"] == BOND - expected_payout
    assert state_after["history"][-1]["finalized"] is True


# ---------------------------------------------------------------------
# withdraw_remaining_bond
# ---------------------------------------------------------------------

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
    """Relies on gl.block.timestamp being before `end` in the test
    environment's default clock. If your direct_vm exposes a way to set
    the simulated block time, prefer asserting the exact boundary instead
    of relying on wall-clock-vs-END being true by coincidence."""
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Periode SLA belum berakhir"):
            contract.withdraw_remaining_bond()


def test_only_provider_can_withdraw(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = deploy_default(direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, contract, direct_alice)
    with direct_vm.prank(direct_bob):  # client, not provider
        with direct_vm.expect_revert("Hanya provider yang boleh menarik sisa bond"):
            contract.withdraw_remaining_bond()
