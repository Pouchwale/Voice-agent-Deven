import type { ReviewLossCause, ReviewOutcome, ReviewSuggestionStatus, SettingsChangeSource } from "@/lib/types";

/** The review agent's mistake categories (backend: services/review/prompts.py ISSUE_CATEGORIES). */
export const CATEGORY_LABEL: Record<string, string> = {
  too_long: "Reply too long",
  many_questions: "Too many questions at once",
  ignored_question: "Ignored the customer's question",
  repeated_question: "Asked the same thing again",
  pushy: "Too pushy",
  wrong_info: "Wrong or invented information",
  misheard: "Misheard the customer",
  unnatural: "Sounded robotic",
  slow_reply: "Slow reply",
  talked_over: "Talked over the customer",
  weak_opening: "Weak opening",
  no_next_step: "No clear next step",
  other: "Other",
};

export const categoryLabel = (c: string) => CATEGORY_LABEL[c] ?? c.replace(/_/g, " ");

export const OUTCOME_LABEL: Record<ReviewOutcome, string> = {
  converted: "Converted",
  interested: "Interested",
  neutral: "Neutral",
  lost: "Lost",
  not_a_fit: "Not a fit",
};

export const CAUSE_LABEL: Record<ReviewLossCause, string> = {
  agent: "Lost because of the agent",
  customer_side: "Customer's own reason",
  technical: "Technical problem",
  unclear: "Cause not proven",
};

export const SUGGESTION_STATUS_LABEL: Record<ReviewSuggestionStatus, string> = {
  pending: "Waiting for you",
  approved: "Approved",
  rejected: "Rejected",
  superseded: "Replaced by a newer suggestion",
};

export const SOURCE_LABEL: Record<SettingsChangeSource, string> = {
  settings: "Settings",
  review: "Review suggestion",
  undo: "Undo",
  reset: "Reset",
};

export const SEVERITY_LABEL: Record<1 | 2 | 3, string> = { 1: "Minor", 2: "Hurt the call", 3: "Lost the customer" };
