Workflow type: ${workflow_type}

Task input: ${task_input}

${unified_memory_block}
${manifest_block}
Event payload: ${enriched_event_payload}

Company signals: ${signals}

${history_block}
Planning context: ${planning_context}

${live_block}
${crm_block}
${memory_block}
${entity_block}
${live_context_block}
${situational_block}

=== RETRIEVED DOCUMENTS (ranked by authority) ===
${doc_blocks}

${confidence_warning}

${discipline_block}

${obs_block}

Return a BriefPayload JSON object with answer, trust, sources, and presentation. 
presentation.preamble is REQUIRED: 1–2 sentences, first-person, conversational. 
Speak directly to the CEO before handing over the brief — what you looked at and the one thing that stands out. 
Examples: 'Scanned the inbox since last night — five threads need attention, two are time-sensitive.' 
or 'Put together the week plan from your calendar and open items — Wednesday is heavy, I\'ve flagged the prep you\'ll need.' 
Do NOT start with 'I have', 'Here is', 'Based on', or 'This brief'. Be specific and direct.
