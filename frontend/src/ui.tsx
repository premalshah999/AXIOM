// Compatibility only. The formatting helpers live in ./format and the primitives in ./ui/index; this file re-exports both
// under their old names for pages that still import "./ui". Delete it once nothing does.
import type { ReactNode } from "react";
import { ConfirmButton, Empty as EmptyLine, Field as FieldRow } from "./ui/index";

export * from "./format";
export { ErrorLine, External, Loading } from "./ui/index";

export function Field(props: { label: string; hint?: ReactNode; error?: string; children: ReactNode; wide?: boolean }) {
  const { wide: _wide, ...rest } = props;
  return <FieldRow {...rest} />;
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <EmptyLine title={title} hint={children} action={action} />;
}

export function Confirm({
  label,
  confirm,
  onConfirm,
  pending,
  disabled,
  danger = true,
  note,
}: {
  label: string;
  confirm: string;
  onConfirm: () => unknown;
  pending?: boolean;
  disabled?: boolean;
  danger?: boolean;
  note?: string;
}) {
  return (
    <ConfirmButton
      label={label}
      confirm={confirm}
      note={note}
      onConfirm={onConfirm}
      pending={pending}
      disabled={disabled}
      variant={danger ? "danger" : "secondary"}
    />
  );
}
