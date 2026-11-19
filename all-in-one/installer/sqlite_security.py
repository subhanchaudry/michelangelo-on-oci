"""Add the reviewed SQLite shared-library build to Python runtime images."""
from pathlib import Path
import re

ROOT = Path(__file__).parent
MARKER = ' AS sqlite-security-builder'


def apply(dockerfile, target):
    path = Path(dockerfile)
    original = path.read_text()
    stage = (ROOT / 'sqlite-security-stage.Dockerfile').read_text()
    if MARKER in original:
        if stage not in original:
            raise ValueError('SQLite security stage differs from reviewed input')
        return
    matches = list(re.finditer(r'^FROM [^\n]+ AS ' + re.escape(target) + r'$', original, re.M))
    if len(matches) != 1:
        raise ValueError('Expected one Python runtime stage: ' + target)
    match = matches[0]
    install = (
        '\nCOPY --from=sqlite-security-builder /tmp/libsqlite3-security.deb /tmp/libsqlite3-security.deb\n'
        'RUN dpkg -i /tmp/libsqlite3-security.deb && ldconfig && rm /tmp/libsqlite3-security.deb && '
        'python3 -c "import sqlite3; assert sqlite3.sqlite_version == \'3.54.1\'"\n'
    )
    result = original[:match.end()] + install + original[match.end():]
    first = re.search(r'^FROM ', result, re.M)
    if first is None:
        raise ValueError('Dockerfile has no build stage')
    path.write_text(result[:first.start()] + stage + '\n' + result[first.start():])
