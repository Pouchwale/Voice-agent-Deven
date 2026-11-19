"use client";

import { useId, useState, type ReactNode } from "react";
import { ACTOR_MAX, ACTOR_MIN, cleanActorName, getActorName, setActorName } from "@/lib/actor";
import { cn } from "@/lib/cn";
import { Field, TextInput } from "./Input";
import { Modal } from "./Modal";

/** The word the person must type. Capitals are not required ("confirm" counts): the point is a deliberate act. */
export const CONFIRM_WORD = "CONFIRM";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Runs once the name is filled in and the word is typed. The name is already remembered when it runs. */
  onConfirm: () => void;
  title: ReactNode;
  /** What is about to happen. */
  children?: ReactNode;
  confirmLabel?: string;
  /** A destructive action: outlined confirm button, like the other destructive buttons. */
  danger?: boolean;
  busy?: boolean;
}

/**
 * Confirm dialog for changes that must be deliberate: the person gives their name (remembered on this computer,
 * shown in the activity log) and types CONFIRM. The confirm button stays off until both are there.
 */
export function TypeConfirmDialog(props: Props) {
  // Mounted only while open: every opening starts with an empty word box.
  return props.open ? <TypeConfirmForm {...props} /> : null;
}

function TypeConfirmForm({ onClose, onConfirm, title, children, confirmLabel = "Confirm", danger = false, busy = false }: Props) {
  const formId = useId();
  const nameId = useId();
  const wordId = useId();
  const [name, setName] = useState(getActorName);
  const [word, setWord] = useState("");
  // Where typing starts: the name the first time on this computer, the word after that.
  const [startAtName] = useState(() => !getActorName());

  const cleanName = cleanActorName(name);
  const nameOk = Array.from(cleanName).length >= ACTOR_MIN;
  const wordOk = word.trim().toUpperCase() === CONFIRM_WORD;
  const ready = nameOk && wordOk && !busy;

  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
      size="sm"
      footer={
        <>
          <button type="button" className="btn border-line bg-white text-fg hover:bg-canvas" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="submit"
            form={formId}
            className={cn("btn text-white", danger ? "border-fg bg-white !text-fg hover:bg-subtle" : "bg-primary hover:bg-primary-hover")}
            disabled={!ready}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      <form
        id={formId}
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!ready) return;
          setActorName(cleanName);
          onConfirm();
        }}
      >
        {children ? <div className="text-sm leading-relaxed">{children}</div> : null}
        <Field label="Your name" htmlFor={nameId} hint="Saved in the activity log with this change. Remembered on this computer.">
          <TextInput
            id={nameId}
            name="actor-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={ACTOR_MAX}
            autoComplete="name"
            placeholder="e.g. Riya Shah"
            disabled={busy}
            {...(startAtName ? { "data-autofocus": true } : {})}
          />
        </Field>
        <Field
          label={
            <>
              Type &quot;<span className="font-bold tracking-wide">{CONFIRM_WORD}</span>&quot; to proceed
            </>
          }
          htmlFor={wordId}
        >
          <TextInput
            id={wordId}
            name="confirm-word"
            value={word}
            onChange={(e) => setWord(e.target.value)}
            maxLength={24}
            autoComplete="off"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            className="font-semibold tracking-wide uppercase"
            disabled={busy}
            {...(startAtName ? {} : { "data-autofocus": true })}
          />
        </Field>
      </form>
    </Modal>
  );
}
