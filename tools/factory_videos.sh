#!/bin/bash
# Демо-камеры только из заводских съёмок: Pexels (Pexels License — бесплатно, с правом изменения) и Mixkit
# (Mixkit Free License). С Pexels берём первые 18 с каждого ролика сразу в 720p — целиком 4K-файлы не качаем.
# Падения — UR Fall Detection Dataset (настоящие падения с разметкой; на стоках падений на производстве нет).
#   bash tools/factory_videos.sh            # на сервере, после bootstrap_vast.sh
set -e
V=/workspace/linq/data/videos
cd $V
mkdir -p raw parts
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
FIT="scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1"

# id Pexels  обрезка (для вертикальных роликов — горизонтальная полоса по центру)  что в кадре
pexels() {
  local id=$1 crop=$2 out=parts/px_$1.mp4
  [ -s $out ] && return
  local url=$(curl -s -m 20 -A "$UA" -o /dev/null -w "%{redirect_url}" "https://www.pexels.com/download/video/$id/")
  nice -n 19 ffmpeg -loglevel error -y -t 18 -i "$url" -an -vf "${crop}${FIT}" -c:v libx264 -preset veryfast -crf 23 \
    -pix_fmt yuv420p $out && echo "pexels $id ок" || echo "pexels $id НЕ СКАЧАН"
}
VCROP="crop=iw:iw*9/16:0:(ih-iw*9/16)/2,"
while read id crop; do pexels $id "$([ "$crop" = v ] && echo "$VCROP")" & done <<'LIST'
5100046 -
27276888 -
3968782 -
10590044 -
32243654 -
32243653 -
31352805 -
3805926 -
39893762 -
12730657 v
38533947 -
37060862 v
5100044 -
5100041 -
31834970 -
LIST
wait

cam() { name=$1; shift; : > parts/$name.txt; for p in "$@"; do [ -s parts/$p ] && echo "file $p" >> parts/$name.txt; done
        ffmpeg -loglevel error -y -f concat -safe 0 -i parts/$name.txt -c copy "$name.mp4"; cp $name.mp4 parts/; echo "$name: $(grep -c file parts/$name.txt) роликов"; }
# цех металлообработки: каску снимают, шлифуют и варят без каски, станочный участок
cam cam_site px_5100046.mp4 px_27276888.mp4 px_3968782.mp4 px_10590044.mp4
# склад: погрузчики и люди в проездах
cam cam_warehouse px_32243654.mp4 px_32243653.mp4 px_31352805.mp4 mixkit_forklift_warehouse.mp4 mixkit_worker_driving_warehouse.mp4
# цех №2 — без изменений, всё снято на производстве
cam cam_shop mixkit_people_walking_warehouse.mp4 mixkit_engineers_factory.mp4 mixkit_worker_machine_corridor.mp4 mixkit_welding_factory.mp4
# АБК: настоящие падения UR Fall
cam cam_corridor urfall_01.mp4 urfall_02.mp4 urfall_03.mp4 urfall_04.mp4 urfall_05.mp4 urfall_06.mp4 urfall_07.mp4 urfall_08.mp4
# курение на производстве
cam cam_smoking px_3805926.mp4 px_39893762.mp4
# возгорание баллона на промплощадке, огневые работы резаком, пламя в цеху, сварка во дворе
cam cam_fire px_12730657.mp4 px_38533947.mp4 px_37060862.mp4 mixkit_welding_yard.mp4
# склад готовой продукции: разговор по телефону в проходе, бег по цеху
cam cam_hall px_5100044.mp4 px_5100041.mp4 px_31834970.mp4 mixkit_man_walking_warehouse.mp4
# длинная запись смены для режима «Разбор смены»: камеры вперемешку с обычной работой цехов (Mixkit)
cam smena_23-10_demo normal_22033.mp4 cam_site.mp4 normal_21795.mp4 normal_20770.mp4 cam_warehouse.mp4 normal_23550.mp4 normal_2244.mp4 \
  cam_shop.mp4 normal_23010.mp4 cam_corridor.mp4 normal_14785.mp4 normal_21950.mp4 cam_smoking.mp4 normal_23406.mp4 cam_fire.mp4 normal_23107.mp4 cam_hall.mp4
echo FACTORY_VIDEOS_DONE
