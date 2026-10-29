#!/usr/bin/env bash
# Build backend/dist/lambda.zip for AWS Lambda (python3.12, arm64).
# Runtime dependencies only, as manylinux_2_28 aarch64 wheels (Lambda python3.12 = AL2023,
# glibc 2.34): no Docker needed.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf dist/build && mkdir -p dist/build
uv export --frozen --no-dev --no-hashes --no-emit-project > dist/requirements.txt
uv pip install \
  --quiet \
  --requirements dist/requirements.txt \
  --target dist/build \
  --python-platform aarch64-manylinux_2_28 \
  --python-version 3.12 \
  --only-binary :all:

# Keep botocore API models only for services the API calls (saves ~20 MB unzipped).
KEEP="bedrock-runtime bedrock lambda ssm s3 sts sso sso-oidc"
for svc in dist/build/botocore/data/*/; do
  name=$(basename "$svc")
  [[ " $KEEP " == *" $name "* ]] || rm -rf "$svc"
done
find dist/build -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +

cp -R app dist/build/app
# Ingestion/eval tooling and tests are operator-side only and are not shipped.
rm -f dist/lambda.zip
(cd dist/build && zip -qr9 ../lambda.zip .)
echo "dist/lambda.zip: $(du -h dist/lambda.zip | cut -f1) zipped, $(du -sh dist/build | cut -f1) unzipped (limit 250M)"
