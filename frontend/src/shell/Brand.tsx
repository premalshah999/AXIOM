const PANES = [
  "M0.52 14.80A14.5 14.5 0 0 1 2.11 8.86L10.91 13.39A4.6 4.6 0 0 0 10.41 15.28Z",
  "M2.80 7.66A14.5 14.5 0 0 1 7.16 3.30L12.51 11.63A4.6 4.6 0 0 0 11.13 13.01Z",
  "M8.36 2.61A14.5 14.5 0 0 1 14.30 1.02L14.78 10.91A4.6 4.6 0 0 0 12.89 11.41Z",
  "M15.70 1.02A14.5 14.5 0 0 1 21.64 2.61L17.11 11.41A4.6 4.6 0 0 0 15.22 10.91Z",
  "M22.84 3.30A14.5 14.5 0 0 1 27.20 7.66L18.87 13.01A4.6 4.6 0 0 0 17.49 11.63Z",
  "M27.89 8.86A14.5 14.5 0 0 1 29.48 14.80L19.59 15.28A4.6 4.6 0 0 0 19.09 13.39Z",
];

/** The AXIOM mark: a fanlight with all six panes lit, in lamplight. `size` 20 is the top bar's. Beside "AXIOM" in .ax-brand. */
export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg className="ax-mark" width={size * 1.5} height={size * 0.8} viewBox="0 0 30 16" aria-hidden="true">
      {PANES.map((d) => (
        <path key={d} d={d} />
      ))}
      <path d="M12 15.5A3 3 0 0 1 18 15.5Z" />
    </svg>
  );
}
