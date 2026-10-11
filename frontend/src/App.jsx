import { useCallback, useEffect, useRef, useState } from "react";
import { call } from "./api.js";

const BADGE_COLORS = {
	Queued: "gray",
	Downloading: "orange",
	Downloaded: "purple",
	Separating: "blue",
	Completed: "green",
	Failed: "red",
};

const PLATFORMS = {
	"youtube.com": "YouTube",
	"youtu.be": "YouTube",
	"music.youtube.com": "YouTube",
	"tiktok.com": "TikTok",
	"instagram.com": "Instagram",
	"facebook.com": "Facebook",
	"fb.watch": "Facebook",
	"twitter.com": "X (Twitter)",
	"x.com": "X (Twitter)",
	"soundcloud.com": "SoundCloud",
	"vimeo.com": "Vimeo",
};

function detectPlatform(url) {
	try {
		const host = new URL(url).hostname.toLowerCase().replace(/^www\./, "");
		for (const domain in PLATFORMS) {
			if (host === domain || host.endsWith("." + domain)) return PLATFORMS[domain];
		}
		return host ? "Unknown" : "";
	} catch (e) {
		return "";
	}
}

function Steps({ status }) {
	const downloadDone = ["Downloaded", "Separating", "Completed"].includes(status);
	const downloadActive = ["Queued", "Downloading"].includes(status);
	const separateDone = status === "Completed";
	const separateActive = status === "Separating";
	const separateReady = status === "Downloaded";
	const failed = status === "Failed";

	const steps = [
		{ label: "Download", state: downloadDone ? "done" : downloadActive ? "active" : "" },
		{
			label: "Remove music",
			state: separateDone ? "done" : separateActive ? "active" : separateReady ? "ready" : "",
		},
		{ label: "Play & download", state: separateDone ? "done" : "" },
	];

	return (
		<div className="steps">
			{steps.map((step, i) => (
				<div
					className={`step ${step.state}${failed && i === 0 ? " failed" : ""}`}
					key={step.label}
				>
					<span className="dot" />
					{step.label}
				</div>
			))}
		</div>
	);
}

function Player({ title, subtitle, url, accent }) {
	if (!url) return null;
	return (
		<div className={`card audio-card${accent ? " accent" : ""}`}>
			<h4>{title}</h4>
			{subtitle && <p className="muted small">{subtitle}</p>}
			<audio controls preload="none" src={url} />
			<div className="links">
				<a className="btn-link" href={url} download>
					Download
				</a>
				<a href={url} target="_blank" rel="noreferrer">
					Open
				</a>
			</div>
		</div>
	);
}

