"""Install-time hooks for the Hees Sola app.

``before_install`` makes sure the Python requirements (yt-dlp + demucs) and the
system dependency (ffmpeg) are available so extraction works right after the app
is installed, without a manual ``pip install`` step.
"""

import os
import shutil
import subprocess
import sys

import frappe

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS_FILE = os.path.join(APP_ROOT, "requirements.txt")


def before_install():
	install_python_requirements()
	ensure_ffmpeg()


def after_install():
	ensure_ffmpeg()


def install_python_requirements():
	"""Pip-install everything listed in the app's requirements.txt."""
	if not os.path.exists(REQUIREMENTS_FILE):
		return

	frappe.logger("hees_sola").info("Installing Hees Sola Python requirements...")
	result = subprocess.run(
		[sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS_FILE],
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
	)
	if result.returncode != 0:
		frappe.throw(
			"Failed to install Hees Sola requirements.\n\n"
			f"{result.stdout[-4000:]}\n\n"
			f"Install them manually with: pip install -r {REQUIREMENTS_FILE}"
		)


def ensure_ffmpeg():
	"""ffmpeg is needed by yt-dlp to decode/convert the downloaded audio."""
	if shutil.which("ffmpeg"):
		return

	message = (
		"ffmpeg was not found on PATH. Vocal extraction needs it to process audio.<br><br>"
		"Install it with:<br><code>sudo apt-get install -y ffmpeg</code> (Debian/Ubuntu) "
		"or <code>brew install ffmpeg</code> (macOS)."
	)
	frappe.logger("hees_sola").warning("ffmpeg not found on PATH.")
	frappe.msgprint(message, title="Hees Sola: ffmpeg missing", indicator="orange")
