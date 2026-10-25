/**
 * Download YouTube audio on the user's own device (its own IP address), so the
 * server never talks to YouTube and needs no cookies. Only works inside the
 * Android app: CapacitorHttp makes native requests, which have no CORS limits.
 */
import { Capacitor, CapacitorHttp } from "@capacitor/core";

const PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false";
const CLIENT = {
	clientName: "ANDROID_VR",
	clientVersion: "1.60.19",
	deviceMake: "Oculus",
	deviceModel: "Quest 3",
	androidSdkVersion: 32,
	osName: "Android",
	osVersion: "12L",
	hl: "en",
};
const USER_AGENT =
	"com.google.android.apps.youtube.vr.oculus/1.60.19 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip";
const CHUNK_BYTES = 2 * 1024 * 1024;
const MAX_SECONDS = 3 * 3600;

export function canDownloadOnDevice(url) {
	return Capacitor.isNativePlatform() && youtubeId(url) !== null;
}

export function youtubeId(url) {
	let parsed;
	try {
		parsed = new URL(url);
	} catch (e) {
		return null;
	}
	const host = parsed.hostname.replace(/^(www|m|music)\./, "");
	if (host === "youtu.be") return parsed.pathname.slice(1).split("/")[0] || null;
	if (host === "youtube.com") {
		if (parsed.pathname === "/watch") return parsed.searchParams.get("v");
		const match = parsed.pathname.match(/^\/(shorts|live|embed)\/([\w-]{6,})/);
		if (match) return match[2];
	}
	return null;
}

function base64ToBytes(data) {
	const binary = atob(data);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	return bytes;
}

async function pickAudio(videoId) {
	const res = await CapacitorHttp.post({
		url: PLAYER_URL,
		headers: {
			"Content-Type": "application/json",
			"User-Agent": USER_AGENT,
			"X-YouTube-Client-Name": "28",
			"X-YouTube-Client-Version": CLIENT.clientVersion,
		},
		data: { context: { client: CLIENT }, videoId, contentCheckOk: true, racyCheckOk: true },
	});
	const info = typeof res.data === "string" ? JSON.parse(res.data) : res.data;
	const status = info && info.playabilityStatus && info.playabilityStatus.status;
	if (status !== "OK") {
		throw new Error((info && info.playabilityStatus && info.playabilityStatus.reason) || "Video unavailable");
	}
	if (Number(info.videoDetails.lengthSeconds) > MAX_SECONDS) throw new Error("Video is too long");

	const formats = (info.streamingData && info.streamingData.adaptiveFormats) || [];
	const audio = formats
		.filter((f) => f.url && f.mimeType && f.mimeType.startsWith("audio/") && f.contentLength)
		.sort((a, b) => {
			// Prefer m4a (what ffmpeg handles best), then the highest bitrate.
			const am = a.mimeType.includes("mp4") ? 1 : 0;
			const bm = b.mimeType.includes("mp4") ? 1 : 0;
			return bm - am || b.bitrate - a.bitrate;
		})[0];
	if (!audio) throw new Error("No audio stream available");
	return { audio, title: info.videoDetails.title };
}

/** Returns {blob, title}. onProgress receives a 0..1 fraction. */
export async function downloadOnDevice(url, onProgress = () => {}) {
	const videoId = youtubeId(url);
	if (!videoId) throw new Error("Not a YouTube link");
	const { audio, title } = await pickAudio(videoId);

	const total = Number(audio.contentLength);
	const parts = [];
	for (let start = 0; start < total; start += CHUNK_BYTES) {
		const end = Math.min(start + CHUNK_BYTES, total) - 1;
		const res = await CapacitorHttp.get({
			url: audio.url,
			headers: { "User-Agent": USER_AGENT, Range: `bytes=${start}-${end}` },
			responseType: "blob",
		});
		if (res.status >= 400) throw new Error(`Download failed (${res.status})`);
		parts.push(base64ToBytes(res.data));
		onProgress((end + 1) / total);
	}
	return { blob: new Blob(parts, { type: audio.mimeType.split(";")[0] }), title };
}
