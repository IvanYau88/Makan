/** Illustrative plate, never a photograph of the venue. */
export function PlatePlaceholder({ className = "" }: { className?: string }) {
  return <span className={`plate-placeholder ${className}`} aria-hidden="true" />;
}
