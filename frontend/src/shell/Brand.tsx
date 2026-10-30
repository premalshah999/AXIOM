/** The AXIOM mark: three cells of the progress tape, in the text colour (the accent is kept for what you can act on). */
export function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg className="ax-mark" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="4" width="4.5" height="16" rx="1.5" />
      <rect x="9.75" y="4" width="4.5" height="16" rx="1.5" />
      <rect x="16.5" y="4" width="4.5" height="16" rx="1.5" opacity="0.35" />
    </svg>
  );
}
