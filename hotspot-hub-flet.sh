#!/usr/bin/env bash
# Launch Hotspot Hub Flet UI dynamically
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "/usr/share/hotspot-hub/app/hotspot-hub-flet.py" ]; then
    exec /usr/bin/env python3 /usr/share/hotspot-hub/app/hotspot-hub-flet.py "$@"
elif [ -f "$DIR/app/hotspot-hub-flet.py" ]; then
    exec /usr/bin/env python3 "$DIR/app/hotspot-hub-flet.py" "$@"
elif [ -f "$DIR/../app/hotspot-hub-flet.py" ]; then
    exec /usr/bin/env python3 "$DIR/../app/hotspot-hub-flet.py" "$@"
else
    echo "Error: hotspot-hub-flet.py could not be located." >&2
    exit 1
fi
