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
- Save personal preferences and context (non-sensitive only)
- List stored memories
- Delete memories on request
- Stored in SQLite (persistent)

### Reminders and Scheduling
- [Future] Cron-based scheduled tasks
- [Future] Proactive reminders via Telegram/webhook
- [Future] Daily briefs (opt-in)

### General Queries
- Answer non-clinical questions
- Provide information and explanations
- Help with research (non-clinical)
- Conversational support

## Workflow

1. **Identify intent**: Is this a task, calendar, memory, or general query?
2. **Execute**: Call the appropriate tool/service
3. **Confirm**: Report back what was done
4. **Store**: Save to memory if appropriate (non-sensitive preferences only)

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

- [ ] Cron-based scheduled jobs (daily briefs, weekly reviews)
- [ ] Approval queue for external actions (emails, posts, payments)
- [ ] Pattern detection and proactive suggestions
- [ ] Integration with more services (email, notes, etc.)
