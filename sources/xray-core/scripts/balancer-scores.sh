#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs use jq variables in single quotes
# Prints the observatory measurements of Xray outbounds with the scores of one balancer,
# computed from the effective weights that RoutingService/GetBalancerInfo reports.
# cspell:ignore xray grpcurl argjson leastload leastlopi devi outbounds slurpfile
set -euo pipefail
prog="${0##*/}"
usage() {
  echo "usage: ${prog} [-s <host:port>] [-b <balancer tag>]"
}
server='127.0.0.1:19644' # DevSkim: ignore DS162092
balancer='vl-w'
while getopts ':s:b:h' opt; do
  case "${opt}" in
  s) server="${OPTARG}" ;;
  b) balancer="${OPTARG}" ;;
  h)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
  esac
done
shift "$((OPTIND - 1))"
if [[ "$#" -gt 0 ]]; then
  usage >&2
  exit 2
fi
for tool in grpcurl jq; do
  if ! command -v "${tool}" >/dev/null; then
    echo "${prog}: ${tool} is not installed" >&2
    exit 1
  fi
done
status="$(grpcurl -plaintext -emit-defaults -d '{}' "${server}" \
  xray.core.app.observatory.command.ObservatoryService/GetOutboundStatus)"
request="$(jq -cn --arg tag "${balancer}" '{tag: $tag}')"
info="$(grpcurl -plaintext -emit-defaults -d "${request}" "${server}" \
  xray.app.router.command.RoutingService/GetBalancerInfo)"
strategy="$(jq -r '.balancer.strategy // ""' <<<"${info}")"
costs="$(jq '.balancer.outboundCosts // [] | length' <<<"${info}")"
case "${strategy}" in
'')
  echo "${prog}: ${server} reports no strategy of balancer ${balancer}, its Xray API" \
    'has no balancer weights: scores are not computed' >&2
  ;;
leastload | leastlopi)
  if [[ "${costs}" -eq 0 ]]; then
    echo "${prog}: balancer ${balancer} reports no candidates with weights:" \
      'scores are not computed' >&2
  fi
  ;;
*)
  echo "${prog}: strategy ${strategy} of balancer ${balancer} has no weights:" \
    'scores are not computed' >&2
  ;;
esac
if [[ "$(jq '.status.status // [] | length' <<<"${status}")" -eq 0 ]]; then
  echo "${prog}: the observatory at ${server} reported no outbounds" >&2
fi
rows="$(jq -c --slurpfile info <(printf '%s' "${info}") --arg strategy "${strategy}" '
  ($info[0].balancer.outboundCosts // [] | map({key: .outboundTag, value: .})
    | from_entries) as $costs
  | (.status.status // [])
  | map(
      .healthPing as $hp
      | ($hp.all // 0 | tonumber) as $all
      | ($hp.fail // 0 | tonumber) as $fail
      | (.delay // 0 | tonumber) as $delay
      | (.alive // false) as $alive
      | (if $hp == null then $delay * 1e6 else $hp.average // 0 | tonumber end) as $avg
      | (if $hp == null then 0 else $hp.deviation // 0 | tonumber end) as $dev
      | $costs[.outboundTag] as $cost
      | (if $cost == null or ($alive | not) or $strategy != "leastload"
            or $cost.cost == null then null
          elif $hp == null then $avg * ($cost.cost | sqrt)
          else $dev * ($cost.cost | sqrt)
          end) as $load
      | . + {
          alive: $alive,
          all: $all,
          fail: $fail,
          avg_ns: $avg,
          overflow: ($load != null and $load >= 9223372036854775808),
          weights: (
            [("cost", "costPing", "costDevi") as $key
              | $cost[$key] // empty | " \($key)=\(.)"] | join("")
          ),
          score_ns: (
            if $cost == null or ($alive | not) then null
            elif $strategy == "leastload" then
              if $load == null then null else $load | floor end
            elif $strategy == "leastlopi" then
              if $cost.costPing == null or $cost.costDevi == null then null
              else $avg * ($cost.costPing | sqrt) + $dev * ($cost.costDevi | sqrt)
              end
            else null
            end
          )
        }
    )
  | sort_by([(.alive | not), (.score_ns == null), (.score_ns // 0), .avg_ns, .fail,
      (-.all), .outboundTag])
' <<<"${status}")"
if [[ "$(jq 'any(.[]; .overflow)' <<<"${rows}")" == true ]]; then
  echo "${prog}: a leastLoad score of balancer ${balancer} is out of the int64 range" \
    'of time.Duration: the server overflows it, so the order may differ from' \
    'the server one' >&2
fi
jq -r '
  def whole: tostring
    | capture("^(?<m>[0-9]*)\\.?(?<f>[0-9]*)(e\\+?(?<e>[0-9]+))?$")
    | (.e // "0" | tonumber) as $e
    | .m + .f + ([range($e - (.f | length)) | "0"] | join(""));
  def ms: (tonumber / 1000 | round | whole) as $d
    | (if ($d | length) < 4 then ("000" + $d)[-4:] else $d end) as $p
    | $p[:-3] + "." + $p[-3:];
  .[]
  | .healthPing as $hp
  | "\(.outboundTag) alive=\(.alive)"
    + (if .all > .fail then
        " avg=\($hp.average | ms)ms dev=\($hp.deviation | ms)ms"
        + " min=\($hp.min | ms)ms max=\($hp.max | ms)ms"
      elif .alive and $hp == null then
        " last=\((.delay | tonumber) * 1000000 | ms)ms"
      else
        " RTT=n/a"
      end)
    + (if .score_ns == null then " score=n/a" else " score=\(.score_ns | ms)ms" end)
    + (if $hp != null then " fail=\(.fail)/\(.all)" else "" end)
    + .weights
' <<<"${rows}"
