// A/B the Deep Research handoff against the events Studio's loop really publishes.
//
// BEFORE is the adapter's original inline reading, transcribed from
// 6e91f09b6:studio/frontend/src/features/chat/api/chat-adapter.ts; the transcription is
// checked against that file below, so it cannot quietly drift into a straw man.
// AFTER is the shipped helper, imported from source.
//
// Same events, same probe. Only the implementation changes.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const {
  newDeepResearchHandoff,
  readDeepResearchToolEvent,
} = await import("./studio/frontend/src/features/chat/utils/deep-research-handoff.ts");

const BEFORE_REF = process.env.BEFORE_REF ?? "6e91f09b6";
const ADAPTER = "studio/frontend/src/features/chat/api/chat-adapter.ts";
const events = JSON.parse(readFileSync("deep-research-events.json", "utf8"));

// ── the transcription, and the proof it is one ───────────────────────────────

const beforeSource = execFileSync("git", ["show", `${BEFORE_REF}:${ADAPTER}`], {
  encoding: "utf8",
  maxBuffer: 64 * 1024 * 1024,
});
for (const line of [
  'if (toolEvent.tool_name === "deep_research") {',
  'if (toolEvent.type === "tool_start") {',
  "if (deepResearchHandoff === null && question) {",
  "pendingResearchQuestion = question;",
  'toolEvent.type === "tool_end" &&',
  "toolEvent.tool_call_id === pendingResearchCallId",
  "? pendingResearchQuestion",
]) {
  assert.ok(
    beforeSource.includes(line),
    `BEFORE transcription is stale: ${BEFORE_REF} no longer contains ${line}`,
  );
}
// Both halves returned to the top of the chunk loop, so no tool card was ever drawn.
assert.equal(
  (beforeSource.match(/^\s+continue;\n\s+\}\n\s+\/\/ Persist container_id/m) || []).length,
  1,
  "BEFORE transcription is stale: the deep_research branch no longer ends in continue",
);

function readBefore(events) {
  let deepResearchHandoff = null;
  let pendingResearchCallId = "";
  let pendingResearchQuestion = "";
  let drewACard = false;
  for (const toolEvent of events) {
    if (toolEvent.tool_name === "deep_research") {
      if (toolEvent.type === "tool_start") {
        const args = toolEvent.arguments;
        const question =
          args && typeof args === "object"
            ? String(args.question ?? "").trim()
            : "";
        if (deepResearchHandoff === null && question) {
          pendingResearchCallId =
            typeof toolEvent.tool_call_id === "string" ? toolEvent.tool_call_id : "";
          pendingResearchQuestion = question;
        }
        continue;
      }
      if (toolEvent.type === "tool_end" && deepResearchHandoff === null) {
        deepResearchHandoff =
          toolEvent.tool_call_id === pendingResearchCallId
            ? pendingResearchQuestion
            : "";
      }
      continue;
    }
    drewACard = true;
  }
  return { started: deepResearchHandoff !== null, question: deepResearchHandoff, drewACard };
}

function readAfter(events) {
  const handoff = newDeepResearchHandoff();
  let drewACard = false;
  for (const toolEvent of events) {
    if (
      toolEvent.tool_name === "deep_research" &&
      readDeepResearchToolEvent(handoff, toolEvent)
    ) {
      continue;
    }
    drewACard = true;
  }
  return { started: handoff.question !== null, question: handoff.question, drewACard };
}

// ── the assertions ───────────────────────────────────────────────────────────

let failures = 0;
function check(label, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}: expected ${expected}, got ${actual}`);
}

console.log("--- BEFORE (the reading this PR shipped) ---");
const beforeRan = readBefore(events.ran);
const beforeDenied = readBefore(events.denied);
const beforeSpent = readBefore(events.budget_spent);
check("a call that ran starts research", beforeRan.started, true);
check("REPRO: a DENIED call also starts research", beforeDenied.started, true);
check(
  "REPRO: the denied run researches the model's question",
  beforeDenied.question,
  "Which small dog breeds suit a flat with no garden?",
);
check(
  "REPRO: the Allow / Deny card is never drawn, so the loop blocks on a verdict",
  beforeDenied.drewACard,
  false,
);
check("REPRO: a call the budget refused also starts research", beforeSpent.started, true);

console.log("--- AFTER (the fix under review) ---");
const afterRan = readAfter(events.ran);
const afterDenied = readAfter(events.denied);
const afterSpent = readAfter(events.budget_spent);
check("a call that ran still starts research", afterRan.started, true);
check(
  "the question the model handed off is unchanged",
  afterRan.question,
  "Which small dog breeds suit a flat with no garden?",
);
check("a call that ran still draws no tool card", afterRan.drewACard, false);
check("FIXED: a DENIED call does not start research", afterDenied.started, false);
check("FIXED: the gated call keeps its Allow / Deny card", afterDenied.drewACard, true);
check("FIXED: a call the budget refused does not start research", afterSpent.started, false);

console.log(failures === 0 ? "\nA/B RESULT: PASS" : `\nA/B RESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
