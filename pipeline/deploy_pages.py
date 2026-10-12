"""
Publish the web map to GitHub Pages.

Usage:  python pipeline/deploy_pages.py [name]     (default: england)
Needs:  the export from pipeline/export_web.py, and a git remote "origin"
Output: the gh-pages branch on origin, replaced with a single fresh commit each time, so ~80MB of
        tiles per rebuild doesn't pile up in the history. The code lives on main as usual.

Pages serves the PMTiles files as they are: the browser fetches byte ranges of them directly.
Don't put the tiles in Git LFS - Pages doesn't serve LFS files.
"""
import os, shutil, subprocess, sys, tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "england"
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(os.path.join(WEB, "index.html"), tmp)
        os.makedirs(os.path.join(tmp, "data"))
        shutil.copy(os.path.join(WEB, "data", f"{name}.json"), os.path.join(tmp, "data"))
        shutil.copytree(os.path.join(WEB, "tiles", name), os.path.join(tmp, "tiles", name))
        os.makedirs(os.path.join(tmp, "downloads"))
        for ext in ("gpx", "kml"):
            shutil.copy(os.path.join(WEB, "downloads", f"{name}_candidates.{ext}"), os.path.join(tmp, "downloads"))
        open(os.path.join(tmp, ".nojekyll"), "w").close()  # serve files as they are, no Jekyll build
        biggest = max((os.path.getsize(os.path.join(d, f)), f) for d, _, fs in os.walk(tmp) for f in fs)
        if biggest[0] > 95e6:
            sys.exit(f"{biggest[1]} is {biggest[0] / 1e6:.0f}MB: GitHub refuses files over 100MB")

        git("init", "-q", "-b", "gh-pages", cwd=tmp)
        git("add", "-A", cwd=tmp)
        git("commit", "-q", "-m", f"Map data for {name}, built {date.today().isoformat()}", cwd=tmp)
        git("push", "--force", remote, "gh-pages", cwd=tmp)
    print(f"Pushed to gh-pages on {remote}")


if __name__ == "__main__":
    main()
