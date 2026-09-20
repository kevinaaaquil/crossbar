"""Fixtures shared across the suite."""

import stat
import textwrap

import pytest


@pytest.fixture
def fake_docker(tmp_path):
    """A stub `docker` binary, so no daemon is needed.

    `exec` runs the command for real, which is what lets a test prove a server
    is reachable through the prefix. The two state hooks are the exception:
    they are shell scripts inside an image that does not exist here, so the
    stub answers for them in the shape the contract requires -- a path as the
    last line of stdout.
    """
    log = tmp_path / "docker.log"
    script = tmp_path / "docker"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            echo "$@" >> {log}
            case "$1" in
              run) echo "container$$" ;;
              cp)
                # `docker cp a b`: make the destination exist, so a caller that
                # reads it back is not fooled by a file the stub never wrote.
                shift
                dest=$2
                case "$dest" in
                  *:*) : ;;
                  *) mkdir -p "$(dirname "$dest")" && : > "$dest" ;;
                esac
                ;;
              exec)
                shift
                while [ "$1" = "-i" ] || [ "$1" = "-e" ] || [ "$1" = "-w" ]; do
                  if [ "$1" = "-e" ] || [ "$1" = "-w" ]; then shift; fi
                  shift
                done
                shift
                case "$1" in
                  *snapshot.sh) echo "snapshot of /app/db/library.db"
                                echo "/app/snapshots/snapshot.db" ;;
                  *restore.sh)  echo "/app/db/library.db" ;;
                  *) exec "$@" ;;
                esac
                ;;
              *) : ;;
            esac
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    class Handle:
        binary = str(script)

        @staticmethod
        def log_text():
            return log.read_text() if log.exists() else ""

    return Handle
