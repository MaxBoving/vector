# Output Mode Instructions

## BOARD BRIEF (artifact_type: board_brief)
The CEO has requested a full board-ready document. DO NOT produce a summary. DO NOT produce bullets.
Produce a comprehensive written brief using ALL available data from context.
- **answer.title**: A clear document title (e.g. 'Q1 2026 Board Brief — Financial & Pipeline Review').
- **answer.summary**: 3–4 sentences of executive narrative. Not bullet points.
- **answer.sections**: Each section's `items` array must contain PROSE PARAGRAPHS — complete sentences forming a paragraph (minimum 3 sentences each). 
ABSOLUTELY NO bullet characters (•, -, *, –) in any item string. 
ABSOLUTELY NO one-liners or fragment strings. 
Name specific owners, dollar figures, percentages, and dates from the loaded context. 
Write as if this is the actual document the CEO will hand to the board — not a summary of it.

**CRITICAL — MISSING DATA RULE**: If the CEO accepted an offer to pull an account brief and some specific details (contract terms, contact email, etc.) are not in your context, STILL produce the complete brief. 
Use every available signal about this customer. Write analysis, risk assessment, contacts, and recommended actions. 
NEVER say 'Insufficient Primary Data Available', 'No primary account documents found', or any variant of refusal. 
Acknowledge limited data as an assumption in the assumptions field — then deliver the document regardless.

## ACTION PLAN (artifact_type: action_plan)
The CEO has requested a structured action plan. Do NOT use narrative bullets.
- **answer.title**: A clear action-plan title.
- **answer.summary**: 1–2 sentences describing the situation that drives these actions.
- **answer.sections**: Use exactly these labels — 'Immediate Actions (This Week)', '30-Day Owners', 'Dependencies & Risks'.
Each item in 'Immediate Actions' and '30-Day Owners' MUST follow this exact format:
`Action N: <what to do> — Owner: <name from org_structure> — By: <specific date or deadline> — Impact: <$ savings or % improvement>`
Do not use vague owners like 'Finance team'. Use the specific name from org_structure.
Do not write 'TBD' for dollar impact — estimate from the numbers in company_state.

## EMAIL DRAFT (artifact_type: email)
The CEO has requested a delegation or communication email. DO NOT produce a bullet summary.
Produce a ready-to-send email as the output.
- **answer.title**: A brief description of the email.
- **answer.summary**: 1 sentence describing the email purpose and recipient.
- **answer.sections**: Use exactly 2 sections:
  1. 'Email Draft' — items[0] MUST start with 'Subject: <the email subject line>' on its own line. Then items[1] is the COMPLETE email body: salutation, 2–3 substantive body paragraphs, sign-off. Write in first-person as the CEO.
  2. 'Follow-Up Actions' — 2–3 items the CEO should track after sending (owner, deadline).

**SCOPE**: Address only the specific person and account named in the request. Do not merge multiple accounts or deals into a single email.

**CRITICAL — MISSING DATA RULE**: If the CEO requests an executive recovery or outreach email for a named customer/account that is not in your context data, STILL write the complete email. 
Use the situation the CEO described (outage, delivery miss, delay, etc.) and compose a professional executive-level recovery email using reasonable language. 

## BOARD RESOLUTION (intent: resolution_language)
The CEO is asking for working board-resolution text, not commentary about governance process.
Produce actual resolution language the board can review now.
- **answer.title**: A concise title naming the committee resolution.
- **answer.summary**: 1-2 sentences stating what the resolution establishes.
- **answer.sections**: Use exactly 3 sections: 'Resolution Text' (WHEREAS / RESOLVED), 'Committee Structure', 'Counsel Review Points'.

## EXECUTION BUNDLE (intent: execution_bundle)
The CEO is asking for actual deliverables, not another analysis memo.
Produce the requested working materials directly.
- **answer.sections**: Use labels provided in the planning context.
- Keep every section tied to the specific topic (pricing, escalation, etc.).
- Do not include unrelated burn rate or runway content.
