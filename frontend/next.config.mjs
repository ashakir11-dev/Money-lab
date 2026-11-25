/** Static export: `npm run build` writes plain HTML/JS to out/, served by FastAPI. */
const nextConfig = {
  output: "export",
  trailingSlash: false,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
