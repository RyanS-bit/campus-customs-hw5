"""Proposed shop policies (Problem 5).

Every value here is a PROPOSED SHOP POLICY approved by Dr. Ryan on 2026-10-09
(output/p5_mcp_tools_spec.md section 7). NONE of them is an assignment requirement.
Assignment rules R1-R8 are enforced in ops.py and cite their rule numbers.
"""

# Payments (D16b): a payment proposal must equal the stored invoice amount or monthly rent.
PAYMENT_FULL_AMOUNT_ONLY = True

# Quotes (D9, D16c): floor at stored unit cost, ceiling at stored list price,
# and the quote must match the ticket's SKU, size and quantity exactly.
QUOTE_FLOOR = "pricing.unit_cost"
QUOTE_CEILING = "pricing.list_price"
QUOTE_MUST_MATCH_TICKET = True

# Drafts (D16d): recipient comes from a stored record, never from the agent.
DRAFT_RECIPIENT_SOURCES = ("leases.landlord", "tickets.requester")

# Drafting authority (Dr. Ryan's organizational decision, Problem 5 follow-up 9): each drafting agent
# may only address the counterparty it is responsible for. A lease-linked ticket is a facilities
# matter (recipient: leases.landlord); any other ticket is customer-facing (recipient: tickets.requester).
# Requests outside an agent's responsibility must be delegated to the responsible specialist.
DRAFT_AUTHORITY = {"customer_service": "tickets.requester", "facilities": "leases.landlord"}

# Drafts (D16d): words that suggest a commitment. Matches produce WARNINGS for the human
# reviewer, never refusals; a linked proposal annotates a warning but never clears it.
COMMITMENT_PHRASES = (
    "confirmed", "confirm", "approved", "guaranteed", "guarantee", "will ship", "has shipped", "shipped",
    "in stock now", "paid", "deal", "accepted", "promise", "delivered", "will arrive", "will be ready",
)

# Engineering limits (not business policy).
PURCHASE_MAX_QTY = 500
RATIONALE_LENGTH = (10, 1000)
SUBJECT_LENGTH = (1, 200)
BODY_LENGTH = (20, 4000)

# Used from Problem 7 (human approval/execution), recorded here so they stay labelled as policy:
INVOICE_PAID_STATUS = "paid"                  # D6
LEASE_ADVANCE_MONTHS = 1                      # D6: next_due moves forward one month
PAYMENT_KINDS = ("invoice", "lease")          # D6: payments.kind values
RESERVE_APPROVED_COMMITMENTS = True           # D4: stricter than R1
