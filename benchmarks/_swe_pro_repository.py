"""Select a bounded evidence excerpt from the public repository on the cached grader image."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


SOURCE_EXTENSIONS = frozenset(
    ".py .js .mjs .cjs .ts .tsx .jsx .go .rs .java .c .cc .cpp .h .hpp .cs .rb "
    ".sh .toml .json .yaml .yml .css .scss .html .sql .xml .kt .proto .vue".split()
)
EXCLUDED_DIRS = {"vendor", "node_modules", "dist", "build", "fixtures", "__snapshots__", "testdata",
                 "tests", "__tests__"}


def terms(text):
    return {s.lower() for s in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", text)
            if len(s) >= 3}


def source_path(path):
    p = PurePosixPath(path)
    return (p.suffix.lower() in SOURCE_EXTENSIONS
            and not any(part.startswith(".") or part in EXCLUDED_DIRS for part in p.parts)
            and not any(ord(c) < 32 for c in path)
            and not p.name.endswith((".min.js", ".min.css", ".lock", "-lock.json"))
            and p.name.lower() not in {"credentials.json", "credentials.yaml", "secrets.yaml", "secrets.json"})


def select(root, query, limit):
    git = ["git", "-c", "safe.directory=" + str(root), "-C", str(root)]
    head = subprocess.check_output(git + ["rev-parse", "--verify", "HEAD"]).decode("ascii").strip()
    tree = subprocess.check_output(git + ["ls-tree", "-r", "-l", "-z", "--full-tree", head])
    symbols = list(dict.fromkeys(re.findall(r"\b[A-Za-z]\w{2,63}\b", query)))
    symbols = [s for s in symbols if "_" in s or re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z]", s)][:24]
    matches = set()
    if symbols:
        command = ["grep", "-I", "-F", "-l", "-z"]
        for sym in symbols:
            command.extend(["-e", sym])
        grep = subprocess.run(git + command + [head, "--"], stdout=subprocess.PIPE)
        if grep.returncode not in (0, 1):
            raise RuntimeError("Cannot search the cached source tree")
        matches = {x.partition(b":")[2] for x in grep.stdout.split(b"\0") if x}
    candidates = []
    query_terms = terms(query)
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        attributes, filename = entry.split(b"\t", 1)
        mode, kind, oid, size = attributes.split()
        path = filename.decode("utf-8", "replace")
        if kind != b"blob" or mode not in (b"100644", b"100755") or not source_path(path):
            continue
        if not 0 < int(size) <= 1024 * 1024:
            continue
        name = PurePosixPath(path).name
        score = (80 * (path in query) + 40 * (name in query) + 15 * (filename in matches)
                 + 3 * len(query_terms & terms(name)) + len(query_terms & terms(path)))
        candidates.append((-score, path, oid))
    chosen = []
    available = limit
    for _, path, oid in sorted(candidates)[:16]:
        if available < 160 or len(chosen) == 4:
            break
        data = subprocess.check_output(git + ["cat-file", "blob", oid.decode("ascii")])
        if b"\0" in data or b"PRIVATE KEY-----" in data:
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        lines = content.splitlines(keepends=True)
        if not lines:
            continue
        # Spend at most half of the initial budget on one file for multi-file repairs.
        budget = min(available, max(160, limit // 2))
        first = 0
        if len(data) > budget and symbols:
            hit = next((i for i, line in enumerate(lines) if any(s in line for s in symbols)), 0)
            first = max(0, hit - 4)
        sample = b""
        for line in lines[first:]:
            encoded = line.encode()
            if len(sample) + len(encoded) > budget:
                break
            sample += encoded
        if not sample.strip():
            continue
        chosen.append({"path": path, "start_line": first + 1, "sha256": hashlib.sha256(data).hexdigest(),
                       "content": sample.decode("utf-8")})
        available -= len(sample)
    return {"head": head, "files": chosen}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    root = next((p for p in (Path("/app"), Path("/testbed")) if (p / ".git").exists()), None)
    if root is None:
        raise RuntimeError("No Git repository at /app or /testbed")
    print(json.dumps(select(root, request["instruction"], request["limit"])))
