---
name: pkucw-cli
description: "Operate PKU CourseWeb through the pkucw CLI: manage accounts and sessions, resolve courses, inspect announcements and assignments, download teaching contents, and reliably download classroom recordings. Use for requests involving pkucw, course.pku.edu.cn, 北大教学网, CourseWeb, 教学内容, 作业, 课堂实录, or 课程回放."
---

# pkucw CLI

Use `pkucw` for CourseWeb operations. Run `pkucw --help` or a subcommand's `--help` when unsure about an argument shape. Prefer structured reads with `--json` after the subcommand:

```bash
pkucw ls --json
pkucw contents tree --course "课程名" --json
```

Do not use `pkucw --json ls`.

## Reliable Workflow

1. Check authentication and saved accounts:

   ```bash
   pkucw status --json
   pkucw accounts list --json
   ```

2. Resolve an exact course before bulk or write operations:

   ```bash
   pkucw ls --json > courses.json
   jq -r '.payload.courses[] | [.id,.name,.term,.status] | @tsv' courses.json
   ```

3. Save a structured resource list before downloading.
4. Operate on stable resource IDs from the saved JSON.
5. Verify downloaded file counts, sizes, and output paths before reporting completion.

## Accounts And Sessions

`pkucw` maintains one active browser session. Logging into another account replaces the active session and may change the default account.

Prefer saved accounts:

```bash
pkucw accounts use "<username-or-label>" --json
pkucw login --account "<username-or-label>" --json
```

When using `--password-stdin`, provide EOF; a newline alone leaves the command waiting:

```bash
printf '%s' "$PASSWORD" | pkucw login --username "$USERNAME" --password-stdin --json
```

Never store passwords in repository files, logs, or generated manifests.

## Courses And Read Operations

```bash
pkucw ls --current --json
pkucw use "<exact-course-name>" --json
pkucw current --json
pkucw info --course "<course>" --json
pkucw announcements list --course "<course>" --json
pkucw announcements show --course "<course>" "<announcement>" --json
pkucw assignments list --course "<course>" --json
pkucw assignments show --course "<course>" "<assignment>" --json
```

## Download All Teaching Contents

Use the bundled script:

```bash
skills/pkucw-cli/scripts/download-contents.sh "<course>"
```

It saves the complete content tree, downloads items with a direct `download_url`, and records text entries and external links separately. Do not claim external links were downloaded.

## Download All Classroom Recordings

Use the bundled script:

```bash
skills/pkucw-cli/scripts/download-recordings.sh "<course>"
```

Prefer `--no-remux` for reliable bulk downloads. The downloader preserves `.ts.part` and `.ts.part.json` files for resume; rerun the same recording ID after interruption.

Keep long-running execution sessions alive and poll progress. Do not assume a detached process will survive the execution host.

## Assignment Safety

Reading and downloading assignments is safe. Treat writes as high risk:

- Do not use `--save-draft` or `--final-submit` without explicit user authorization.
- Require explicit confirmation immediately before final submission.
- Pass upload paths directly with `--file`; do not modify source files.

## Failure Handling

- Write metadata to a temporary file, then rename it after a successful command. Direct shell redirection can leave an empty canonical JSON file after failure.
- Retry transient network failures using the same stable resource ID.
- Preserve recording `.part` and checkpoint files so retries resume.
- Progress may be written to stderr even with `--json`; use the final JSON result plus local file verification.
- Never preserve transient playback URLs or authentication tokens in reusable references.

## Bundled Scripts

- `scripts/download-contents.sh`: download all direct teaching-content files and save a manifest.
- `scripts/download-recordings.sh`: download all classroom recordings with retries and manifests.
