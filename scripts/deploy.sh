#!/bin/sh
# Deploy the current checkout to the running installation without interrupting an application.
#
#   scripts/deploy.sh            refuse if any application is queued, running or submitting
#   scripts/deploy.sh --wait     wait (up to 30 minutes) for the active applications to finish first
#
# Order: hold new dispatch -> confirm no application is active -> build -> recreate the app -> wait for health ->
# release the hold. The hold is always released, even when a step fails, so dispatch never stays stopped.
set -eu
cd "$(dirname "$0")/.."

psql() { docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' ; }
hold() {
  printf "update settings set value=(value::jsonb||jsonb_build_object('stage','%s','reason','%s','updated_at',now()))::json where key='execution_v2_deployment_hold';\n" "$1" "deploy $(git rev-parse --short HEAD)" | psql >/dev/null
}
active() {
  echo "select count(*) from runs where state in ('queued','running','submitting');" | psql
}

hold draining
trap 'hold complete; echo "deploy: hold released"' EXIT

waited=0
while [ "$(active)" != "0" ]; do
  if [ "${1:-}" != "--wait" ] || [ "$waited" -ge 1800 ]; then
    echo "deploy: $(active) application(s) still active; not deploying (use --wait to wait for them)" >&2
    exit 1
  fi
  sleep 15
  waited=$((waited + 15))
done

GIT_SHA=$(git rev-parse --short HEAD) docker compose build app
docker compose up -d --no-deps app
for _ in $(seq 1 80); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)" 2>/dev/null || echo starting)
  case "$state" in
    healthy) echo "deploy: $(git rev-parse --short HEAD) is healthy"; exit 0 ;;
    unhealthy) echo "deploy: the app is unhealthy; see docker compose logs app" >&2; exit 1 ;;
  esac
  sleep 3
done
echo "deploy: the app did not report healthy in 4 minutes" >&2
exit 1
