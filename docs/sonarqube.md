# SonarQube (Local Analysis)

On-demand SonarQube analysis for this repository: `scripts/sonar.sh` starts an
ephemeral SonarQube container with Docker, runs the Python analysis, prints the
open-issue counts and leaves the server up so the issues can be browsed in the
UI. This is **not** a CI pipeline step — the analysis runs only when you invoke
the script.

## Why no quality gate

The server is ephemeral (no volume, no history), so SonarQube treats the
entire codebase as "new code" on every run. The default `Sonar way` quality
gate fails whenever any pre-existing issue exists, which means a gate verdict
would be a constant, information-free FAIL. The script therefore reports
**issue counts per severity** instead, and issue counts never affect the exit
code. If you want real "new code" deltas and a meaningful gate, you need a
persistent server — deliberately out of scope here.

## Prerequisites

- Docker daemon running (macOS Docker Desktop needs no extra sysctl; on bare
  Linux hosts raise `vm.max_map_count`).
- `sonar-scanner` on PATH: `brew install sonar-scanner`.
- `python3` on PATH (used to parse the SonarQube API responses).

## Usage

```bash
./scripts/sonar.sh                  # full analysis
./scripts/sonar.sh --rm             # remove the container when the run finishes
./scripts/sonar.sh --dry-run        # print the commands without executing them
./scripts/sonar.sh --help
```

What happens:

1. A SonarQube Community container (`sonarqube:26.7.0.124771-community`,
   pinned) is started on `127.0.0.1:9000` and polled until ready (timeout
   300s). A stale container from a previous run is replaced; a running one is
   reused as-is.
2. `src/` and `packages/sonata-tasks/src/` are analysed via the
   `sonar-scanner` CLI (static analysis; no coverage import).
3. The script prints the open-issue counts per severity for the project
   (`sonata-python`) plus the UI URL, and leaves the server running so the
   issue lists can be read in the browser.
4. The exit code is 0 when the analysis was submitted; it is non-zero on
   infrastructure errors (Docker, readiness, token, scanner) or a failed
   analysis.

## Caveats

- The first run downloads the image (~1-2 GB) and the server boot takes a few
  minutes; subsequent runs are faster (image cached, boot still ~1-3 min).
- Every run is a fresh server with `admin/admin` — a token is generated
  automatically, nothing to configure. If the token API rejects the default
  password, the script changes it via the API and retries; on a reused server
  with an already-changed password it prints a clear error and suggests
  `docker rm -f sonar-sonata` (the next run starts a fresh container and
  resets to `admin/admin`).
- The port guard uses `lsof` (macOS); on minimal Linux builds without `lsof`
  the check is skipped and Docker's own port error surfaces instead.
- Coverage is not imported (only static analysis); `tests/` is not analysed.

## Cleaning up

The server is left running by default so you can browse the issues. Remove it
any time with:

```bash
docker rm -f sonar-sonata
```

or pass `--rm` on the next run. State is ephemeral either way: no volume, no
history.
