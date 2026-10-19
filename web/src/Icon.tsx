const paths = {
  discover: "M4 11l8-7 8 7v9h-5v-6H9v6H4z",
  group:
    "M3 20c0-7 12-7 12 0M17 14c3 0 4 2 4 5M12 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0M20 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0",
  execution: "M4 5h16M4 12h16M4 19h16M8 3v4M16 10v4M10 17v4",
  account: "M5 20c0-8 14-8 14 0M16 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
};

export function Icon({ name }: { name: keyof typeof paths }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
