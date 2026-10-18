#!/bin/sh
# Install the HP KE200 filter + PPD and create a shared (AirPrint) CUPS queue.
# Usage: sudo ./install.sh
set -eu

cd "$(dirname "$0")"
QUEUE=HP_KE200

install -o root -g root -m 755 rastertohplabel.py /usr/lib/cups/filter/rastertohplabel
install -o root -g root -m 644 HP-KE200.ppd /usr/share/ppd/HP-KE200.ppd

URI=$(/usr/lib/cups/backend/usb 2>/dev/null | awk '/usb:\/\/HP\/KE200/ {print $2; exit}')
if [ -z "$URI" ]; then
	echo "HP KE200 not found on USB; is it plugged in and switched on?" >&2
	exit 1
fi

lpadmin -p "$QUEUE" -E -v "$URI" -P /usr/share/ppd/HP-KE200.ppd \
	-D "HP Label Printer" -L "$(hostname)" \
	-o printer-is-shared=true -o printer-error-policy=retry-current-job
lpadmin -d "$QUEUE"
# One call: cupsctl restarts CUPS, so a second call can hit it mid-restart.
# IdleExitTimeout=0 keeps CUPS running, so the AirPrint advert never disappears.
cupsctl --share-printers IdleExitTimeout=0

echo "Installed $QUEUE on $URI"
