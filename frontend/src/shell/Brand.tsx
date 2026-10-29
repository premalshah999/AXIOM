/** The AXIOM mark: three cells of the progress tape (the product's one recurring motif) on the accent. */
export function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg className="ax-mark" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect width="24" height="24" rx="6" className="ax-mark-bg" />
      <rect x="5" y="6" width="3.2" height="12" rx="1" className="ax-mark-cell" />
      <rect x="10.4" y="6" width="3.2" height="12" rx="1" className="ax-mark-cell" />
      <rect x="15.8" y="6" width="3.2" height="12" rx="1" className="ax-mark-cell is-off" />
    </svg>
  );
}
