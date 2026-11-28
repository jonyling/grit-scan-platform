# Documentation map

The repository [README](../README.md) is the operator guide. This folder holds the guides, design notes, source files, and progress records around that guide.

## Guides

- [Add a business area](add-a-business-area.md) adds a reader pack as data.
- [Set up the daily signal review](daily-review-setup.md) connects the daily review to SharePoint and mail. The `review/` folder holds the column formatting for the Approve and Reject buttons.
- [Prepare the weekly exposure snapshot](templates/exposure-snapshot-prompt.md) is the prompt for the internal evidence file. [exposure-snapshot.example.json](templates/exposure-snapshot.example.json) shows the shape.
- [Event relations and topic collapse](topic-collapse-operator-note.md) explains the `--event-relations` / `--topic-collapse` flags and how to read topic parent, child, unassigned, and emerging-theme rows.

## Design notes

The `design/` folder holds working notes for acquisition lanes, the ontology bind, reader notes, and the Co-Pilot. `design/schema.yaml` and `design/patterns.yaml` sit with those notes.

## Reference files

The `reference/` folder holds the description, the executive-summary deck, and the 2 October 2026 URS Taiwan brief.

These source files stay at the folder root because specs and progress notes cite the paths:

- `Adverse_News_Lessons_Learnt_Scan_Prompt.docx` is the prompt for the adverse news scan. [Spec 46](../specs/46_Adverse_News_and_Lessons_Learnt_Scan.md) cites it.
- `GRIT-Scan_Platform_Methodology.docx` and `M365_Copilot_GRIT-Scan_URS_Methodology.docx` are the methodology sources.
- `GRIT_Scan_URS_BA-wide_22-29Oct2026_v2.docx` is a sample business-area brief.
- `GRIT-Scan_Wilson_Discussion_2026-08-24.pptx` is the August discussion deck.

## Progress records

The `progress/` folder holds dated plans and handover notes. They record decisions at the time they were written. The README and the specs describe current behavior. [Personnel grounding and report outputs (17 November 2026)](progress/Personnel-Grounding-and-Report-Outputs_Plan_2026-11-17.md) is the open plan to ground all lanes on the STE staff log, ship adverse as Word and CSV, standardize Word table headers, and add an Adverse column on SharePoint.
