# Specifications

Specs 13–33 state the rules. Spec 34 states the rebuild order. Spec 35 describes the proof of concept as built, and it is the baseline for MVP specifications. Read the spec before you change the behavior it names.

| Spec | Subject |
|---|---|
| [13](13_Annual_Risks.md), [14](14_Risk_Definitions.md) | Annual risks and risk definitions. A taxonomy tag is not a multiplicand. |
| [15](15_Severity_Definition.md) | Severity. Maximum across dimensions. Financial uses value-at-risk only. |
| [16](16_Likelihood_Definition.md) | Likelihood. Annual assessment only, not signal scoring. |
| [17](17_Supervisor_Agent_Specification.md) | The Supervisor and the agent roles. |
| [18](18_GRIT_WorkflowState_Table_Design.md), [19](19_ReviewGate_Function_Specification.md), [20](20_TransitionGuard_Function_Specification.md) | The append-only workflow table, `ReviewGate()`, and `TransitionGuard()`. |
| [21](21_Strategic_Horizon_Architecture_Specification.md)–[23](23_Strategic_Horizon_Agent_Specification.md), [26](26_Theme_Lifecycle_and_Escalation_Specification.md), [31](31_Standing_Risk_Theme_Model.md) | Strategic horizon, themes, events, lifecycle, and standing risks. |
| [24](24_Reader_Profile_and_Aperture_Routing_Specification.md), [25](25_Layered_Reporting_Pack_Specification.md) | Reader profiles, aperture routing, and the layered reporting pack. |
| [27](27_GRIT_Dataverse_ERD_and_Phase_Plan.md) | The Dataverse model and phase plan. |
| [28](28_MVP_Build_Plan_for_Wilson_Demo.md) | The Wilson demo plan. |
| [29](29_News_Priority_and_Management_Request_Model.md), [30](30_Source_Taxonomy_and_Credibility_Model.md) | News priority, management requests, and source credibility. |
| [32](32_GRIT_Cascade_Design_Global_to_Entity.md), [33](33_URS_TW_Global_Conditions_Scanner_Agent_Specification.md) | The cascade from global conditions to an entity, and the Taiwan condition scanner. |
| [34](34_GRIT_Scan_Rebuild_Specification.md) | The rebuild order and invariants. Section 5 is a gap list; spec 35 records what later work closed. |
| [35](35_GRIT_Scan_POC_Architecture.md) | The proof of concept as built: six roles, the signal pool, and the interfaces. |
| [36](36_Key_Risks_Implications_and_Proposer.md) | Key risk focus, the implication for the business area, and the Proposer's response options. |
| [37](37_STE_Group_Pack.md) | The STE Group pack: 12 standing conditions, the footprint rung, baseline drafting, scenarios, the monthly radar, and the Currents fallback. |
| [38](38_Report_Library_and_State_of_the_Situation.md) | The thought-leadership report library, and the State of the Situation documents that ground forecasts and options. |
| [39](39_Reader_Notes_and_Profiles.md) | Reader notes, the `profile.md` distilled from them, emphasis, and the AIRIS rule by package. |
| [40](40_Copilot_Core.md) | The Co-Pilot: scope, hybrid search, tools, the checked answer, privacy, and the dashboard drawer. |
| [41](41_Profiler_Fit_Report_and_Web_Search.md) | The Profiler's checked profile proposals, the fit report, the Co-Pilot's web search and budget, and Add to pool. |
| [42](42_Daily_Lane_and_Nightly_Index.md) | The daily feed register and lane, events, daily digests, retention, and the Co-Pilot's `whats_new`. |

`archive/` holds an early draft of spec 17 for reference. It does not govern.
