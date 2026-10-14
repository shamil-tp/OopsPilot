import type { SVGProps } from "react";

// 16×16 pixel paper plane ("pilot") on a black tile, with a lime beacon pixel.
const PLANE = [
  "................",
  "................",
  "............###.",
  "..........##oo#.",
  "........##oooo#.",
  "......##ooooo#..",
  "....##oooooo##..",
  "..##oooooo#o#...",
  "....##oo##oo#...",
  "......##.#oo#...",
  "......#o#.#o#...",
  "......#o#..##...",
  ".......#........",
  "................",
  "................",
  "................",
];

function pathFor(char: string) {
  const d: string[] = [];
  PLANE.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) if (row[x] === char) d.push(`M${x} ${y}h1v1h-1z`);
  });
  return d.join("");
}

/** OpsPilot Logo: pixel-art paper plane on a black tile. */
export function OpsPilotLogo({ className = "h-6 w-6", ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 16 16"
      shapeRendering="crispEdges"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      {...props}
    >
      <rect width="16" height="16" fill="#1e1e1e" />
      <path d={pathFor("o")} fill="#3a3a3a" />
      <path d={pathFor("#")} fill="#e8e8e8" />
      <rect x="2" y="12" width="2" height="2" fill="#e8e8e8" />
    </svg>
  );
}
