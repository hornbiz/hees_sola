import { useCallback, useEffect, useRef, useState } from "react";
import { call } from "./api.js";

const HISTORY_KEY = "hees_sola_history";
const WORKING = ["Queued", "Downloading", "Downloaded", "Separating"];

const STEPS = [
	{ label: "Download", active: ["Queued", "Downloading"], done: ["Downloaded", "Separating", "Completed"] },
	{ label: "Remove music", active: ["Downloaded", "Separating"], done: ["Completed"] },
	{ label: "Ready", active: [], done: ["Completed"] },
];

function loadHistory() {
	try {
		return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
	} catch (e) {
		return [];
	}
}

function saveHistory(items) {
	try {
		localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 8)));
	} catch (e) {
		/* storage unavailable */
	}
}

function Steps({ status }) {
	return (
		<ol className="steps">
			{STEPS.map((step, i) => {
				const state = step.done.includes(status)
					? "done"
					: step.active.includes(status)
						? "active"
						: status === "Failed" && i === 0
							? "failed"
							: "";
				return (
					<li key={step.label} className={`step ${state}`}>
						<span className="dot">{state === "done" ? "✓" : i + 1}</span>
						{step.label}
					</li>
				);
			})}
		</ol>
	);
}

function Result({ url }) {
	return (
		<section className="card result">
			<h2>Vocals only</h2>
			<p className="muted">The words, with the music removed</p>
			<audio controls preload="metadata" src={url} />
			<a className="btn" href={`${url}&download=1`}>
				Download
			</a>
		</section>
	);
}

export default function App() {
	const [url, setUrl] = useState("");
	const [job, setJob] = useState(null);
	const [error, setError] = useState("");
	const [history, setHistory] = useState(loadHistory);
	const pollRef = useRef(null);
	const separatingRef = useRef(null);

	const stopPolling = useCallback(() => {
		clearInterval(pollRef.current);
		pollRef.current = null;
	}, []);

	const remember = useCallback((data) => {
		setHistory((prev) => {
			const next = [
				{ name: data.name, key: data.key, title: data.title },
				...prev.filter((item) => item.name !== data.name),
			];
			saveHistory(next);
			return next;
		});
	}, []);

	const startPolling = useCallback(
		(name, key) => {
			stopPolling();
			const tick = async () => {
				try {
					const data = await call("hees_sola.api.get_extraction", { name, key });
					setJob(data);
					remember(data);
					if (data.status === "Downloaded" && separatingRef.current !== name) {
						separatingRef.current = name;
						await call("hees_sola.api.separate_vocals", { name, key });
					}
					if (!WORKING.includes(data.status)) stopPolling();
				} catch (e) {
					stopPolling();
					setError(e.message || "Could not load this extraction.");
				}
			};
			tick();
			pollRef.current = setInterval(tick, 3000);
		},
		[stopPolling, remember]
	);

	useEffect(() => stopPolling, [stopPolling]);

	const submit = async (e) => {
		e.preventDefault();
		const value = url.trim();
		if (!value) return;
		setError("");
		setJob(null);
		try {
			const data = await call("hees_sola.api.download_audio", { source_url: value });
			setJob(data);
			remember(data);
			startPolling(data.name, data.key);
		} catch (err) {
			setError(err.message || "Could not start the extraction.");
		}
	};

	const openJob = (item) => {
		setError("");
		setJob(null);
		separatingRef.current = item.name;
		startPolling(item.name, item.key);
	};

	const working = job && WORKING.includes(job.status);

	return (
		<div className="page">
			<main className="wrap">
				<header className="hero">
					<span className="eyebrow">Hees Sola</span>
					<h1>Remove the music, keep the words</h1>
					<p>
						Paste a link from YouTube, TikTok, Instagram, Facebook, X or SoundCloud. No account needed.
					</p>
					<form className="form" onSubmit={submit}>
						<input
							type="url"
							inputMode="url"
							autoComplete="off"
							placeholder="Paste a link here"
							value={url}
							onChange={(e) => setUrl(e.target.value)}
							required
						/>
						<button type="submit" disabled={working}>
							{working ? "Working…" : "Extract vocals"}
						</button>
					</form>
				</header>

				{error && <div className="card error">{error}</div>}

				{job && (
					<section className="card">
						<h2>{job.title || "Getting your audio…"}</h2>
						<Steps status={job.status} />
						{working && <p className="muted">This can take a few minutes. You can leave this page open.</p>}
						{job.status === "Failed" && <p className="error-text">{job.error}</p>}
					</section>
				)}

				{job && job.status === "Completed" && job.vocals_url && <Result url={job.vocals_url} />}

				{history.length > 0 && (
					<section className="card">
						<h2>Your recent extractions</h2>
						<div className="recent">
							{history.map((item) => (
								<button key={item.name} className="recent-row" onClick={() => openJob(item)}>
									{item.title || item.name}
								</button>
							))}
						</div>
					</section>
				)}
			</main>
		</div>
	);
}
