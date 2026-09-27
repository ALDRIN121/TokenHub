import type { CSSProperties } from 'react';

const paths = {
  sort: 'M9 8l3-3 3 3 M9 16l3 3 3-3',
  sortAscending: 'M12 19V5 M7 10l5-5 5 5',
  search: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  chevron: 'M9 5l7 7-7 7',
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  sources: 'M8 3v5 M16 3v5 M6 8h12v4a6 6 0 0 1-12 0z M12 18v3',
  quality: 'M9 3H5v18h14V3h-4 M9 2h6v4H9z M8 11l2 2 5-5 M8 17h7',
  shield: 'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
  refresh: 'M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-2l2 3 M4 16l2 3a7 7 0 0 0 12-2',
  input: 'M12 20V4 M6 10l6-6 6 6 M4 20h16',
  output: 'M12 4v16 M6 14l6 6 6-6 M4 4h16',
  cache: 'M20 12a8 8 0 1 1-2-5 M20 3v6h-6 M12 8v5l3 2',
  reasoning: 'M9 18h6 M10 21h4 M8 14a6 6 0 1 1 8 0l-1 4H9z',
  layers: 'M12 3l9 5-9 5-9-5z M3 12l9 5 9-5 M3 16l9 5 9-5',
  device: 'M4 3h16v13H4z M2 20h20 M8 16l-1 4 M16 16l1 4',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  info: 'M12 11v6 M12 7h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  check: 'M5 12l4 4L19 6',
  terminal: 'M5 6l5 6-5 6 M13 18h6',
  sparkle: 'M12 2v20 M2 12h20 M5 5l14 14 M5 19L19 5',
  hermes: 'M6 7v10 M18 7v10 M6 12h12 M3 4h6 M15 4h6 M3 20h6 M15 20h6',
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, className = '', style }: { name: IconName; className?: string; style?: CSSProperties }) {
  return (
    <svg className={`icon ${className}`} style={style} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={paths[name]} />
    </svg>
  );
}
