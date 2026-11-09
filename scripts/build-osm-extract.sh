#!/bin/sh
# Vercel build step: write the OpenStreetMap extract into the function bundle, or skip it.
#
# It does nothing unless MAKAN_OSM_BUILD_BBOX is set for the deployment, and it never fails the
# build: without an extract the site runs exactly as before, with no OpenStreetMap facts.
# The file stays inside the function and is never served, so no bulk extract is published.
# See "OpenStreetMap venue facts" in docs/DESIGN.md.
if [ -z "$MAKAN_OSM_BUILD_BBOX" ]; then
  echo "OpenStreetMap extract skipped: MAKAN_OSM_BUILD_BBOX is not set."
  exit 0
fi
for python in python python3; do
  if command -v "$python" >/dev/null 2>&1; then
    PYTHONPATH=src "$python" -m makan.places.osm_build --from-env --soft --out osm/places.parquet \
      || echo "OpenStreetMap extract not built: $python could not run the builder."
    exit 0
  fi
done
echo "OpenStreetMap extract not built: no Python on the build image."
exit 0
