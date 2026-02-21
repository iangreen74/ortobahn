"""Legal Agent - generates legal documents, identifies compliance gaps."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response
from ortobahn.models import LegalReport

logger = logging.getLogger("ortobahn.legal")


class LegalAgent(BaseAgent):
    name = "legal"
    prompt_file = "legal.txt"
    thinking_budget = 10_000

    def run(self, run_id: str, **kwargs) -> LegalReport:
        client = kwargs.get("client")
        client_id = client.id if client else "default"

        # Check what legal documents already exist
        existing_docs = self.db.get_legal_documents(client_id=client_id)
        existing_types = {d["document_type"] for d in existing_docs}

        # Determine what needs to be generated
        needed_docs = []
        for doc_type in ("terms_of_service", "privacy_policy"):
            if doc_type not in existing_types:
                needed_docs.append(doc_type)

        # Build context for LLM
        parts = [f"Current date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"]

        if client:
            parts.append("\n## Client Context")
            parts.append(f"Company: {client.name}")
            parts.append(f"Industry: {client.industry}")
            parts.append(f"Website: {client.website}")

        # Check connected platforms
        connected = []
        for platform in ("bluesky", "twitter", "linkedin"):
            row = self.db.fetchone(
                "SELECT id FROM platform_credentials WHERE client_id=? AND platform=?",
                (client_id, platform),
            )
            if row:
                connected.append(platform)
        parts.append(f"\n## Connected Platforms: {', '.join(connected) if connected else 'None yet'}")

        # Report existing documents
        if existing_docs:
            parts.append("\n## Existing Legal Documents")
            for doc in existing_docs:
                parts.append(f"- {doc['document_type']} (v{doc['version']}, updated {doc['updated_at']})")
        else:
            parts.append("\n## Existing Legal Documents: NONE")

        # Request generation of missing documents
        if needed_docs:
            parts.append(f"\n## REQUIRED: Generate the following documents: {', '.join(needed_docs)}")
            parts.append("Generate complete, enforceable documents for Ortobahn by Vaultscaler Inc.")
        else:
            parts.append("\n## All core documents exist. Review for compliance gaps and update recommendations.")

        # Check client count for data handling scope
        total_clients = len(self.db.get_all_clients())
        parts.append(f"\n## Platform Scale: {total_clients} registered clients")

        user_message = "\n".join(parts)
        response = self.call_llm(user_message)

        try:
            report = parse_json_response(response.text, LegalReport)
        except Exception:
            report = LegalReport(summary=response.text[:500])

        # Save generated documents to DB
        for doc in report.documents_generated:
            self.db.save_legal_document(
                {
                    "client_id": client_id,
                    "document_type": doc.document_type,
                    "title": doc.title,
                    "content": doc.content,
                    "version": doc.version,
                    "effective_date": doc.effective_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "created_by": "legal_agent",
                }
            )

        self.log_decision(
            run_id=run_id,
            input_summary=f"Existing docs: {len(existing_docs)}, Needed: {len(needed_docs)}, Client: {client_id}",
            output_summary=f"Generated {len(report.documents_generated)} docs, {len(report.compliance_gaps)} gaps",
            reasoning=report.summary[:200],
            llm_response=response,
        )
        return report
