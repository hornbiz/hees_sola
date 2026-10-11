/**
 * Vite dev-server proxy so `npm run dev` talks to the local Frappe site.
 * Reads the webserver port from sites/common_site_config.json when available.
 */

import fs from "fs";
import path from "path";

let webserver_port = 8000;

try {
	const configPath = path.resolve("../../../sites/common_site_config.json");
	if (fs.existsSync(configPath)) {
		const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
		webserver_port = config.webserver_port || webserver_port;
	}
} catch (error) {
	console.warn("Could not read common_site_config.json, using port", webserver_port);
}

const proxyOptions = {
	"^/(app|assets|files|private)": {
		target: `http://127.0.0.1:${webserver_port}`,
		ws: true,
		router(req) {
			const site_name = req.headers.host.split(":")[0];
			return `http://${site_name}:${webserver_port}`;
		},
	},
	"^/api": {
		target: `http://127.0.0.1:${webserver_port}`,
		changeOrigin: true,
	},
};

export default proxyOptions;
