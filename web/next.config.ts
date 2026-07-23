import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Default (60s) is too tight for static export inside a CPU-throttled
  // Docker build environment (observed failing intermittently there even
  // though a native `npm run build` completes in ~13s). See
  // docs/known-limitations.md.
  staticPageGenerationTimeout: 180,
};

export default nextConfig;
