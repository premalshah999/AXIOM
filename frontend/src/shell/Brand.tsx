/** The AXIOM wordmark: thin drawn letters, with a lamp in the O that is lit while the machine runs. */
export function Wordmark({ width = 80 }: { width?: number }) {
  return (
    <svg className="ax-mark" width={width} viewBox="0 0 80 16" aria-hidden="true">
      <path d="M.9 15.6 6.6.4h.8l5.7 15.2M3 10.6h8" />
      <path d="M18 .4l11 15.2M29 .4 18 15.6" />
      <path d="M35.5.4v15.2" />
      <circle className="o" cx="50" cy="8" r="7.4" />
      <circle className="lamp" cx="50" cy="8" r="2.8" />
      <path d="M64.5 15.6V.4h.4l6 11.6 6-11.6h.4v15.2" />
    </svg>
  );
}
