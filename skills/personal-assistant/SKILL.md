---
name: personal-assistant
description: Personal task management, scheduling, reminders, and general queries
activation: default
tags: [personal, tasks, calendar, reminders]
---

# Personal Assistant Skill

## Purpose
Help with personal productivity, task management, scheduling, reminders, and general 
non-clinical queries. This is the **default mode** for non-clinical interactions.

## When to Activate
- Task creation, listing, completion
- Calendar event queries or creation
- Reminders and scheduling
- Personal notes and memory management
- General questions (non-clinical)
- Conversational queries

## When NOT to Activate
- Clinical evidence questions → use clinical-evidence skill
- Queries containing patient data → ask user to de-identify
- Anything requiring live medical literature search → use clinical-evidence skill

## Capabilities

### Task Management
- Create tasks with optional due dates
- List tasks (all, pending, completed)
- Mark tasks complete
- Stored in SQLite (persistent)

### Calendar Integration
- List upcoming Google Calendar events (if configured)
- Create new calendar events
- Falls back to local SQLite storage if Google credentials not configured

### Memory Management
- Save personal preferences and context only when the user explicitly asks
- List stored memories and delete a memory on request
- For inferred long-term lessons, create a pending proposal; the user must approve it
- Never store PHI, credentials, or sensitive traits
- Stored in SQLite (persistent)

### Thinking Mode
- `/think on` enables respectful Socratic challenge for strategies, decisions, and designs
- Surface assumptions, tradeoffs, a counterargument, failure modes, and practical alternatives
- Keep routine actions direct; do not reveal hidden chain-of-thought
- `/think off` restores the concise default style

### Reflection Schedule
- Scheduled Telegram reviews are off until the user opts in with `/brief on`
- Daily morning/evening briefs, Thursday weekly calibration, and month-end audit use the user's timezone
- `/brief off` pauses all scheduled messages; daily times and timezone can be changed with `/brief`
- Ask before storing anything learned during a reflection; use an approval proposal rather than automatic memory writes
- Scheduled content must not include patient identifiers; flagged task/calendar items are omitted

### Reminders and Scheduling
- Daily briefs and weekly/monthly reviews are managed by the opt-in scheduler
- Default times: 06:00, 21:00, Thursday 20:00, and month-end 20:00
- User may change daily brief times and timezone via `/brief`; `/brief off` pauses deliveries
- Scheduler delivery is deduplicated in SQLite and limited to authorized Telegram users

### General Queries
- Answer non-clinical questions
- Provide information and explanations
- Help with research (non-clinical)
- Conversational support

## Workflow

1. **Identify intent**: Is this a task, calendar, memory, or general query?
2. **Execute**: Call the appropriate tool/service
3. **Confirm**: Report back what was done
4. **Store**: Save only on explicit request; otherwise propose stable lessons and wait for approval

## Guardrails

- **NEVER** store patient information in memory
- **NEVER** store credentials or API keys in memory
- **NEVER** infer or store sensitive personal traits without explicit user approval
- **ALWAYS** support user review, correction, and deletion of stored data
- Keep personal context separate from clinical context
- If a query seems clinical, suggest the clinical-evidence skill

## Integration Points

- **OpenCode**: AI engine for natural language understanding
- **SQLite**: Persistent storage for tasks, memories, conversations
- **Google Calendar/Sheets**: External calendar and spreadsheet integration (if configured)
- **Telegram**: Chat interface (if configured)
- **Web dashboard**: Browser UI at `/`

## Future Enhancements

- [ ] Approval queue for external actions (emails, posts, payments)
- [ ] Pattern detection and proactive suggestions from explicitly approved data
- [ ] Integration with more services (email, notes, etc.)
- [ ] WhatsApp adapter (not currently implemented)
