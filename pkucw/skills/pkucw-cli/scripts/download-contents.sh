#!/usr/bin/env bash
set -u

COURSE="${1:-}"
WORKDIR="${PKUCW_WORKDIR:-${HOME}/pkucw}"
MAX_ATTEMPTS="${PKUCW_DOWNLOAD_ATTEMPTS:-5}"

if [[ -z "${COURSE}" ]]; then
  echo "usage: download-contents.sh <course-title-or-id>" >&2
  exit 2
fi

BASE="${WORKDIR}/${COURSE}/contents"
OUT="${BASE}/files"
LOG="${BASE}/download.log"
TREE_TMP="${BASE}/contents-tree.json.tmp"

mkdir -p "${OUT}"
if ! pkucw contents tree --course "${COURSE}" --json > "${TREE_TMP}"; then
  rm -f "${TREE_TMP}"
  echo "Failed to list contents for ${COURSE}." >&2
  exit 1
fi
mv "${TREE_TMP}" "${BASE}/contents-tree.json"

jq -r '.payload.contents[] | select(.download_url != null) | .id' \
  "${BASE}/contents-tree.json" > "${BASE}/downloadable-ids.txt"
jq -r '.payload.contents[] | select(.download_url == null) | [.id,.type,.title,.url] | @tsv' \
  "${BASE}/contents-tree.json" > "${BASE}/other-items.tsv"

: > "${LOG}"
failures=0
while IFS= read -r content_id; do
  attempt=1
  downloaded=0
  while (( attempt <= MAX_ATTEMPTS )); do
    if pkucw contents download --course "${COURSE}" "${content_id}" \
      --output-dir "${OUT}" --json >> "${LOG}" 2>&1; then
      downloaded=1
      break
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  if (( downloaded == 0 )); then
    echo "Failed to download content ${content_id} after ${MAX_ATTEMPTS} attempts." | tee -a "${LOG}" >&2
    failures=$((failures + 1))
  fi
done < "${BASE}/downloadable-ids.txt"

find "${OUT}" -type f -print0 |
  sort -z |
  while IFS= read -r -d '' file; do basename "${file}"; done > "${BASE}/downloaded-files.txt"

(( failures == 0 ))
