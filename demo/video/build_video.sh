#!/usr/bin/env bash
# Compose the silent, captioned TVLens demo video from the recorded footage.
# No audio track at any stage. Output: tvlens-demo.mp4 (1920x1080, H.264, 30fps).
#
# Prereq: raw/walkthrough-raw.webm exists (see record_walkthrough.py). Run from
# anywhere; paths are resolved relative to this script.
set -euo pipefail
cd "$(dirname "$0")"

RAW="raw/walkthrough-raw.webm"
FB="/usr/share/fonts/noto/NotoSans-Black.ttf"    # headlines / wordmark
FM="/usr/share/fonts/noto/NotoSans-Medium.ttf"   # subtext
FA="/usr/share/fonts/liberation/LiberationSans-Regular.ttf"  # has the -> arrow glyph (Noto lacks U+2192)
BG="0x08080a"      # app darkest background
WHITE="0xf0ebe3"   # app --text-primary
AMBER="0xe89b2d"   # app --accent
mkdir -p build

# --- Title card (4.5s): TV(white)+LENS(amber) wordmark, amber rule, app tagline.
ffmpeg -y -loglevel error -f lavfi -i "color=c=${BG}:s=1920x1080:d=4.5:r=30" -filter_complex "
  drawtext=fontfile=${FB}:textfile=txt/wtv.txt:fontsize=150:fontcolor=${WHITE}:x=w/2-tw-58:y=350,
  drawtext=fontfile=${FB}:textfile=txt/wlens.txt:fontsize=150:fontcolor=${AMBER}:x=w/2-58:y=350,
  drawbox=x=(iw-360)/2:y=545:w=360:h=5:color=${AMBER}@0.9:t=fill,
  drawtext=fontfile=${FM}:textfile=txt/ttag.txt:fontsize=46:fontcolor=${WHITE}:x=(w-tw)/2:y=600,
  fade=t=in:st=0:d=0.4,fade=t=out:st=4.1:d=0.4,format=yuv420p
" -c:v libx264 -crf 18 -pix_fmt yuv420p -an build/title.mp4

# --- Closing card (4.5s): wordmark, honest builder line, site.
ffmpeg -y -loglevel error -f lavfi -i "color=c=${BG}:s=1920x1080:d=4.5:r=30" -filter_complex "
  drawtext=fontfile=${FB}:textfile=txt/wtv.txt:fontsize=130:fontcolor=${WHITE}:x=w/2-tw-50:y=340,
  drawtext=fontfile=${FB}:textfile=txt/wlens.txt:fontsize=130:fontcolor=${AMBER}:x=w/2-50:y=340,
  drawtext=fontfile=${FM}:textfile=txt/csub.txt:fontsize=44:fontcolor=${WHITE}:x=(w-tw)/2:y=545,
  drawtext=fontfile=${FA}:textfile=txt/curl.txt:fontsize=34:fontcolor=${AMBER}:x=(w-tw)/2:y=625,
  fade=t=in:st=0:d=0.4,fade=t=out:st=4.1:d=0.4,format=yuv420p
" -c:v libx264 -crf 18 -pix_fmt yuv420p -an build/closing.mp4

# --- Body: real footage with four timed caption bands (head + sub).
# band: full-width dark strip; head: white Black; sub: amber Medium.
band() { echo "drawbox=x=0:y=850:w=1920:h=230:color=${BG}@0.82:t=fill:enable='between(t,$1,$2)'"; }
head() { echo "drawtext=fontfile=${FB}:textfile=$3:fontsize=58:fontcolor=${WHITE}:x=(w-tw)/2:y=884:enable='between(t,$1,$2)'"; }
sub()  { echo "drawtext=fontfile=${FM}:textfile=$3:fontsize=36:fontcolor=${AMBER}:x=(w-tw)/2:y=968:enable='between(t,$1,$2)'"; }

FC="fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:${BG},"
FC+="$(band 0.6 5.2),$(head 0.6 5.2 txt/c1h.txt),$(sub 0.6 5.2 txt/c1s.txt),"
FC+="$(band 11.2 16.2),$(head 11.2 16.2 txt/c2h.txt),$(sub 11.2 16.2 txt/c2s.txt),"
FC+="$(band 17.8 23.2),$(head 17.8 23.2 txt/c3h.txt),$(sub 17.8 23.2 txt/c3s.txt),"
FC+="$(band 26.3 32.6),$(head 26.3 32.6 txt/c4h.txt),$(sub 26.3 32.6 txt/c4s.txt),"
FC+="format=yuv420p"

ffmpeg -y -loglevel error -i "${RAW}" -vf "${FC}" -c:v libx264 -crf 18 -pix_fmt yuv420p -r 30 -an build/body.mp4

# --- Stitch: title -> body -> closing with 0.5s crossfades. No audio anywhere.
BODY_DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 build/body.mp4)
OFF1=$(awk 'BEGIN{print 4.5-0.5}')
OFF2=$(awk -v b="$BODY_DUR" 'BEGIN{print 4.5-0.5 + b - 0.5}')
ffmpeg -y -loglevel error -i build/title.mp4 -i build/body.mp4 -i build/closing.mp4 -filter_complex "
  [0:v][1:v]xfade=transition=fade:duration=0.5:offset=${OFF1}[a];
  [a][2:v]xfade=transition=fade:duration=0.5:offset=${OFF2}[v]
" -map "[v]" -r 30 -c:v libx264 -crf 19 -pix_fmt yuv420p -movflags +faststart -an tvlens-demo.mp4

echo "built tvlens-demo.mp4"
