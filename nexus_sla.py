# v0.4.0 - NexusSLA (Fair & Impartial AI Court)
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
NexusSLA: SLA Dispute Court & Reliability Enforcement.
Impartially evaluates uptime evidence using multiple independent sources.
"""

from genlayer import *
import json


class NexusSLA(gl.Contract):
    provider: Address
    client: Address
    evidence_domains_json: str
    quorum_required: u256
    start: u256
    end: u256
    bond_amount: u256
    bond_deposited: bool
    remaining_bond: u256
    state: str
    tier_thresholds_json: str
    tier_penalties_json: str
    dispute_period_seconds: u256
    claims_history_json: str
    pending_claim_json: str

    def __init__(
        self,
        provider: str,
        client: str,
        evidence_domains_json: str,
        quorum_required: int,
        start: int,
        end: int,
        bond_amount: int,
        tier_uptime_thresholds_json: str,
        tier_penalty_json: str,
        dispute_period_seconds: int = 86400,
    ):
        provider_addr = Address(provider)
        client_addr = Address(client)
        assert provider_addr != client_addr, "Provider dan client harus berbeda"
        assert bond_amount > 0, "Bond amount harus positif"
        assert end > start, "end harus setelah start"
        assert dispute_period_seconds > 0, "dispute_period_seconds harus positif"

        domains = json.loads(evidence_domains_json)
        assert len(domains) >= 1, "Minimal 1 domain bukti terdaftar"
        assert 1 <= quorum_required <= len(domains), "quorum_required tidak valid"

        self.provider = provider_addr
        self.client = client_addr
        self.evidence_domains_json = evidence_domains_json
        self.quorum_required = u256(quorum_required)
        self.start = u256(start)
        self.end = u256(end)
        self.bond_amount = u256(bond_amount)
        self.bond_deposited = False
        self.remaining_bond = u256(0)
        self.state = "UNINITIALIZED"
        self.tier_thresholds_json = tier_uptime_thresholds_json
        self.tier_penalties_json = tier_penalty_json
        self.dispute_period_seconds = u256(dispute_period_seconds)
        self.claims_history_json = "[]"
        self.pending_claim_json = "{}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _domain_of(self, url: str) -> str:
        s = url.strip()
        if "://" in s:
            s = s.split("://", 1)[1]
        for sep in ("/", "?", "#"):
            idx = s.find(sep)
            if idx != -1:
                s = s[:idx]
        if "@" in s:
            s = s.split("@", 1)[1]
        if ":" in s:
            s = s.split(":", 1)[0]
        s = s.lower()
        if s.startswith("www."):
            s = s[4:]
        return s

    def _matches_registered_domain(self, hostname: str) -> bool:
        registered_domains = json.loads(self.evidence_domains_json)
        for registered in registered_domains:
            reg = str(registered).lower()
            if reg.startswith("www."):
                reg = reg[4:]
            if hostname == reg or hostname.endswith("." + reg):
                return True
        return False

    def _validate_evidence_urls(self, urls) -> None:
        if len(urls) < int(self.quorum_required):
            raise Exception("Jumlah URL kurang dari kuorum yang disyaratkan")
        seen_domains = []
        for url in urls:
            hostname = self._domain_of(str(url))
            if not self._matches_registered_domain(hostname):
                raise Exception(f"Domain tidak terdaftar: {hostname}")
            if hostname in seen_domains:
                raise Exception("Dua URL mengarah ke domain yang sama")
            seen_domains.append(hostname)

    def _clean_json_response(self, raw_str: str) -> str:
        s = str(raw_str).strip()
        if s.startswith("```"):
            s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return s

    def _compute_penalty_bps(self, downtime_minutes: int, period_seconds: int) -> int:
        total_minutes = max(1, period_seconds // 60)
        downtime_bps = min(10000, (downtime_minutes * 10000) // total_minutes)
        uptime_bps = 10000 - downtime_bps
        thresholds = json.loads(self.tier_thresholds_json)
        penalties = json.loads(self.tier_penalties_json)
        for i in range(len(thresholds)):
            if uptime_bps <= int(thresholds[i]):
                return int(penalties[i])
        return 0

    def _transfer_native(self, to: Address, amount: int) -> None:
        if amount <= 0:
            return
        recipient = gl.get_contract_at(to)
        recipient.emit_transfer(value=u256(amount))

    # ------------------------------------------------------------------
    # Public write methods
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def deposit_bond(self) -> None:
        if gl.message.sender_address != self.provider:
            raise Exception("Hanya provider yang boleh deposit")
        if self.bond_deposited:
            raise Exception("Bond sudah disetor")
        assert gl.message.value == self.bond_amount, "Deposit harus sama dengan bond_amount"

        self.bond_deposited = True
        self.remaining_bond = gl.message.value
        self.state = "ACTIVE"

    @gl.public.write
    def file_claim(self, evidence_urls_json: str) -> None:
        if gl.message.sender_address != self.client:
            raise Exception("Hanya client yang boleh mengajukan klaim")
        if self.state != "ACTIVE":
            raise Exception("Kontrak tidak dalam status ACTIVE")

        urls = json.loads(evidence_urls_json)
        self._validate_evidence_urls(urls)

        # Salin ke variabel lokal untuk nondet
        local_quorum = int(self.quorum_required)
        local_total_sources = len(urls)
        local_urls = [str(u) for u in urls]
        local_start = int(self.start)
        local_end = int(self.end)

        def check_incident() -> str:
            contents = []
            for u in local_urls:
                try:
                    page = gl.nondet.web.render(u, mode="text")
                except Exception:
                    page = "(gagal memuat konten web)"
                contents.append(page[:5000])

            sources_block = ""
            for idx, c in enumerate(contents):
                sources_block += f"=== SOURCE {idx+1} ({local_urls[idx]}) ===\n{c}\n\n"

            prompt = f"""
