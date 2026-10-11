import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import proxyOptions from "./proxyOptions.js";

export default defineConfig({
	base: "",
	server: {
		host: "::",
		port: 8080,
		proxy: proxyOptions,
		hmr: { overlay: false },
	},
	plugins: [react()],
	build: {
		outDir: "../hees_sola/public/hees_sola",
		emptyOutDir: true,
		target: "es2015",
	},
});
