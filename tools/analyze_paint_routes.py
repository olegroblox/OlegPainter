"""Compare benchmark tiles from a PNG saved by Paint; assemble frame viewer."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def rgb(path):
    image = Image.open(path).convert("RGBA")
    background = Image.new("RGBA", image.size, "white")
    return np.asarray(Image.alpha_composite(background, image).convert("RGB"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--canvas", type=Path, required=True)
    parser.add_argument("--origin-canvas", type=int, nargs=2, required=True)
    parser.add_argument("--screen-canvas-origin", type=int, nargs=2,
                        help="Physical screen origin of the saved canvas; use recorded regions for irregular layouts")
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--note", default="Изменение времени в одном прогоне ещё не доказывает ускорение. Сопоставляйте точность и повторы в таблице.")
    args = parser.parse_args()
    root = args.output
    canvas = rgb(args.canvas)
    results = []
    for path in sorted(root.glob("[0-9][0-9]/result.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item["result"] != "completed" or item["recording"]["errors"]:
            raise ValueError(f"Incomplete case: {path}")
        i = item["index"]
        expected = rgb(path.with_name("expected.png"))
        h, w = expected.shape[:2]
        x = args.origin_canvas[0] + (i % args.columns)*(w+32)
        y = args.origin_canvas[1] + (i // args.columns)*(h+32)
        if args.screen_canvas_origin is not None:
            x = item["region"][0] - args.screen_canvas_origin[0]
            y = item["region"][1] - args.screen_canvas_origin[1]
        actual = canvas[y:y+h, x:x+w].copy()
        if x < 4 or y < 4 or x+w+4 > canvas.shape[1] or y+h+4 > canvas.shape[0]:
            raise ValueError(f"Tile {i} and its margin do not fit")
        margin = np.any(canvas[y-4:y+h+4, x-4:x+w+4] != 255, axis=2).copy()
        margin[4:h+4, 4:w+4] = False
        if actual.shape != expected.shape:
            raise ValueError(f"Tile {i} does not fit: {actual.shape}")
        mismatch = np.any(expected != actual, axis=2)
        ink = np.any(expected != 255, axis=2)
        painted = np.any(actual != 255, axis=2)
        holes = ink & ~painted
        outside = ~ink & painted
        colour = mismatch & ink & painted
        delta = np.abs(expected.astype(np.int16)-actual.astype(np.int16))
        item["metrics"] = dict(exact_percent=float(100*(1-mismatch.mean())),
            ink_exact_percent=float(100*(1-mismatch[ink].mean())),
            missing_pixels=int(holes.sum()), extra_pixels=int(outside.sum()),
            wrong_colour_pixels=int(colour.sum()), different_pixels=int(mismatch.sum()),
            pixels=int(mismatch.size), ink_pixels=int(ink.sum()),
            mean_absolute_channel_error=float(delta.mean()),
            outside_tile_pixels=int(margin.sum()),
            within_3_percent=float(100*(delta.max(axis=2)<=3).mean()),
            canvas_origin=[x,y])
        difference = np.full_like(actual, 245)
        difference[holes] = (235, 40, 70)
        difference[outside] = (0, 170, 210)
        difference[colour] = (245, 145, 25)
        Image.fromarray(actual).save(path.with_name("actual.png"))
        Image.fromarray(difference).save(path.with_name("difference.png"))
        frames = json.loads((path.parent/"frames/frames.json").read_text(encoding="utf-8"))
        item["frames"] = frames
        item["frame_size"] = list(Image.open(path.parent/"frames"/frames[0]["file"]).size)
        item["image_size"] = [w,h]
        item.pop("configuration", None)
        results.append(item)
    (root/"comparison.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),encoding="utf-8")
    if not results:
        raise ValueError("No completed cases found")
    with (root/"comparison.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        fields = ["index","name","elapsed_seconds","exact_percent","ink_exact_percent",
                  "missing_pixels","extra_pixels","wrong_colour_pixels","different_pixels","outside_tile_pixels"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(dict(r, **r["metrics"]))
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    w,h = results[0]["image_size"]
    scale = min(2,256/w,306/h)
    thumb = (round(w*scale),round(h*scale))
    row_height = thumb[1]+68
    sheet = Image.new("RGB", (4*300, ((len(results)+3)//4)*row_height), "#f2f2f2")
    draw = ImageDraw.Draw(sheet)
    for k, r in enumerate(results):
        x,y=(k%4)*300,(k//4)*row_height
        im=Image.open(root/f"{r['index']:02d}/actual.png").resize(thumb,Image.Resampling.NEAREST)
        sheet.paste(im,(x+10,y+8))
        draw.text((x+10,y+thumb[1]+12), f"{r['index']:02d}  {r['name'][:26]}",font=font,fill="black")
        draw.text((x+10,y+thumb[1]+34),f"{r['elapsed_seconds']:.2f} s | {r['metrics']['exact_percent']:.2f}%",font=font,fill="black")
    sheet.save(root/"all-results.png")
    data=json.dumps(results,ensure_ascii=False).replace("</","<\\/")
    html='''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Paint — сравнение маршрутов 1 px</title>
<style>body{margin:30px;font:16px system-ui;background:#17191d;color:#eee}h1{font-size:26px}p{color:#bbc3cd;max-width:1100px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px;border-bottom:1px solid #3a3d43;text-align:left}tr{cursor:pointer}tr:hover,.selected{background:#353943}button,select,input{font:inherit}button,select{background:#343942;color:#fff;border:1px solid #647080;border-radius:6px;padding:8px}header{display:flex;gap:20px;align-items:center}.panels{display:flex;gap:18px;flex-wrap:wrap}.panel img{image-rendering:pixelated;width:384px;height:288px;background:white}.panel p{margin:8px 0}.card{background:#242830;padding:20px;border-radius:10px;margin:18px 0}input[type=range]{width:600px;max-width:70vw}small{color:#aeb8c7}.scroll{overflow-x:auto}pre{white-space:pre-wrap;font-size:12px}#stamp{font-variant-numeric:tabular-nums}</style>
<h1>Paint · кисть 1 px · сравнение порядка рисования</h1>
<p>Рисовал OlegPainter. Автоматическая кисть и исправление результата выключены. Все проценты рассчитаны после рисования по PNG, сохранённому самим Paint. «Точно» — полное равенство RGB, без допуска. «На фигуре» исключает белый фон. Кадры экрана служат только записью процесса и могут содержать курсор/экранный шум.</p>
<div class="card"><header><select id="choose"></select><button id="play">▶ Кадры</button><select id="speed"><option value="1">1×</option><option value="5" selected>5×</option><option value="10">10×</option></select><select id="zoom"><option value="3">3× масштаб</option><option value="5">5× масштаб</option><option value="8">8× масштаб</option></select></header>
<h2 id="title"></h2><div class="panels"><div class="panel"><p>Подготовленный результат</p><img id="expected"></div><div class="panel"><p>Реальный PNG из Paint</p><img id="actual"></div><div class="panel"><p>Ошибки: красный — пропуск, голубой — лишнее, оранжевый — цвет</p><img id="difference"></div><div class="panel"><p>Кадр процесса</p><img id="frame"></div></div>
<p><input id="slider" type="range" min="0" value="0"> <span id="stamp"></span></p><pre id="settings"></pre></div>
<p>Нажмите строку для просмотра. Изменение времени в одном прогоне ещё не доказывает ускорение. Плавность отдельно проверена с паузой длинного штриха 1 мс. Оптимизация переходов 2-opt/Or-opt не включается в доступных порядках «большие/маленькие»; ветка сложного маршрута обходится при включённом A*.</p>
<div class="scroll"><table><thead><tr><th>№</th><th>Настройки</th><th>Время, с</th><th>Точно, %</th><th>На фигуре, %</th><th>Белые пропуски</th><th>Лишние</th><th>Неверный цвет</th><th>Кадры</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>const data=DATA;let active=0,playing=false,start=0,origin=0;const $=id=>document.getElementById(id);
data.forEach((r,n)=>{let o=document.createElement('option');o.value=n;o.textContent=String(r.index).padStart(2,'0')+' · '+r.name;$('choose').append(o);let tr=document.createElement('tr');tr.onclick=()=>select(n);let m=r.metrics;[r.index,r.name,r.elapsed_seconds.toFixed(2),m.exact_percent.toFixed(3),m.ink_exact_percent.toFixed(3),m.missing_pixels,m.extra_pixels,m.wrong_colour_pixels,r.frames.length].forEach(v=>{let td=document.createElement('td');td.textContent=v;tr.append(td)});$('rows').append(tr)});
function show(){const r=data[active],f=r.frames[Number($('slider').value)];if(f){$('frame').src=String(r.index).padStart(2,'0')+'/frames/'+f.file;$('stamp').textContent=f.seconds.toFixed(2)+' с · кадр '+$('slider').value+'/'+(r.frames.length-1)}}
function select(n){playing=false;active=n;$('choose').value=n;const r=data[n],dir=String(r.index).padStart(2,'0')+'/';$('title').textContent=r.name+' — '+r.elapsed_seconds.toFixed(2)+' с';['expected','actual','difference'].forEach(id=>$(id).src=dir+id+'.png');$('slider').max=r.frames.length-1;$('slider').value=0;$('settings').textContent=JSON.stringify(r.settings,null,2);show();document.querySelectorAll('tbody tr').forEach((tr,k)=>tr.classList.toggle('selected',k===n))}
$('choose').onchange=()=>select(Number($('choose').value));$('slider').oninput=()=>{playing=false;show()};$('zoom').onchange=()=>document.querySelectorAll('.panel img').forEach(im=>{im.style.width=(128*Number($('zoom').value))+'px';im.style.height=(96*Number($('zoom').value))+'px'});
$('play').onclick=()=>{playing=!playing;start=performance.now();origin=data[active].frames[Number($('slider').value)].seconds;if(Number($('slider').value)==data[active].frames.length-1){origin=0;$('slider').value=0}};
function tick(now){if(playing){const frames=data[active].frames;let t=origin+(now-start)/1000*Number($('speed').value);let i=Number($('slider').value);while(i+1<frames.length&&frames[i+1].seconds<=t)i++;$('slider').value=i;show();if(i==frames.length-1)playing=false}requestAnimationFrame(tick)}select(0);requestAnimationFrame(tick);</script></html>'''
    html = html.replace('<h2 id="title">', '<p id="overview"></p><h2 id="title">')
    html = html.replace('<div class="card"><header>', '<p><a style="color:#8dccff" href="REPORT.md">Выводы и условия</a> · <a style="color:#8dccff" href="all-results.png">Все результаты</a> · <a style="color:#8dccff" href="comparison.csv">Скачать таблицу</a> · <a style="color:#8dccff" href="paint-canvas.png">Полный холст Paint</a></p><div class="card"><header>')
    import html as html_module
    start = html.index('<p>Нажмите строку для просмотра.')
    end = html.index('</p>', start) + 4
    html = html[:start] + '<p>Нажмите строку для просмотра. ' + html_module.escape(args.note) + '</p>' + html[end:]
    html = html.replace("if(playing){const frames", "$('play').textContent=playing?'⏸ Пауза':'▶ Кадры';if(playing){const frames")
    html = html.replace("$('settings').textContent=JSON.stringify(r.settings,null,2);", "$('settings').textContent=settingsText(r.settings);")
    html = html.replace("im.style.width=(128*Number($('zoom').value))+'px';im.style.height=(96*Number($('zoom').value))+'px'", "const size=im.id==='frame'?data[active].frame_size:[128,96];im.style.width=(size[0]*Number($('zoom').value))+'px';im.style.height=(size[1]*Number($('zoom').value))+'px'")
    html = html.replace("show();document.querySelectorAll", "show();$('zoom').onchange();document.querySelectorAll")
    additions = '''
const labels={run_length_merge_enabled:'Объединять прямые штрихи',astar_bridge_enabled:'Проводить переходы внутри цвета',euler_greedy_pairing_enabled:'Соединять сложные области одним маршрутом',area_order_2opt_enabled:'Сокращать переходы между областями',area_order_or_opt_enabled:'Дополнительно переставлять области',area_entry_exit_routing_enabled:'Подбирать точки входа и выхода',fill_route_polish_enabled:'Уточнять маршрут внутри области',snake_turn_minimize_enabled:'Уменьшать число поворотов',motion_profile_enabled:'Плавное изменение скорости',fill_traversal_mode:'Маршрут заполнения',tone_sequence:'Порядок цветов',area_sequence:'Порядок областей',area_fill_delay:'Пауза на пиксель длинного штриха'};
const values={auto:'Автоматически',gilbert:'Гильберт',fermat:'Ферма',dark_to_light:'От тёмных к светлым',light_to_dark:'От светлых к тёмным',large_to_small:'От больших к маленьким',small_to_large:'От маленьких к большим'};
function settingsText(s){return Object.entries(s).map(([k,v])=>(labels[k]||k)+': '+(typeof v==='boolean'?(v?'Включено':'Выключено'):k==='area_fill_delay'?(v*1000)+' мс':values[v]||v)).join('\\n')}
const best=data.reduce((a,b)=>b.metrics.exact_percent>a.metrics.exact_percent||b.metrics.exact_percent===a.metrics.exact_percent&&b.elapsed_seconds<a.elapsed_seconds?b:a);
$('overview').textContent=data.length+' прогонов · '+data.reduce((s,r)=>s+r.frames.length,0)+' кадров · лучший: №'+best.index+' — '+best.metrics.exact_percent.toFixed(3)+'%, '+best.elapsed_seconds.toFixed(2)+' с. Кадры записаны с шагом около 0,2 с; отдельные движения мыши между кадрами не видны.';
'''
    additions = additions.replace("const labels={", "const labels={draw_delay:'Пауза между движениями',")
    additions = additions.replace("const labels={", "const labels={pen_settle_delay:'Пауза после движения (переход к мазку)',")
    additions = additions.replace("const values={", "const values={nearest:'Ближайшие области',")
    additions = additions.replace("k==='area_fill_delay'?", "['area_fill_delay','draw_delay','pen_settle_delay'].includes(k)?")
    html = html.replace("data[active].frame_size:[128,96]", "data[active].frame_size:data[active].image_size")
    if w > 128 or h > 96:
        html = html.replace('<option value="3">3× масштаб</option>', '<option value="1">1× масштаб</option><option value="2">2× масштаб</option><option value="3">3× масштаб</option>')
    html = html.replace("select(0);requestAnimationFrame(tick);", additions+"select(data.indexOf(best));requestAnimationFrame(tick);")
    (root/"comparison.html").write_text(html.replace("DATA",data),encoding="utf-8")
    for r in sorted(results,key=lambda r:(-r["metrics"]["exact_percent"],r["elapsed_seconds"])):
        print(f"{r['index']:02d} {r['elapsed_seconds']:.2f}s {r['metrics']['exact_percent']:.3f}% {r['name']}")


if __name__ == "__main__":
    main()