You are an impartial judge evaluating an SLA outage claim based strictly on the provided web evidence.

EVIDENCE SOURCES:
{sources_block}

CRITERIA:
1. Examine the evidence objectively. Is there clear documentation of an outage, server downtime, major degradation, or service disruption?
2. If at least {local_quorum} of the {local_total_sources} source(s) confirm an incident:
   - "consensus_reached": true
   - "incident_id": concise name of the incident
   - "start_time_unix": unix timestamp or best estimate
   - "end_time_unix": end timestamp or estimate (+3600s if ongoing)
   - "impact": "major" or "minor"
   - "sources_agreeing": count of sources confirming it
3. If no incident is reported, the page is blank, or evidence does NOT support downtime:
   - "consensus_reached": false
   - "incident_id": ""
   - "start_time_unix": 0
   - "end_time_unix": 0
   - "impact": "none"
   - "sources_agreeing": 0

Respond ONLY with valid JSON (no markdown):
{{"consensus_reached": <bool>, "incident_id": "<string>", "start_time_unix": <int>, "end_time_unix": <int>, "impact": "<major|minor|none>", "sources_agreeing": <int>}}
"""
            default_empty = json.dumps({
                "consensus_reached": False,
                "incident_id": "",
                "start_time_unix": 0,
                "end_time_unix": 0,
                "impact": "none",
                "sources_agreeing": 0,
            })

            try:
                res = gl.nondet.exec_prompt(prompt, response_format="json")
                if not res or not str(res).strip():
                    return default_empty
                cleaned = self._clean_json_response(str(res))
                json.loads(cleaned)
                return cleaned
            except Exception:
                return default_empty

        raw = gl.eq_principle.prompt_comparative(
            check_incident,
            "Validators must agree EXACTLY on every field that drives settlement: "
            "consensus_reached (true/false), incident_id (same string), "
            "start_time_unix and end_time_unix (same integers -- these decide "
            "the penalty tier, so approximate agreement is not acceptable), "
            "impact level, and sources_agreeing (same integer count -- this is "
            "checked against quorum_required before any payout is computed). "
            "Minor wording differences in any free-text explanation are fine; "
            "differences in any of the fields above are NOT equivalent results.",
        )
        parsed = json.loads(raw)

        # KEPUTUSAN JURI AI: Jika bukti tidak valid / tidak ada insiden
        if not parsed.get("consensus_reached", False):
            history = json.loads(self.claims_history_json)
            history.append({
                "status": "DISMISSED",
                "reason": "AI Oracle: Tidak ditemukan bukti downtime/insiden yang memenuhi syarat kuorum.",
                "evidence_provided": local_urls,
            })
            self.claims_history_json = json.dumps(history)
            # Transaksi tetap SUCCESS! Tidak di-revert, vonis penolakan tercatat sah.
            return

        # KEPUTUSAN JURI AI: Jika bukti VALID dan ada insiden
        incident_id = str(parsed.get("incident_id", "")).strip()
        incident_start = int(parsed.get("start_time_unix", 0))
        incident_end = int(parsed.get("end_time_unix", 0))
        impact = str(parsed.get("impact", "")).lower().strip()
        sources_agreeing = int(parsed.get("sources_agreeing", 0))
        current_time = int(gl.block.timestamp)

        if not incident_id:
            raise Exception("Incident ID tidak boleh kosong")

        # Cek apakah incident_id sudah pernah diajukan sebelumnya
        history = json.loads(self.claims_history_json)
        for c in history:
            if c.get("incident_id") == incident_id:
                raise Exception("Incident ID ini sudah pernah diproses sebelumnya")

        # Validasi ketat waktu insiden (timing bounds)
        if incident_start > incident_end:
            raise Exception("Waktu mulai insiden tidak boleh setelah waktu selesai")
        if incident_start < local_start:
            raise Exception("Waktu insiden sebelum periode SLA dimulai")
        if incident_end > local_end:
            raise Exception("Waktu insiden setelah periode SLA berakhir")
        if incident_start > current_time:
            raise Exception("Waktu insiden tidak boleh di masa depan")

        # Validasi ketat level impact
        if impact not in ("major", "minor"):
            raise Exception("Level impact tidak valid")

        # Validasi ketat kuorum sumber yang setuju
        if sources_agreeing < local_quorum or sources_agreeing > local_total_sources:
            history.append({
                "status": "DISMISSED",
                "incident_id": incident_id,
                "reason": f"Kuorum sumber tidak terpenuhi: {sources_agreeing}/{local_total_sources} setuju (dibutuhkan {local_quorum})",
                "evidence_provided": local_urls,
            })
            self.claims_history_json = json.dumps(history)
            return

        duration_minutes = max(1, (incident_end - incident_start) // 60)
        penalty_bps = self._compute_penalty_bps(duration_minutes, local_end - local_start)
        remaining_int = int(self.remaining_bond)
        payout = min(remaining_int, (remaining_int * penalty_bps) // 10000)

        claim_filed_at = current_time
        dispute_deadline = claim_filed_at + int(self.dispute_period_seconds)

        pending_data = {
            "incident_id": incident_id,
            "filed_at": claim_filed_at,
            "dispute_deadline": dispute_deadline,
            "incident_start": incident_start,
            "incident_end": incident_end,
            "impact": impact,
            "duration_minutes": duration_minutes,
            "penalty_bps": penalty_bps,
            "payout_amount": payout,
            "sources_agreeing": sources_agreeing,
            "disputed": False,
            "finalized": False,
        }
        self.pending_claim_json = json.dumps(pending_data)
        self.state = "CLAIM_PENDING"

    @gl.public.write
    def dispute_claim(self, evidence_urls_json: str) -> None:
        if gl.message.sender_address != self.provider:
            raise Exception("Hanya provider yang boleh mengajukan dispute")
        if self.state not in ("CLAIM_PENDING", "DISPUTE_WINDOW"):
            raise Exception("Tidak ada klaim aktif yang bisa didispute")

        urls = json.loads(evidence_urls_json)
        self._validate_evidence_urls(urls)

        claim = json.loads(self.pending_claim_json)
        if claim.get("disputed", False):
            raise Exception("Klaim sudah pernah didispute")

        # Penegakan batas waktu periode dispute
        current_time = int(gl.block.timestamp)
        filed_at = int(claim.get("filed_at", 0))
        dispute_deadline = filed_at + int(self.dispute_period_seconds)
        if current_time > dispute_deadline:
            raise Exception("Periode dispute telah berakhir")

        incident_id = str(claim.get("incident_id", ""))
        original_impact = str(claim.get("impact", ""))
        local_quorum = int(self.quorum_required)
        local_urls = [str(u) for u in urls]
        local_total_sources = len(urls)

        def re_check() -> str:
            contents = []
            successful_fetches = 0
            for u in local_urls:
                try:
                    page = gl.nondet.web.render(u, mode="text")
                    if page and page.strip() and page != "(gagal mengambil konten)":
                        successful_fetches += 1
                except Exception:
                    page = "(gagal mengambil konten)"
                contents.append(page[:5000])

            # Bukti tidak tersedia jika tidak memenuhi kuorum fetch berhasil
            if successful_fetches < local_quorum:
                return json.dumps({
                    "status": "INCONCLUSIVE",
                    "upheld": False,
                    "revised_impact": "none",
                    "sources_agreeing": 0,
                })

            sources_block = ""
            for idx, c in enumerate(contents):
                sources_block += f"=== SOURCE {idx+1} ({local_urls[idx]}) ===\n{c}\n\n"

            prompt = f"""
