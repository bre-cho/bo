/** @type {import('next').NextConfig} */
const isDeployBuild = process.env.NEXT_DEPLOY_STANDALONE === "1";

const nextConfig = {
  // Allow the app to be served behind a reverse proxy
  ...(isDeployBuild ? { output: "standalone" } : {}),

  // Rewrites: forward /api/* to the FastAPI backend in development
  async rewrites() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    return [
      {
        source:      "/api/:path*",
        destination: `${apiUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
