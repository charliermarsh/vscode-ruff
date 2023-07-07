###
# Bump the extension version.
#
# Usage:
#   ./scripts/bump_ruff_version.sh 0.0.274 0.0.277
###

set -euxo pipefail

FROM=$1
TO=$2

# Create a branch.
git checkout -B v$TO main

# Update the version in-place.
rg $FROM --files-with-matches | xargs sed -i "" "s/$FROM/$TO/g"

# Re-lock dependencies.
rm requirements.txt
rm requirements-dev.txt
pip-compile --generate-hashes --resolver=backtracking -o ./requirements.txt ./pyproject.toml
pip-compile --generate-hashes --resolver=backtracking --upgrade --extra dev -o ./requirements-dev.txt ./pyproject.toml
npm install --package-lock-only

# Commit the change.
git add .
git commit -m "Bump Ruff version to $TO"

# Push.
git push origin HEAD