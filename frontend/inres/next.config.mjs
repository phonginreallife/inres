/** @type {import('next').NextConfig} */
const apiBase = process.env.NEXT_PUBLIC_API_URL || '/api';
const aiPublicBase = (process.env.NEXT_PUBLIC_AI_API_URL || '/ai').replace(/\/$/, '');

const nextConfig = {
  output: 'standalone',

  // When NEXT_PUBLIC_API_URL is a same-origin path (e.g. /api), proxy to the Go API in dev
  // so /api/env → http://127.0.0.1:8080/env and the Supabase client gets real keys.
  //
  // When NEXT_PUBLIC_AI_API_URL is a path (default /ai), proxy to the Python agent (uvicorn
  // default port 8002) so /ai/api/sync-bucket → FastAPI /api/sync-bucket.
  async rewrites() {
    const rules = [];

    if (!apiBase.startsWith('http')) {
      const backend =
        process.env.API_INTERNAL_URL ||
        process.env.INRES_API_BACKEND ||
        'http://127.0.0.1:8080';
      const base = backend.replace(/\/$/, '');
      rules.push({
        source: '/api/:path*',
        destination: `${base}/:path*`,
      });
    }

    if (!aiPublicBase.startsWith('http')) {
      const aiBackend =
        process.env.AI_AGENT_INTERNAL_URL ||
        process.env.INRES_AI_BACKEND ||
        'http://127.0.0.1:8002';
      const aiBase = aiBackend.replace(/\/$/, '');
      rules.push({
        source: `${aiPublicBase}/:path*`,
        destination: `${aiBase}/:path*`,
      });
    }

    return rules;
  },

  // PWA Configuration
  headers: async () => [
    {
      source: '/manifest.json',
      headers: [
        {
          key: 'Content-Type',
          value: 'application/manifest+json',
        },
      ],
    },
  ],
};

export default nextConfig;