export default function App() {
	const [url, setUrl] = useState("");
	const [job, setJob] = useState(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const [recent, setRecent] = useState([]);
	const [user, setUser] = useState(null);
	const pollRef = useRef(null);

	const loadRecent = useCallback(async () => {
		try {
			const rows = await call("hees_sola.api.list_extractions", { limit: 8 });
			setRecent(rows || []);
		} catch (e) {
			/* ignore */
		}
	}, []);

	useEffect(() => {
		(async () => {
			try {
				setUser(await call("hees_sola.api.whoami"));
			} catch (e) {
				setUser({ is_guest: true });
			}
			loadRecent();
		})();
	}, [loadRecent]);

	const stopPolling = useCallback(() => {
		if (pollRef.current) {
			clearInterval(pollRef.current);
			pollRef.current = null;
		}
	}, []);

	const startPolling = useCallback(
		(name) => {
			stopPolling();
			const tick = async () => {
				try {
					const data = await call("hees_sola.api.get_extraction", { name });
					setJob(data);
					if (!["Queued", "Downloading", "Separating"].includes(data.status)) {
						stopPolling();
						setBusy(false);
						loadRecent();
					}
				} catch (e) {
					stopPolling();
					setBusy(false);
				}
			};
			tick();
			pollRef.current = setInterval(tick, 3000);
		},
		[stopPolling, loadRecent]
	);

	useEffect(() => () => stopPolling(), [stopPolling]);

	const download = async (e) => {
		e.preventDefault();
		const value = url.trim();
		if (!value || busy) return;
		setBusy(true);
		setError("");
		setJob(null);
		try {
			const res = await call("hees_sola.api.download_audio", { source_url: value });
			setJob(res);
			startPolling(res.name);
		} catch (err) {
			setBusy(false);
			setError(err.message || "Could not start the download.");
		}
	};

	const removeMusic = async () => {
		if (!job || busy) return;
		setBusy(true);
		setError("");
		try {
			await call("hees_sola.api.separate_vocals", { name: job.name });
			setJob({ ...job, status: "Separating" });
			startPolling(job.name);
		} catch (err) {
			setBusy(false);
			setError(err.message || "Could not remove the music.");
		}
	};

	const openJob = (name) => {
		setError("");
		setBusy(true);
		setJob(null);
		startPolling(name);
	};

	const platform = detectPlatform(url);
	const separating = job && job.status === "Separating";
	const completed = job && job.status === "Completed";

	return (
		<div className="page">
			<div className="wrap">
				<header className="hero">
					<h1>Remove the music, keep the words</h1>
					<p>
						Paste a YouTube, TikTok, Instagram, Facebook, X or SoundCloud link, download the audio,
						then strip the music and keep only the vocals.
					</p>
					<form className="form" onSubmit={download}>
						<input
							type="url"
							inputMode="url"
							autoComplete="off"
							placeholder="https://www.youtube.com/watch?v=..."
							value={url}
							onChange={(e) => setUrl(e.target.value)}
							required
						/>
						<button type="submit" disabled={busy}>
							{job && job.status === "Downloading" ? (
								<>
									<span className="spin" /> Downloading...
								</>
							) : (
								"Download audio"
							)}
						</button>
					</form>
					{platform && (
						<div className="platform">
							{platform === "Unknown" ? "Unknown platform - will try anyway" : `Detected: ${platform}`}
						</div>
					)}
				</header>

				{user && user.is_guest && (
					<div className="banner">
						You are not signed in.{" "}
						<a href={`/login?redirect-to=${encodeURIComponent("/hees_sola")}`}>Sign in</a> to
						download and edit audio.
					</div>
				)}

				{error && <div className="card error">{error}</div>}

				{job && (
					<div className="card">
						<h3>{job.title || job.name}</h3>
						<Steps status={job.status} />
						<p className="muted small">
							Reference{" "}
							<a
								href={`/app/vocal-extraction/${encodeURIComponent(job.name)}`}
								target="_blank"
								rel="noreferrer"
							>
								{job.name}
							</a>
							{job.platform ? ` · ${job.platform}` : ""}
						</p>
						{job.status === "Failed" && job.error_log && (
							<pre className="error small">{job.error_log}</pre>
						)}
					</div>
				)}

				{job && job.status === "Downloaded" && (
					<div className="card action-card">
						<h3>Step 2 - Remove the music</h3>
						<p className="muted small">
							Keep only the vocals (the words) and drop the background music.
						</p>
						<button className="primary" onClick={removeMusic} disabled={busy}>
							Remove music
						</button>
					</div>
				)}

				{separating && (
					<div className="card action-card">
						<h3>Step 2 - Removing the music</h3>
						<p className="muted small">
							<span className="spin dark" /> This can take a minute or two.
						</p>
					</div>
				)}

				{completed && (
					<>
						<Player
							accent
							title="Vocals only"
							subtitle="The words, with the music removed"
							url={job.vocals_file}
						/>
					</>
				)}

				<div className="card">
					<h3>Recent extractions</h3>
					{recent.length === 0 ? (
						<p className="muted small">No extractions yet.</p>
					) : (
						<div className="recent">
							{recent.map((row) => (
								<button key={row.name} className="recent-row" onClick={() => openJob(row.name)}>
									<span className="rt">
										<b>{row.title || row.source_url || row.name}</b>
										<span className="muted small">{row.creation}</span>
									</span>
									<span className={`badge ${BADGE_COLORS[row.status] || "gray"}`}>
										{row.status}
									</span>
								</button>
							))}
						</div>
					)}
				</div>
			</div>
		</div>
	);
}
