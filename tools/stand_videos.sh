#!/bin/bash
# Видео камер на стенде: Pexels оттуда недоступен, берём Mixkit и UR Fall (падения).
cd ${LINQ_DATA:-$HOME/work/linq/data}/videos
export PATH=$HOME/work/linq/bin:$PATH
mkdir -p raw
FIT="scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1"
for id in 46753 31473 1459 45848 24127 23789 23551 23237 22032 23378 47755; do
  [ -s raw/mx_$id.mp4 ] || curl -s -f -m 120 -o raw/mx_$id.mp4 https://assets.mixkit.co/videos/$id/$id-720.mp4 &
done
for i in 01 02 03 04 05 06; do
  [ -s raw/urfall_$i.mp4 ] || curl -s -f -m 120 -o raw/urfall_$i.mp4 http://fenix.ur.edu.pl/~mkepski/ds/data/fall-$i-cam0.mp4 &
done
wait
cam() { out=$1; shift; args=(); n=0; f=""
  for c in "$@"; do [ -s raw/$c.mp4 ] || continue; args+=(-i raw/$c.mp4); f+="[$n:v]$FIT[v$n];"; n=$((n+1)); done
  [ $n -gt 0 ] || { echo "$out: нет роликов"; return; }
  for ((i=0;i<n;i++)); do f+="[v$i]"; done
  ffmpeg -loglevel error -y "${args[@]}" -filter_complex "${f}concat=n=$n:v=1:a=0[o]" -map "[o]" -c:v libx264 -preset veryfast -crf 24 -pix_fmt yuv420p $out.mp4 \
    && echo "$out: $n роликов"; }
cam cam_site mx_46753 mx_31473 mx_1459 &
cam cam_warehouse mx_45848 mx_24127 mx_23789 &
cam cam_shop mx_22032 mx_23378 &
cam cam_corridor urfall_01 urfall_02 urfall_03 urfall_04 urfall_05 urfall_06 &
cam cam_smoking mx_23237 mx_23551 &
cam cam_fire mx_47755 &
cam cam_hall mx_23551 mx_23237 &
wait
ls -la *.mp4
echo STAND_VIDEOS_DONE
