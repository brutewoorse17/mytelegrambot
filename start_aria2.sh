#!/bin/bash
aria2c \
  --enable-rpc \
  --rpc-listen-all=true \
  --rpc-allow-origin-all=true \
  --rpc-listen-port=6800 \
  --dir=downloads \
  --max-connection-per-server=10 \
  --min-split-size=1M \
  --split=10 \
  --follow-torrent=mem \
  --seed-time=0 \
  --disable-ipv6