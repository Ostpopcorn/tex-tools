"""Build the web app into a folder that can be served as a static site, e.g.,
with GitHub Pages. The web app runs textools in the browser with Pyodide,
which is downloaded from npm and served with the app.

    python web/build.py                 # build into web/dist
    python web/build.py --serve         # build and serve on http://localhost:8000
    python web/build.py --out _site     # build into another folder
"""
import argparse
import base64
import functools
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import tarfile
import urllib.request
import zipfile

PYODIDE_VERSION = "314.0.7"
PYODIDE_FILES = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm",
                 "python_stdlib.zip", "pyodide-lock.json"]
STATIC_FILES = ["index.html", "style.css", "app.js", "worker.js", "favicon.svg"]
EXAMPLES = ["example.tex"]

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(WEB_DIR)
PACKAGE_DIR = os.path.join(ROOT_DIR, "textools")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "textools-web")


def download_pyodide(version=PYODIDE_VERSION):
    """Return the npm package of Pyodide, which is cached."""
    path = os.path.join(CACHE_DIR, "pyodide-{}.tgz".format(version))
    if os.path.isfile(path):
        with open(path, "rb") as _file:
            return _file.read()
    url = "https://registry.npmjs.org/pyodide/{}".format(version)
    with urllib.request.urlopen(url) as response:
        dist = json.load(response)["dist"]
    print("Downloading", dist["tarball"])
    with urllib.request.urlopen(dist["tarball"]) as response:
        data = response.read()
    algorithm, digest = dist["integrity"].split("-", 1)
    if base64.b64encode(hashlib.new(algorithm, data).digest()).decode() != digest:
        raise RuntimeError("The download of Pyodide is corrupted")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as _file:
        _file.write(data)
    return data


def copy_pyodide(out_dir):
    target = os.path.join(out_dir, "pyodide")
    os.makedirs(target)
    with tarfile.open(fileobj=io.BytesIO(download_pyodide())) as archive:
        for name in PYODIDE_FILES:
            member = archive.extractfile("package/" + name)
            with open(os.path.join(target, name), "wb") as _file:
                shutil.copyfileobj(member, _file)


def get_version():
    with open(os.path.join(PACKAGE_DIR, "__init__.py"), encoding="utf-8") as _file:
        return re.search(r'__version__ = "(.*?)"', _file.read()).group(1)


def zip_package(out_dir):
    """Zip textools, which is pure Python without dependencies, so that the
    worker unpacks it into the site-packages of Pyodide. Return the zipped
    source, whose hash is the version of the zip file."""
    sources = {}
    for name in sorted(os.listdir(PACKAGE_DIR)):
        if name.endswith(".py"):
            with open(os.path.join(PACKAGE_DIR, name), encoding="utf-8") as _file:
                sources["textools/" + name] = _file.read()
    with zipfile.ZipFile(os.path.join(out_dir, "textools.zip"), "w",
                         zipfile.ZIP_DEFLATED) as archive:
        for name, source in sources.items():
            archive.writestr(name, source)
    return "".join(sources.values())


def _versioned(text, link, content):
    """Add a hash of the content to a link in the text, e.g., app.js?v=1a2b,
    so that browsers load the new file after an update instead of an old
    cached one, which may not fit the new index.html."""
    version = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
    if text.count(link) != 1:
        raise RuntimeError("Expected {} once to add its version".format(link))
    # the link ends with a quote, e.g., src="app.js"
    return text.replace(link, "{}?v={}{}".format(link[:-1], version, link[-1]))

def copy_static(out_dir, package):
    files = {}
    for name in STATIC_FILES:
        with open(os.path.join(WEB_DIR, name), encoding="utf-8") as _file:
            files[name] = _file.read()
    files["worker.js"] = _versioned(files["worker.js"], '"./textools.zip"',
                                    package)
    files["app.js"] = _versioned(files["app.js"], '"./worker.js"',
                                 files["worker.js"])
    files["index.html"] = _versioned(files["index.html"], 'href="style.css"',
                                     files["style.css"])
    files["index.html"] = _versioned(files["index.html"], 'src="app.js"',
                                     files["app.js"])
    for name, content in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8",
                  newline="\n") as _file:
            _file.write(content)


def build(out_dir):
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    package = zip_package(out_dir)
    copy_static(out_dir, package)
    shutil.copytree(os.path.join(WEB_DIR, "fonts"), os.path.join(out_dir, "fonts"))
    os.makedirs(os.path.join(out_dir, "examples"))
    for name in EXAMPLES:
        shutil.copy(os.path.join(ROOT_DIR, "examples", name),
                    os.path.join(out_dir, "examples"))
    copy_pyodide(out_dir)
    print("Built the web app with textools {} in {}".format(get_version(), out_dir))


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript",
                      ".wasm": "application/wasm", ".tex": "text/plain"}


def serve(out_dir, port):
    handler = functools.partial(Handler, directory=out_dir)
    with http.server.ThreadingHTTPServer(("localhost", port), handler) as server:
        print("Serving the web app on http://localhost:{}".format(port))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(WEB_DIR, "dist"),
                        help="Output folder (default: web/dist)")
    parser.add_argument("--serve", nargs="?", type=int, const=8000,
                        metavar="PORT", help="Serve the web app after building")
    args = parser.parse_args()
    build(args.out)
    if args.serve:
        serve(args.out, args.serve)


if __name__ == "__main__":
    main()
