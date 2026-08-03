"use agent";

import { type Task, agent, pick, userInterfaceTools } from "@guildai/agents-sdk";
import { gitHubTools } from "@guildai-services/guildai~github";
import { z } from "zod";

// Input mirrors the payload sent by drift/guild_trigger.py exactly (CONTRACT.md).
const inputSchema = z.object({
  repo: z.string(), // "owner/name", e.g. "sharique2004/mmm-demo"
  issue_number: z.number(),
  prior_decision: z.string(),
  new_decision: z.string(),
  what_changed: z.string(),
  speaker: z.string(),
  meeting: z.string(),
  date: z.string(),
  quote: z.string(),
  ts: z.string(),
});

const outputSchema = z.object({
  posted: z.boolean(),
});

const tools = {
  ...userInterfaceTools,
  ...pick(gitHubTools, ["github_issues_get", "github_issues_create_comment"]),
};

type Input = z.infer<typeof inputSchema>;

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.text === "string") return record.text;
    if (typeof record.value === "string") return record.value;
  }
  return value == null ? "" : String(value);
}

function stripFences(text: string): string {
  return text
    .trim()
    .replace(/^```[a-zA-Z]*\s*\n/, "")
    .replace(/\n```\s*$/, "")
    .trim();
}

// The exact comment template. Doubles as the deterministic fallback body.
// Must stay in lockstep with build_comment() in drift/guild_trigger.py.
function commentSkeleton(input: Input): string {
  const oneLine = (s: string) => s.replace(/\s+/g, " ").trim();
  const cell = (s: string) => oneLine(s).replace(/\|/g, "\\|");
  return [
    "> [!IMPORTANT]",
    "> **Decision changed in a later meeting.**",
    `> The plan recorded on this issue was revised during **${oneLine(input.meeting)}** (${oneLine(input.date)}).`,
    "",
    "|            | Decision |",
    "| ---------- | -------- |",
    `| **Before** | ${cell(input.prior_decision)} |`,
    `| **After**  | ${cell(input.new_decision)} |`,
    "",
    `**What changed:** \`${oneLine(input.what_changed)}\``,
    "",
    "<details>",
    "<summary>Meeting excerpt</summary>",
    "",
    `> [${oneLine(input.ts)}] **${oneLine(input.speaker)}:** ${oneLine(input.quote)}`,
    "",
    "</details>",
    "",
    "---",
    "",
    `_Meeting: ${oneLine(input.meeting)}, ${oneLine(input.date)} · Detected by Drift Historian (FalkorDB graph diff) · via Guild.ai_`,
  ].join("\n");
}

export default agent({
  description:
    "Herald — Drift's governed reporter. Drafts a 'decision changed in a later meeting' " +
    "comment for a GitHub issue, waits for human approval, then posts it.",
  inputSchema,
  outputSchema,
  tools,
  run: async (input: Input, task: Task<typeof tools>) => {
    const skeleton = commentSkeleton(input);

    const prompt = [
      "You are Herald, the reporter for Drift, a meeting-memory system.",
      `A decision tracked on GitHub issue ${input.repo}#${input.issue_number} was changed in a later meeting, and you must draft the issue comment announcing it.`,
      "Below is the exact GitHub-flavored-markdown template, already filled in with the detected values.",
      "Reproduce it faithfully: keep every line, the exact ordering, and every blank line (the blank line after </summary> is required or GitHub will not render the excerpt).",
      "You may only smooth grammar inside the Before / After table cells and the banner sentence; never invent facts, and never add, drop, or reorder lines.",
      "Output ONLY the raw markdown comment — no code fences, no preamble, no commentary.",
      "",
      "TEMPLATE:",
      skeleton,
    ].join("\n");

    // NON-streaming on purpose: streaming 501s with Gemini on Guild.
    let body = skeleton;
    try {
      const generated = asText(await task.llm.generateText({ prompt }));
      const cleaned = stripFences(generated);
      if (cleaned.length > 0) body = cleaned;
    } catch {
      // Drafting is cosmetic — the deterministic skeleton is already a complete comment.
    }

    // Human approval gate: blocks until someone answers in the session UI.
    // No UI attached / no answer / anything but yes -> do not post.
    let approved = false;
    try {
      const reply = await task.ui?.prompt({
        type: "text",
        text: [
          `Herald wants to post this comment on ${input.repo}#${input.issue_number}:`,
          "",
          body,
          "",
          'Reply "yes" to post it, anything else to cancel.',
        ].join("\n"),
      });
      approved = /^\s*(y|yes|approve|approved|ok|lgtm)\b/i.test(asText(reply));
    } catch {
      approved = false;
    }
    if (!approved) {
      return { posted: false };
    }

    const slash = input.repo.indexOf("/");
    const owner = slash === -1 ? input.repo : input.repo.slice(0, slash);
    const repo = slash === -1 ? "" : input.repo.slice(slash + 1);
    if (!task.tools) {
      return { posted: false };
    }
    await task.tools.github_issues_create_comment({
      owner,
      repo,
      issue_number: input.issue_number,
      body,
    });
    return { posted: true };
  },
});
