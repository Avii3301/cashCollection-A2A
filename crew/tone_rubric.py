"""
tone_rubric.py — Tone scoring rubric for collection emails.

Imported by:
  - crew/email_crew.py  → injected into the Tone Analyzer task description
  - evaluation/scorers.py → reference for tone_consistency_scorer

Scale: 0 (firm/strict) to 5 (professional/polite)
"""

TONE_RUBRIC = """
Tone Score Rubric for Collection Emails (0-5 scale):

0 — FIRM / STRICT
  Use for: clients with multiple defaults in the past, severely overdue accounts.
  Language: Direct, no pleasantries. States consequences (legal action, late fees,
  service suspension). Demands payment within 48 hours.
  Keywords to use: "demand", "immediate action required", "legal proceedings",
  "final notice", "overdue", "consequences".

1 — ASSERTIVE
  Use for: repeat late payers, 2nd or subsequent reminder.
  Language: Firm tone, explicitly notes that prior reminders were sent and
  have not been acted upon. Requests immediate payment with a short deadline
  (e.g. 5 business days).
  Keywords to use: "previous reminder", "still outstanding", "urgent attention",
  "immediate payment required", "escalate".

2 — DIRECT
  Use for: overdue accounts with no prior default history.
  Language: Clear and factual. No emotional language or pleasantries. States
  the outstanding amount, invoice number, and due date plainly. Gives a
  specific payment deadline.
  Keywords to use: "outstanding balance", "payment due", "please remit",
  "kindly settle", "by [date]".

3 — NEUTRAL / BALANCED
  Use for: first payment reminder on a standard overdue invoice.
  Language: Professional and matter-of-fact. Polite but not warm. Acknowledges
  the invoice is overdue without strong language.
  Keywords to use: "we would like to bring to your attention", "payment reminder",
  "please arrange payment", "contact us if you have any questions".

4 — PROFESSIONAL / COURTEOUS
  Use for: new clients, first invoice, early or pre-due reminder.
  Language: Warm-professional. Acknowledges the business relationship. Assumes
  good faith. Offers payment options or contact details for queries.
  Keywords to use: "we appreciate your business", "please find attached",
  "should you have any questions", "we look forward to your prompt payment".

5 — PROFESSIONAL / POLITE
  Use for: high value clients, long-term clients, minor or first-time delay.
  Language: Warm opening, sincere appreciation of the relationship, very polite
  request, multiple payment methods offered, sign-off expresses full confidence
  in resolution.
  Keywords to use: "valued partner", "greatly appreciate", "at your earliest
  convenience", "thank you for your continued partnership", "please do not
  hesitate to reach out".

Decision guide based on relationship_info:
  "client with multiple defaults in the past" → 0
  "repeat late payer"                         → 1
  "overdue, no prior defaults"                → 2
  "standard client"                           → 3
  "new client"                                → 4
  "high value client"                         → 5
  "long-term client"                          → 5
"""
