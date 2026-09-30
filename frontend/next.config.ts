import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits a self-contained server in .next/standalone for a small Docker image.
  output: "standalone",
};

export default nextConfig;
