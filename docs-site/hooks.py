"""MkDocs hooks for the fprime-wsts site.

The reference documents in docs/ are the repository's own and link to files outside docs/
(the example, the tests, AGENTS.md) by relative path, which is right on GitHub. On the
site those files do not exist, so such links are pointed at the same file on GitHub.
"""

import os
import re

REPOSITORY = "https://github.com/vincewoo/fprime-wsts"
BRANCH = "main"

# An inline Markdown link or image target: ](target) or ](target "title")
LINK = re.compile(r"\]\((?P<target>[^)\s]+)(?P<title>\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(\s*)(```|~~~)")


def _rewrite(target, page_dir, docs_dir, repo_root):
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "/")):
        return None
    path, _, anchor = target.partition("#")
    if not path:
        return None
    resolved = os.path.normpath(os.path.join(page_dir, path))
    if os.path.commonpath([resolved, docs_dir]) == docs_dir:
        return None
    relative = os.path.relpath(resolved, repo_root).replace(os.sep, "/")
    if relative.startswith(".."):
        return None
    kind = "tree" if os.path.isdir(resolved) else "blob"
    url = f"{REPOSITORY}/{kind}/{BRANCH}/{relative}"
    return url + (f"#{anchor.lower()}" if anchor else "")


def on_page_markdown(markdown, page, config, files):
    docs_dir = os.path.abspath(config["docs_dir"])
    repo_root = os.path.dirname(os.path.abspath(config["config_file_path"]))
    page_dir = os.path.dirname(os.path.join(docs_dir, page.file.src_path))

    out, fence = [], None
    for line in markdown.split("\n"):
        match = FENCE.match(line)
        if match:
            marker = match.group(2)
            fence = None if fence == marker else (fence or marker)
            out.append(line)
            continue
        if fence is None:
            def replace(m):
                url = _rewrite(m.group("target"), page_dir, docs_dir, repo_root)
                if url is None:
                    return m.group(0)
                return f"]({url}{m.group('title') or ''})"
            line = LINK.sub(replace, line)
        out.append(line)
    return "\n".join(out)
