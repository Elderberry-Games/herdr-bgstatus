# Feature Specification: Background-work spinner for the Herdr sidebar

**Feature Branch**: `001-bgstatus-spinner`

**Created**: 2026-11-17

**Status**: Implemented (local), review pending

**Input**: User description: "Herdr plugin that shows a fixed-width spinner + count to the left of each agent's state icon in the sidebar when the agent (Claude Code, Codex, OpenCode) still has background shells, terminals, tasks, or sub-agents. No inference. Strict alignment: every agent row reserves the same 2-cell slot (blank placeholder when idle), so text never shifts left or right when spinners appear or disappear. Agent row shows breakdown. TTL-based self-clean. Open source (MIT), unit tested."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See that an idle agent still works in the background (Priority: P1)

The operator looks at the Herdr sidebar. An agent shows "done" or "idle", but it still has background shells or sub-agents. The operator must see this at once, without opening the tab.

**Why this priority**: This is the problem. Today "done" hides running work, and the operator closes or forgets tabs that are not finished.

**Independent Test**: Start a background shell in an agent. Make the agent idle. Look at the sidebar.

**Acceptance Scenarios**:

1. **Given** an idle agent with 2 background shells, **When** the operator looks at the sidebar, **Then** the row reads `2 ⠹ · ✓ 🍋 citrus  42+77`, the icon keeps its color, and the spinner moves.
2. **Given** that agent, **When** the background shells stop, **Then** the spinner goes away within 3 seconds.
3. **Given** an agent with no background work, **When** the operator looks at the sidebar, **Then** no spinner shows for it.

---

### User Story 2 - Rows stay aligned (Priority: P1)

The operator scans the sidebar as a list. All icons and names stay in the same columns. When a spinner shows or goes away, no text moves left or right.

**Why this priority**: The operator asked for this specifically. A list that moves is hard to read on a phone over ssh.

**Independent Test**: Show 2 agents, one with work and one without. Start and stop work several times. Compare the column of each state icon in each frame.

**Acceptance Scenarios**:

1. **Given** a mix of agents with and without work, **When** the sidebar draws, **Then** every state icon is in the same column.
2. **Given** an agent, **When** its spinner shows, turns, or goes away, **Then** its icon and name do not move.
3. **Given** a count that goes from 9 to 10 or more, **When** the sidebar draws, **Then** the slot keeps the same width.

---

### User Story 3 - See what runs (Priority: P2)

The operator wants to know what kind of work runs, not only that work runs.

**Why this priority**: It helps the operator decide if they must act. It is not necessary for the main signal.

**Independent Test**: Start 2 shells and 1 sub-agent. Read the second row of that agent.

**Acceptance Scenarios**:

1. **Given** 2 shells and 1 sub-agent, **When** the operator reads the agent row, **Then** it shows "claude · 2 shells · 1 agent".
2. **Given** the work stops, **When** the sidebar draws, **Then** the agent row shows only the agent name again.

---

### User Story 4 - Install, stop, and remove it safely (Priority: P2)

Any Herdr user installs the plugin with one command and one config line. They can stop it at any time. If it crashes, nothing stays on the screen.

**Why this priority**: The plugin is open source for all Herdr users.

**Independent Test**: Install, stop, kill the process, and uninstall. Look at the sidebar after each step.

**Acceptance Scenarios**:

1. **Given** a fresh Herdr, **When** the user installs the plugin and adds the sidebar token, **Then** the spinner works after the next server start or one restart action.
2. **Given** the plugin runs, **When** the user stops it, **Then** all slots go away in the same frame.
3. **Given** the plugin process crashes, **When** 15 seconds pass, **Then** no spinner or placeholder stays.
4. **Given** the plugin runs, **When** the start action runs again, **Then** a second copy does not start.

### Edge Cases

- A pane closes between the list and the read: skip it, do not crash.
- A pane cannot be read many times: remove its spinner, do not show old data.
- The Herdr server stops: the plugin stops.
- Two Herdr servers on one machine: each gets its own copy of the plugin.
- A stale process ID after a reboot: the plugin must not stop a different process.
- Agent hint text such as "← 5 agents" or "← for agents": this is not work.
- Text such as "open 2 shells" in the input box: counts as work (known limit, in the README).
- A new agent appears: it gets its slot within one scan (3 seconds or less).
- Count above 9: show "+".
- Agent is "working" or "blocked": the spinner shows too, if there is background work.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The plugin MUST detect background shells, terminals, tasks, and sub-agents from the screen text of Claude Code, Codex, and OpenCode panes.
- **FR-002**: The plugin MUST NOT use inference, network calls, or packages outside the language standard library.
- **FR-003**: The first row of each agent MUST start with a slot `<count> <spinner>` in front of the Herdr state icon. The Herdr icon and labels MUST keep their colors.
- **FR-004**: When work runs, the count MUST be 1-9, or "+" above 9, so it is always 1 cell wide.
- **FR-005**: When no work runs, count and spinner MUST be blank cells of the same width, so icons, workspace, and tab names stay in the same columns.
- **FR-006**: The spinner MUST change frame about once per second.
- **FR-007**: The second agent row MUST show a breakdown of the work by kind, in a fixed order: shells, terminals, agents, tasks.
- **FR-008**: All displayed values MUST expire by themselves. Spinner and placeholder MUST use the same expiry time, so that after a crash all slots go away at about the same time.
- **FR-009**: The stop action MUST remove all slots from all agent rows at once.
- **FR-010**: Only one copy of the plugin process MUST run for each Herdr server.
- **FR-011**: The stop action MUST NOT signal a process that is not the plugin.
- **FR-012**: The plugin MUST stop when its Herdr server stops.
- **FR-013**: The plugin MUST ignore agent-switcher hints.
- **FR-014**: The plugin MUST be installable with `herdr plugin install` and MUST document the one config line.
- **FR-015**: The plugin MUST have unit tests for detection, slot width, breakdown, scan and spin logic, and process lifecycle.
- **FR-016**: The plugin MUST be released under the MIT license.

### Key Entities

- **Agent pane**: a pane with a recognized agent. Has a pane ID, an agent label, and screen text.
- **Work counts**: number of shells, terminals, agents, and tasks found in one pane.
- **Slot**: the `<count> <spinner>` text in front of the state icon (3 cells).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of test frames, every state icon in the sidebar is in the same column.
- **SC-002**: The spinner shows within 3 seconds after background work starts and goes away within 3 seconds after it stops.
- **SC-003**: After a crash, no slot stays on screen after 15 seconds.
- **SC-004**: One scan of 65 agent panes takes less than 1 second.
- **SC-005**: A new user installs and sees the spinner in less than 5 minutes with the README only.
- **SC-006**: All unit tests pass on Linux and macOS with the standard Python interpreter.

## Assumptions

- Herdr 0.9.1 or newer. It supports pane metadata tokens with expiry and custom sidebar rows.
- Herdr does not let a plugin change the state icon. The slot to the left of the icon is the closest place.
- Herdr puts " · " after a custom token. The owner chose colored icons over a row without " · " (a plain-text full row loses all colors).
- Herdr trims normal spaces, so the blank slot uses a blank character that Herdr keeps.
- The user edits their own config once to add the slot. The plugin does not edit the config.
- Agent footer texts can change in new agent versions. The patterns then need an update.
- Rows of non-agent panes are not in the agent list, so they are not affected.
- During the first scan after start (3 seconds or less), rows can move once.
