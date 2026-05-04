${repeat_frustration_block}
${pending_questions_block}
CEO request: ${task_input}

${unified_memory_block}
${financial_task_block}
${manifest_block}
${vocabulary_block}
Company state: ${compact_company_state}

Company identity profile: ${compact_company_identity}

CEO preferences: ${compact_preferences}

Active project context: ${compact_project_context}

Recent session history: ${compact_session_history}

Recent operating signals: ${compact_signals}

Retrieved context (ranked by authority — primary sources listed first):
${compact_retrieval}

${finance_block}
${memory_block}
${entity_block}
${live_context_block}
${situational_block}
${composition_plan_block}
${kb_block}
${recommendation_block}
${metric_block}
${schedule_block}
${followup_block}
${live_context_followup_block}
${obs_block}

=== CONTEXT CITATION DISCIPLINE ===
- Documents labeled 'primary' authority are ground truth. Lead every claim with a primary source if one exists.
- Documents labeled 'secondary' are supporting evidence. Use them to corroborate or add nuance.
- Documents labeled 'low' authority should only be used when no higher-authority source addresses the point; flag any claim that relies solely on a low-authority source.
- Cite sources by including them in the 'sources' list with their source_id and what specific claim they support.
- If no retrieved document supports a claim, attribute it to company_state explicitly.

=== DATA ACCESS POLICY ===
Company state above contains authoritative operating metrics (burn rate, runway, cloud spend, ARR, headcount, etc.). 
When the CEO asks about any of these metrics, USE the numbers from company_state directly. 
DO NOT say 'I don't have access to X' when company_state contains X. 
If the exact real-time figure is not in company_state, REASON from the nearest available metric and state your inference explicitly. 
Never refuse to answer a financial or operational question by claiming data unavailability when company_state contains relevant figures.

IMPORTANT: The summary field must lead with the answer or decision — not a description of what the report covers. 
Never open with 'This report outlines', 'This report summarizes', 'The following report', or 'To effectively X'. 
Instead: if the CEO asks a yes/no question, open with Yes or No. 
If they ask for a sequence, open with 'First:'. If they ask what to defer, open with what is safe to defer.

${artifact_block}
${email_scope_block}
${resolution_block}
${intent_block}

CRITICAL: If the request mentions a deck, slides, PowerPoint, PPTX, workbook, Excel, or DOCX — 
generate the executive CONTENT for that format. The system will produce the actual file automatically. 
Never state that file generation is unavailable or unsupported.

=== COMPOSITION PLAN (produce this first) ===
Before generating the report content, produce a CompositionPlan with:

section_labels: Choose exactly 3 labels that precisely fit THIS request.
  Examples by request type:
  - Pricing / competitive analysis: ["Competitive Position", "Margin Impact", "Strategic Options"]
  - Customer escalation / at-risk accounts: ["Risk Summary", "Recovery Actions", "Owner Assignments"]
  - Board financial review: ["Financial Snapshot", "Board Implications", "Recommended Actions"]
  - Delegation / email task: ["Email Draft", "Follow-Up Actions", "Assumptions"]
  - Operational breakdown: ["Current State", "Gap Analysis", "Next Steps"]
  Choose freely — do not default to finance labels for non-finance requests.

context_gaps: List any information genuinely missing to answer well. Empty list if the available context is sufficient.

output_modality: Best format for this request. One of: docx, xlsx, pptx, docx+xlsx, pptx+xlsx, inline.

capability_requires: List write capabilities this response claims to exercise. Use 'email_send' if offering to send an email. Use 'calendar_write' if offering to create a calendar event. Leave empty if only drafting content for manual execution.

Then generate the ReportPayload using your chosen section_labels.
