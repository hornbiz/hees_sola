/**
 * Tiny Frappe REST client. Handles the CSRF token that Frappe requires for
 * session-authenticated POST requests.
 */

let csrfToken = null;

function readCookie(name) {
	const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
	return match ? decodeURIComponent(match[2]) : null;
}

function parseServerError(payload) {
	// Frappe returns _server_messages as a JSON array of JSON strings.
	if (payload && payload._server_messages) {
		try {
			const messages = JSON.parse(payload._server_messages);
			return messages
				.map((raw) => {
					try {
						const parsed = JSON.parse(raw);
						return parsed.message || parsed.title || raw;
					} catch (e) {
						return raw;
					}
				})
				.join("\n");
		} catch (e) {
			/* fall through */
		}
	}
	if (payload && payload.exception) {
		return String(payload.exception).split(":").slice(-1)[0].trim();
	}
	if (payload && payload.message) return payload.message;
	return null;
}

async function getCsrfToken() {
	if (csrfToken) return csrfToken;

	const fromCookie = readCookie("csrf_token");
	if (fromCookie) {
		csrfToken = fromCookie;
		return csrfToken;
	}

	// Some setups don't allow POST to this without a CSRF for anonymous requests.
	// Try to fetch a token in a way that Frappe accepts (GET is fine too).
	try {
		const res = await fetch("/api/method/hees_sola.api.get_csrf_token", {
			method: "GET",
			credentials: "include",
		});
		if (res.ok) {
			const data = await res.json();
			csrfToken = (data.message && data.message.csrf_token) || "";
		}
	} catch (e) {
		/* ignore */
	}
	return csrfToken || "";
}

export async function call(method, args = {}, { retry = true } = {}) {
	const token = await getCsrfToken();
	const res = await fetch(`/api/method/${method}`, {
		method: "POST",
		credentials: "include",
		headers: {
			"Content-Type": "application/json",
			"X-Frappe-CSRF-Token": token,
		},
		body: JSON.stringify(args),
	});

	let payload = {};
	try {
		payload = await res.json();
	} catch (e) {
		/* empty body */
	}

	if (res.status === 403 && retry) {
		// Expired csrf token: refresh once and retry.
		csrfToken = null;
		return call(method, args, { retry: false });
	}

	if (!res.ok) {
		const message = parseServerError(payload) || res.statusText || "Request failed";
		const error = new Error(message);
		error.status = res.status;
		throw error;
	}

	return payload.message;
}

export const getCsrf = getCsrfToken;

/** POST a file as multipart form data, plus string fields. */
export async function upload(method, fields, blob, { retry = true } = {}) {
	const token = await getCsrfToken();
	const form = new FormData();
	Object.entries(fields).forEach(([key, value]) => form.append(key, value == null ? "" : value));
	form.append("file", blob, "audio");
	const res = await fetch(`/api/method/${method}`, {
		method: "POST",
		credentials: "include",
		headers: { "X-Frappe-CSRF-Token": token },
		body: form,
	});

	let payload = {};
	try {
		payload = await res.json();
	} catch (e) {
		/* empty body */
	}

	if (res.status === 403 && retry) {
		csrfToken = null;
		return upload(method, fields, blob, { retry: false });
	}
	if (!res.ok) {
		const error = new Error(parseServerError(payload) || res.statusText || "Upload failed");
		error.status = res.status;
		throw error;
	}
	return payload.message;
}
