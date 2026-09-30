/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverComponentsExternalPackages: ['ollama'],
  },
  async rewrites() {
    return [
      {
        source: '/ws/chat',
        destination: 'http://localhost:8000/ws/chat',
      },
    ];
  },
};

module.exports = nextConfig;