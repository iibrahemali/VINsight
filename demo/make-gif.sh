#!/usr/bin/env bash
# Speeds up each recorded scene (slow scenes more), joins them, and writes docs/demo.mp4 and docs/demo.gif.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../docs out/parts
# scene:speed, in playback order. Scenes that weren't recorded are skipped.
# Inspector (the main feature) plays slower so the verdict and sources are readable; the rest is a quick montage.
SCENES="vin:1.1 listing:1.1 scout:1.8 coach:1.8 advisor:1.8 tour:1.4"
: > out/parts.txt
for entry in $SCENES; do
  name=${entry%%:*}; speed=${entry##*:}
  [ -f "out/${name}.webm" ] || continue
  ffmpeg -y -loglevel error -i "out/${name}.webm" -vf "setpts=PTS/${speed},fps=25,scale=1280:-2" -an \
    -c:v libx264 -pix_fmt yuv420p "out/parts/${name}.mp4"
  echo "file 'parts/${name}.mp4'" >> out/parts.txt
done
ffmpeg -y -loglevel error -f concat -safe 0 -i out/parts.txt -c copy -movflags +faststart ../docs/demo.mp4
ffmpeg -y -loglevel error -i ../docs/demo.mp4 -vf "fps=8,scale=840:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" ../docs/demo.gif
ls -lh ../docs/demo.mp4 ../docs/demo.gif
printf "length: %.1fs\n" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 ../docs/demo.mp4)"
