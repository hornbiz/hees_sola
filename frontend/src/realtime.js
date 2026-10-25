/**
 * Live progress over Frappe's socket.io server. Joining the "task" room named by
 * the extraction's secret key means only its owner receives its updates.
 */
import { io } from "socket.io-client";

export const PROGRESS_EVENT = "hees_sola_progress";

let socket = null;

function getSocket() {
	if (!socket) {
		socket = io(`${window.location.origin}/${window.location.hostname}`, {
			withCredentials: true,
			transports: ["websocket", "polling"],
			reconnectionDelayMax: 10000,
		});
	}
	return socket;
}

/**
 * Listen for progress of one extraction. `onConnect` fires on every (re)connect
 * so the caller can catch up on anything missed; `onState(connected)` reports
 * connection changes. Returns a function that stops listening.
 */
export function watchProgress(key, { onEvent, onConnect, onState }) {
	const sock = getSocket();
	const join = () => {
		sock.emit("task_subscribe", key);
		onState(true);
		onConnect();
	};
	const leave = () => onState(false);

	sock.on(PROGRESS_EVENT, onEvent);
	sock.on("connect", join);
	sock.on("disconnect", leave);
	sock.on("connect_error", leave);
	if (sock.connected) join();
	else onState(false);

	return () => {
		sock.emit("task_unsubscribe", key);
		sock.off(PROGRESS_EVENT, onEvent);
		sock.off("connect", join);
		sock.off("disconnect", leave);
		sock.off("connect_error", leave);
	};
}