Review this SLA dispute impartially.
INCIDENT UNDER REVIEW: {incident_id}, ORIGINAL IMPACT: {original_impact}
Quorum required to uphold: at least {local_quorum} of {local_total_sources} sources.

COUNTER-EVIDENCE / STATUS PROOF:
{sources_block}

TASK:
Determine if the counter-evidence conclusively proves whether the incident was valid or invalid:
1. If sources clearly prove service was normal / false alarm / mitigated:
   - "status": "DISMISSED"
   - "upheld": false
   - "revised_impact": "none"
   - "sources_agreeing": count of confirming sources (>= {local_quorum})
2. If sources confirm the outage occurred and incident is valid:
   - "status": "UPHELD"
   - "upheld": true
   - "revised_impact": "major" or "minor"
   - "sources_agreeing": count of confirming sources (>= {local_quorum})
3. If evidence is ambiguous, incomplete, blank, unavailable, or insufficient:
   - "status": "INCONCLUSIVE"
   - "upheld": false
   - "revised_impact": "none"
   - "sources_agreeing": 0

Respond ONLY with valid JSON:
{{"status": "<UPHELD|DISMISSED|INCONCLUSIVE>", "upheld": <bool>, "revised_impact": "<major|minor|none>", "sources_agreeing": <int>}}
"""
            default_resp = json.dumps({
                "status": "INCONCLUSIVE",
                "upheld": False,
                "revised_impact": "none",
                "sources_agreeing": 0,
            })

            try:
                res = gl.nondet.exec_prompt(prompt, response_format="json")
                if not res or not str(res).strip():
                    return default_resp
                cleaned = self._clean_json_response(str(res))
                parsed_json = json.loads(cleaned)
                if not isinstance(parsed_json, dict):
                    return default_resp
                return cleaned
            except Exception:
                return default_resp

        raw = gl.eq_principle.prompt_comparative(
            re_check,
            "Validators must agree EXACTLY on whether status is UPHELD, DISMISSED, "
            "or INCONCLUSIVE, upheld (true/false), revised_impact, and sources_agreeing.",
        )
        parsed = json.loads(raw)

        status = str(parsed.get("status", "INCONCLUSIVE")).upper().strip()
        if status == "INCONCLUSIVE" or not parsed.get("status"):
            # Bukti tidak tersedia atau invalid -> inconclusive retry (tidak mengunci payout, revert agar bisa retry)
            raise Exception("Bukti dispute tidak tersedia atau tidak konklusif; silakan coba lagi")

        revised_impact = str(parsed.get("revised_impact", "none")).lower().strip()
        sources_agreeing = int(parsed.get("sources_agreeing", 0))

        if revised_impact not in ("major", "minor", "none"):
            raise Exception("Level revised_impact tidak valid")

        if sources_agreeing < 0 or sources_agreeing > local_total_sources:
            raise Exception("Jumlah sumber yang setuju tidak valid")

        if status == "DISMISSED":
            if sources_agreeing < local_quorum:
                raise Exception("Kuorum sumber dispute tidak terpenuhi")
            claim["disputed"] = True
            claim["impact"] = "none"
            claim["sources_agreeing"] = sources_agreeing
            claim["penalty_bps"] = 0
            claim["payout_amount"] = 0
        elif status == "UPHELD":
            if sources_agreeing < local_quorum:
                raise Exception("Kuorum sumber dispute tidak terpenuhi")
            claim["disputed"] = True
            claim["impact"] = revised_impact if revised_impact != "none" else original_impact
            claim["sources_agreeing"] = sources_agreeing

        self.pending_claim_json = json.dumps(claim)
        self.state = "DISPUTE_WINDOW"

    @gl.public.write
    def finalize_claim(self) -> None:
        if self.state not in ("CLAIM_PENDING", "DISPUTE_WINDOW"):
            raise Exception("Tidak ada klaim yang siap difinalisasi")

        claim = json.loads(self.pending_claim_json)
        current_time = int(gl.block.timestamp)
        filed_at = int(claim.get("filed_at", 0))
        dispute_deadline = filed_at + int(self.dispute_period_seconds)

        # Enforceable time-based dispute period:
        # Jika belum didispute, finalize_claim WAJIB menunggu hingga dispute deadline berakhir
        if not claim.get("disputed", False):
            if current_time < dispute_deadline:
                raise Exception("Periode dispute belum berakhir")

        payout = min(int(claim.get("payout_amount", 0)), int(self.remaining_bond))

        self.remaining_bond = u256(int(self.remaining_bond) - payout)
        claim["finalized"] = True

        history = json.loads(self.claims_history_json)
        history.append(claim)
        self.claims_history_json = json.dumps(history)

        self.pending_claim_json = "{}"
        self.state = "ACTIVE"

        if payout > 0:
            self._transfer_native(self.client, payout)

    @gl.public.write
    def withdraw_remaining_bond(self) -> None:
        if gl.message.sender_address != self.provider:
            raise Exception("Hanya provider yang boleh menarik sisa bond")
        if self.state in ("CLAIM_PENDING", "DISPUTE_WINDOW"):
            raise Exception("Tidak bisa withdraw saat klaim pending")

        current_ts = u256(gl.block.timestamp)
        if current_ts < self.end:
            raise Exception("Periode SLA belum berakhir")

        amount = int(self.remaining_bond)
        self.remaining_bond = u256(0)
        self.state = "CLOSED"

        if amount > 0:
            self._transfer_native(self.provider, amount)

    # ------------------------------------------------------------------
    # Public views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_config(self) -> str:
        return json.dumps({
            "bond_amount": int(self.bond_amount),
            "start": int(self.start),
            "end": int(self.end),
            "dispute_period_seconds": int(self.dispute_period_seconds),
            "evidence_domains": json.loads(self.evidence_domains_json),
            "tier_uptime_thresholds_bps": json.loads(self.tier_thresholds_json),
            "tier_penalty_bps": json.loads(self.tier_penalties_json),
        })

    @gl.public.view
    def get_state(self) -> str:
        return json.dumps({
            "state": self.state,
            "provider": str(self.provider),
            "client": str(self.client),
            "remaining_bond": int(self.remaining_bond),
            "quorum_required": int(self.quorum_required),
            "pending_claim": json.loads(self.pending_claim_json),
            "history": json.loads(self.claims_history_json),
        })