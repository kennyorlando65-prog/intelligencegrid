/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,

  // NOTE: no `rewrites` here, deliberately.
  //
  // The deployed app routes /api/:path* to the Python service through the
  // `services` + `rewrites` block in the repository-root vercel.json. Keeping
  // routing in exactly one place matters: Next.js rewrites here combined with
  // root rewrites produces a 404 that neither file explains on its own.

  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
