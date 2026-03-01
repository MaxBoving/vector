# Architectural Detail: agenticMIND

This document defines the two primary logic cycles of **agenticMIND**: the **Inquiry Synthesis Flow** (Inbound Tasks) and the **Preference Learning Loop** (Continuous Improvement).

## 1. Inquiry Synthesis Flow

This flow ensures that every CEO request is framed by the current company state and the CEO's personal preferences before any external models are engaged.

```mermaid
sequenceDiagram
    participant CEO
    participant Brain as Executive Brain
    participant State as State Engine
    participant Pref as Preference Model
    participant Route as Routing Logic
    participant Specialist as Specialist Worker (Claude/GPT)

    CEO->>Brain: Strategic Inquiry ("Assess expansion into SE Asia")
    Brain->>State: Fetch Capital Position & Revenue Segmentation
    Brain->>Pref: Fetch Risk Tolerance (Geographic) & Tone
    Brain->>Route: Decide Specialist (e.g., Gemini for Research)
    Route->>Specialist: Exec Task (Bounded)
    Specialist-->>Route: Raw Synthesis
    Route->>Brain: Normalized Return
    Brain->>Brain: Refine Output (Apply Tone/Style)
    Brain->>CEO: Executive Synthesis & Proposed Next Steps
```

## 2. Preference Learning Loop

The system avoids neural weight retraining, instead using a structural adaptation loop to update the `CEOPreferenceModel`.

```mermaid
graph LR
    Log[Interaction Log] --> FB[Feedback Capture: Edit/Approve/Reject]
    FB --> Calc[Calculate Edit Distance & Approval Rate]
    Calc --> Update[Update CEO Preference Vector]
    Update --> Next[Next Brain Synthesis]
    
    subgraph "Adaptation Framework"
        Update
        Next
    end
```

### Preference Metrics
- **Tone Alignment:** Track preferred vs. actual brevity.
- **Risk Convergence:** Update risk tolerance for specific domains based on rejection patterns.
- **Approval Rate:** Metric for system trust and routing efficiency.

## 3. Decision Primitives (Company State)
Rather than raw data, the **State Engine** maintains a high-level summary of:
- **Financials:** Revenue by segment, cost structure, and capital runway.
- **Operations:** Strategic initiatives, org structure, and regulatory footprint.
- **Velocity:** Historical decision-making speed for performance baselining.

## 4. Latency Mitigation
- **Parallelized Worker Calls:** Specialized agents are called simultaneously when dependencies permit.
- **Context Compression:** Only relevant primitives from the `CompanyState` are injected into prompts.
- **Caching:** Semantic caching for repetitive strategic queries.
