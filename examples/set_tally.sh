#!/bin/sh
# Set text and tally of one display through the REST API of Tally Tapir, with curl.
#
#   ./set_tally.sh 3 "CAM 3" 1        UMD ID 3, text "CAM 3", right lamp red
#   ./set_tally.sh 3 "CAM 3" 0        the same display, all lamps off
#
# The third value is tally_bits, two bits per lamp:
#   right lamp: 1 red, 2 green, 3 amber
#   text:       4 red, 8 green, 12 amber
#   left lamp:  16 red, 32 green, 48 amber
# Add the values to light several lamps, for example 17 = left and right red.
#
# Set BASE to reach another machine, and add "-u USER" to CURL for an API login:
#   BASE=http://192.168.1.20:8088 CURL="curl -u robot" ./set_tally.sh 3 "CAM 3" 1

set -eu

BASE="${BASE:-http://localhost:8088}"
CURL="${CURL:-curl}"
ID="${1:?usage: set_tally.sh UMD_ID TEXT TALLY_BITS}"
TEXT="${2:?usage: set_tally.sh UMD_ID TEXT TALLY_BITS}"
BITS="${3:?usage: set_tally.sh UMD_ID TEXT TALLY_BITS}"

# Change the settings. With "send on change" this already sends when connected.
$CURL --fail --silent --show-error -X PATCH "$BASE/api/v1/sender" \
  -H 'Content-Type: application/json' \
  -d "{\"umd_id\": $ID, \"text\": \"$TEXT\", \"tally_bits\": $BITS}" > /dev/null

# Make sure the connection is open, then send once more for good measure.
$CURL --fail --silent --show-error -X POST "$BASE/api/v1/sender/connect" \
  -H 'Content-Type: application/json' -d '{}' > /dev/null
$CURL --fail --silent --show-error -X POST "$BASE/api/v1/sender/send" \
  -H 'Content-Type: application/json' -d '{}' > /dev/null

echo "Display $ID now shows \"$TEXT\" with tally bits $BITS."
