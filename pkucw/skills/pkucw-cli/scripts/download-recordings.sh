#!/usr/bin/env bash
set -u

COURSE="${1:-}"
WORKDIR="${PKUCW_WORKDIR:-${HOME}/pkucw}"
MAX_ATTEMPTS="${PKUCW_DOWNLOAD_ATTEMPTS:-5}"

if [[ -z "${COURSE}" ]]; then
  echo "usage: download-recordings.sh <course-title-or-id>" >&2
  exit 2
fi

COURSE_DIR="${WORKDIR}/${COURSE}"
mkdir -p "${COURSE_DIR}"

if ! pkucw recordings list "${COURSE}" --json > "${COURSE_DIR}/recordings.json.tmp"; then
  rm -f "${COURSE_DIR}/recordings.json.tmp"
  echo "Failed to list recordings for ${COURSE}." >&2
  exit 1
fi
mv "${COURSE_DIR}/recordings.json.tmp" "${COURSE_DIR}/recordings.json"

failures=0
while IFS=$'\t' read -r recording_id title; do
  if [[ -s "${COURSE_DIR}/${title}.ts" ]]; then
    echo "Skipping existing recording ${recording_id}: ${title}"
    continue
  fi
  attempt=1
  downloaded=0
  while (( attempt <= MAX_ATTEMPTS )); do
    if pkucw recordings download "${COURSE}" "${recording_id}" \
      --output-dir "${COURSE_DIR}" --no-remux --no-progress --json; then
      downloaded=1
      break
    fi
    attempt=$((attempt + 1))
    sleep 10
  done
  if (( downloaded == 0 )); then
    echo "Failed to download recording ${recording_id} after ${MAX_ATTEMPTS} attempts." >&2
    failures=$((failures + 1))
  fi
done < <(jq -r '.payload.recordings[] | [.id,.title] | @tsv' "${COURSE_DIR}/recordings.json")

{
  printf 'file\tsize\n'
  find "${COURSE_DIR}" -maxdepth 1 -type f -name '*.ts' -print0 |
    sort -z |
    while IFS= read -r -d '' file; do
      printf '%s\t%s\n' "$(basename "${file}")" "$(du -h "${file}" | awk '{print $1}')"
    done
} > "${COURSE_DIR}/download-manifest.tsv"

(( failures == 0 ))
