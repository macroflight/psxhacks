"""The read-only CB status map at /devel (frankenweather's own web UI only).

Everything frankenweather uses to place CBs and what it made of them, on one Leaflet map
with a switch per layer (remembered by the browser): the weather radar frame that decides,
radar coverage, storm cells as frankenweather decoded them (strength, size, motion,
lightning, satellite top, whether a new CB there would be seen, the planner's cluster and
score), MTG lightning, Meteosat cloud tops, SIGMET areas, the zones, the CBs PSX draws (echo
sizes, oktas, base and top, anvil, drift), what the crew may see (window and ND sectors), the
route and the planner's last moves; and a "zone changes" panel with the last 100 zone
decisions and their reasons (frankenweather's own log lines about them). Served by
frankenweather itself (no data on the PSX network); the radar, coverage and satellite
pictures load in the browser straight from RainViewer and EUMETSAT. No controls: status only.
"""
import asyncio
import json

_PAGE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>frankenweather CB map</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{margin:0;height:100%;background:#0f172a;color:#e2e8f0;font:13px system-ui,sans-serif}
#map{position:absolute;top:28px;bottom:0;left:0;right:0}
#bar{height:28px;line-height:28px;padding:0 10px;background:#1e293b}
#bar a{color:#93c5fd}
#ev{display:none;position:absolute;top:28px;right:0;bottom:0;width:min(640px,60vw);z-index:2000;
background:rgba(15,23,42,.96);border-left:1px solid #334155;padding:8px;overflow:auto}
#ev a{color:#93c5fd}
.evl{font:11px ui-monospace,monospace;padding:2px 0;border-bottom:1px solid #1e293b;
white-space:pre-wrap}
.evt{color:#94a3b8}
.lbl{background:rgba(15,23,42,.85);color:#f1f5f9;border:1px solid #334155;border-radius:4px;
padding:1px 4px;white-space:nowrap;font-size:11px}
.leaflet-tooltip{font-size:12px}
.leaflet-interactive:focus{outline:none}
.legend{background:rgba(15,23,42,.9);color:#e2e8f0;padding:6px 8px;border-radius:6px;
font-size:11px;line-height:1.5}
.sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;
vertical-align:middle}
</style></head><body>
<div id="bar"><b>frankenweather CB map</b> (read-only) &nbsp;
<span id="info">loading...</span> &nbsp; <a href="#" id="evbtn">zone changes</a> &nbsp;
<a href="#" id="whybtn">why no CB here?</a> &nbsp;
<a href="/weather">weather pages</a></div>
<div id="ev"><div style="margin-bottom:6px"><b>Zone changes</b> (newest first) &nbsp;
zone <select id="evzone"><option value="">all</option></select> &nbsp;
<a href="#" id="evclose">close</a></div><div id="evlist"></div></div>
<div id="map"></div>
<script>
// gentle mouse wheel zoom: twice as many wheel pixels per zoom level as the /weather map
var map=L.map('map',{zoomSnap:0.25,zoomDelta:0.5,wheelPxPerZoomLevel:240,wheelDebounceTime:60})
 .setView([50,0],6);
// a plain base: land, sea, coastlines and borders, and country/region names on top
var ESRI='https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/';
L.tileLayer(ESRI+'World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
 {attribution:'Tiles &copy; Esri',maxZoom:12}).addTo(map);
map.createPane('names');map.getPane('names').style.zIndex=450;
map.getPane('names').style.pointerEvents='none';
L.tileLayer(ESRI+'World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
 {pane:'names',maxZoom:12}).addTo(map);
var groups={
 'Radar picture':L.layerGroup(),'Radar coverage':L.layerGroup(),
 'Satellite cloud tops':L.layerGroup(),'Lightning':L.layerGroup(),
 'Storm cells':L.layerGroup(),'Weak cells (< 43 dBZ)':L.layerGroup(),
 'Planner clusters':L.layerGroup(),'SIGMETs':L.layerGroup(),'Zones':L.layerGroup(),
 'CBs (PSX)':L.layerGroup(),'CB echo (core/amber)':L.layerGroup(),
 'CB turbulence zone':L.layerGroup(),'CB anvil':L.layerGroup(),
 'CB drift':L.layerGroup(),'Crew view':L.layerGroup(),'Route':L.layerGroup(),
 'Aircraft':L.layerGroup()};
// at a glance: the radar picture, the CBs, the zones and the crew view; the rest is the why
var DEF_ON={'Radar picture':1,'CBs (PSX)':1,'CB echo (core/amber)':1,'CB turbulence zone':1,
 'CB anvil':1,'CB drift':1,'Zones':1,'Crew view':1,'Aircraft':1,'Route':1};
var saved=JSON.parse(localStorage.getItem('devmapLayers2')||'null');
Object.keys(groups).forEach(function(n){
 var on=saved&&n in saved?saved[n]:!!DEF_ON[n]; if(on)groups[n].addTo(map);});
L.control.layers(null,groups,{collapsed:false}).addTo(map);
function saveLayers(){var s={};Object.keys(groups).forEach(function(n){
 s[n]=map.hasLayer(groups[n]);});localStorage.setItem('devmapLayers2',JSON.stringify(s));}
map.on('overlayadd overlayremove',saveLayers);
var leg=L.control({position:'bottomleft'});
leg.onAdd=function(){var d=L.DomUtil.create('div','legend');d.innerHTML=
 '<b>Storm cells</b> (peak dBZ)<br>'+
 [['#9ca3af','< 43'],['#22c55e','43-44'],['#eab308','45-49'],['#f97316','50-54'],
  ['#ef4444','55-59'],['#d946ef','60+']].map(function(c){
  return '<span class="sw" style="background:'+c[0]+'"></span>'+c[1];}).join(' ')+
 '<br>dashed: made up (no radar) &middot; white ring: a new CB there would be seen<br>'+
 'grey arrow: motion in 1 h (dashed: the mean wind; the cell\'s own not trusted)<br>'+
 '<b>CBs</b>: red core, yellow echo (PSX), dashed white: turbulence zone, violet: anvil'+
 '<br>orange arrow: drift in 1 h &middot; faded/grey: decaying';return d;};
leg.addTo(map);
function dbzColor(d){return d<43?'#9ca3af':d<45?'#22c55e':d<50?'#eab308':d<55?'#f97316':
 d<60?'#ef4444':'#d946ef';}
function dest(lat,lon,brg,nm){var R=3440.065,d=nm/R,b=brg*Math.PI/180,
 la=lat*Math.PI/180,lo=lon*Math.PI/180;
 var la2=Math.asin(Math.sin(la)*Math.cos(d)+Math.cos(la)*Math.sin(d)*Math.cos(b));
 var lo2=lo+Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(la),Math.cos(d)-Math.sin(la)*Math.sin(la2));
 return [la2*180/Math.PI,lo2*180/Math.PI];}
function sector(lat,lon,hdg,deg,nm){var p=[[lat,lon]];
 for(var a=-deg;a<=deg;a+=5)p.push(dest(lat,lon,hdg+a,nm));p.push([lat,lon]);return p;}
function arrow(g,from,brg,nm,color,dash){var to=dest(from[0],from[1],brg,nm);
 L.polyline([from,to],{color:color,weight:2.5,dashArray:dash||null,interactive:false}).addTo(g);
 L.polyline([dest(to[0],to[1],brg+150,Math.min(nm/3,6)),to,
  dest(to[0],to[1],brg-150,Math.min(nm/3,6))],{color:color,weight:2.5,interactive:false}).addTo(g);}
var radarLayer=null,radarPath=null,covLayer=null,satLayer=null,satTime=null,fitted=false;
// the aircraft, as on the /weather map: nose up, turned to the heading
function acSvg(hdg){return '<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" '+
 'viewBox="-18 -18 36 36" style="transform:rotate('+hdg+'deg)">'+
 '<path d="M 0,-16 C 3.5,-13 4,-6 4,1 L 4,12 C 4,14 2,16 0,16 C -2,16 -4,14 -4,12 L -4,1 '+
 'C -4,-6 -3.5,-13 0,-16 Z" fill="#3b82f6" stroke="#0f172a" stroke-width="0.8"/>'+
 '<polygon points="-4,1 -17,8 -16,11 -4,6" fill="#3b82f6" stroke="#0f172a" stroke-width="0.8"/>'+
 '<polygon points="4,1 17,8 16,11 4,6" fill="#3b82f6" stroke="#0f172a" stroke-width="0.8"/>'+
 '<polygon points="-1.5,11 -7,15 -7,16.5 0,15 7,16.5 7,15 1.5,11" fill="#3b82f6"/></svg>';}
function hhmm(t){var d=new Date(typeof t==='number'?t*1000:t);
 return isNaN(d)?String(t):d.toISOString().slice(11,16)+'Z';}
function fl(ft){return ft==null?'?':'FL'+('00'+Math.round(ft/100)).slice(-3);}
function draw(d){
 Object.keys(groups).forEach(function(n){
  if(['Radar picture','Radar coverage','Satellite cloud tops'].indexOf(n)<0)groups[n].clearLayers();});
 if(!d.aircraft){document.getElementById('info').textContent='no aircraft position yet';return;}
 var ac=d.aircraft,v=d.view||{};
 if(!fitted){map.setView([ac.lat,ac.lon],6);fitted=true;}
 document.getElementById('info').textContent=new Date(d.time*1000).toISOString().slice(11,19)+
  'Z · '+(d.cells||[]).filter(function(c){return c.cb;}).length+' storm cells, '+
  (d.cbs||[]).length+' CBs · visible '+Math.round(v.visible_nm||0)+' nm, scan '+
  Math.round(v.scan_nm||0)+' nm'+(v.new_anywhere?' · start: new CBs anywhere':'')+
  (d.planner?' · planner on':'');
 // radar and coverage tiles (RainViewer, the frame frankenweather uses)
 if(d.radar&&d.radar.path&&d.radar.path!==radarPath){
  groups['Radar picture'].clearLayers();radarPath=d.radar.path;
  radarLayer=L.tileLayer(d.radar.tile_url+radarPath+'/256/{z}/{x}/{y}/2/1_1.png',
   {opacity:0.55,maxNativeZoom:7,maxZoom:12,attribution:'RainViewer'}).addTo(groups['Radar picture']);}
 if(d.radar&&!covLayer){covLayer=L.tileLayer(d.radar.tile_url+d.radar.coverage_path+
  '/256/{z}/{x}/{y}/0/0_0.png',{opacity:0.35,maxNativeZoom:7,maxZoom:12})
  .addTo(groups['Radar coverage']);}
 if(d.satellite&&d.satellite.time&&d.satellite.time!==satTime){
  groups['Satellite cloud tops'].clearLayers();satTime=d.satellite.time;
  satLayer=L.tileLayer.wms(d.satellite.wms,{layers:d.satellite.layer,format:'image/png',
   transparent:true,version:'1.3.0',time:satTime,opacity:0.55,attribution:'EUMETSAT'})
   .addTo(groups['Satellite cloud tops']);}
 // lightning
 (d.lightning||[]).forEach(function(p){L.circleMarker(p,{radius:2,color:'#fde047',weight:0,
  fillOpacity:0.8,interactive:false}).addTo(groups['Lightning']);});
 // storm cells and planner clusters
 var clusters={};
 (d.cells||[]).forEach(function(c){
  var g=c.cb?groups['Storm cells']:groups['Weak cells (< 43 dBZ)'];
  var r=Math.max(3,Math.sqrt(c.area_km2)/2);
  var m=L.circleMarker([c.lat,c.lon],{radius:r,color:c.in_view?'#ffffff':dbzColor(c.dbz),
   weight:c.in_view?2:1,fillColor:dbzColor(c.dbz),fillOpacity:0.6,
   dashArray:c.made_up?'3 3':null}).addTo(g);
  m.bindTooltip('<b>'+c.dbz+' dBZ</b>, '+c.area_km2+' km²'+(c.made_up?' (made up)':'')+
   '<br>lightning '+c.lightning+(c.top_ft?' · satellite top '+fl(c.top_ft):'')+
   (c.motion?'<br>moving '+('00'+c.motion[0]).slice(-3)+'\u00b0/'+c.motion[1]+' kt ('+
    (c.motion_own?'its own tracked motion':'the 850-300 hPa mean wind')+')':'')+
   (c.motion_raw&&!c.motion_own?'<br>tracker said '+('00'+c.motion_raw[0]).slice(-3)+
    '\u00b0/'+c.motion_raw[1]+' kt (not used)':'')+
   (c.score!=null?'<br>planner score '+c.score+(c.cluster?' (cluster '+c.cluster.slice(-12)+')':''):''));
  // the motion frankenweather uses, in an hour: solid its own (tracked, plausible), dashed
  // the mean wind instead
  if(c.motion&&c.motion[1]>0)arrow(g,[c.lat,c.lon],c.motion[0],c.motion[1],'#94a3b8',
   c.motion_own?null:'4 4');
  if(c.cluster){(clusters[c.cluster]=clusters[c.cluster]||[]).push([c.lat,c.lon]);}
 });
 Object.keys(clusters).forEach(function(k){var pts=clusters[k];if(pts.length<2)return;
  var la=0,lo=0;pts.forEach(function(p){la+=p[0];lo+=p[1];});la/=pts.length;lo/=pts.length;
  var rmax=0;pts.forEach(function(p){rmax=Math.max(rmax,map.distance([la,lo],p));});
  L.circle([la,lo],{radius:rmax+15000,color:'#38bdf8',weight:1,dashArray:'4 4',fill:false,
   interactive:false}).addTo(groups['Planner clusters']);});
 (d.planner&&d.planner.moves||[]).forEach(function(mv){if(mv.storm)
  L.circleMarker([mv.lat,mv.lon],{radius:14,color:'#38bdf8',weight:2,fill:false})
   .bindTooltip('planner: zone '+mv.zone+' here (score '+mv.score+')').addTo(groups['Planner clusters']);});
 // SIGMETs
 (d.sigmets||[]).forEach(function(s){if(!s.polygon)return;
  var t='<b>'+(s.hazard||'')+' SIGMET</b> '+(s.fir||'')+' '+(s.series||'')+' '+(s.qualifier||'')+
   '<br>'+fl(s.base_ft||0)+' - '+fl(s.top_ft)+(s.valid_from?'<br>valid '+
   hhmm(s.valid_from)+' - '+hhmm(s.valid_to):'')+
   (s.used_by?'<br>used by '+s.used_by:'')+(s.raw?'<pre style="white-space:pre-wrap;'+
   'max-width:420px;font-size:11px">'+s.raw.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</pre>':'');
  L.polygon(s.polygon,{color:s.hazard==='TS'||s.hazard==='TC'?'#f472b6':'#a78bfa',weight:1.5,
   fillOpacity:0.08}).bindPopup(t,{maxWidth:460}).addTo(groups['SIGMETs']);});
 // zones
 (d.zones||[]).forEach(function(z){
  var flags=[z.anchor?'anchor':'',z.airport?'airport':'',z.focused?'aircraft is here':'',
   z.cb_free?'CB-free':'',z.visible?'CB visible to the crew':'',
   z.kept_cb_free_in_view?'no new CB (in view)':'',z.dead_storm_kept?'dead storm kept':'']
   .filter(Boolean).join(', ');
  var tip='<b>Zone '+z.zone+'</b> '+z.icao+(z.oktas?' \u00b7 CB '+z.oktas+'/8':' \u00b7 no CB')+
   (flags?'<br>'+flags:'')+'<br>'+(z.placement||'');
  L.circleMarker([z.lat,z.lon],{radius:5,color:z.anchor||z.airport||z.focused?'#38bdf8':'#0f172a',
   weight:2,fillColor:z.oktas?'#f8fafc':'#94a3b8',fillOpacity:0.9}).bindPopup(tip,{maxWidth:360})
   .addTo(groups['Zones']);
  L.marker([z.lat,z.lon],{icon:L.divIcon({className:'',iconSize:[0,0],iconAnchor:[-8,8],
   html:'<span class="lbl">WX'+z.zone+'</span>'})}).bindPopup(tip,{maxWidth:360})
   .addTo(groups['Zones']);});
 // CBs, each with why it is where it is (its zone, its storm)
 var zoneOf={},cellOf={};
 (d.zones||[]).forEach(function(z){zoneOf[z.zone]=z;});
 (d.cells||[]).forEach(function(c){cellOf[c.key]=c;});
 (d.cbs||[]).forEach(function(c){
  var dead=!!(zoneOf[c.zone]||{}).dead_storm_kept;var col=dead?'#94a3b8':'#f8fafc';
  var ct=(d.cb_turb||{})[c.zone];
  if(ct&&ct.core_nm)L.circle([c.lat,c.lon],{radius:(ct.core_nm+(ct.flank_nm||0))*1852,
   color:'#f8fafc',weight:1.5,dashArray:'5 5',fill:false,interactive:false})
   .addTo(groups['CB turbulence zone']);
  if(c.radar_amber_nm)L.circle([c.lat,c.lon],{radius:c.radar_amber_nm/2*1852,color:'#facc15',
   weight:1.5,fillColor:'#facc15',fillOpacity:dead?0.15:0.35,interactive:false})
   .addTo(groups['CB echo (core/amber)']);
  if(c.radar_core_nm)L.circle([c.lat,c.lon],{radius:c.radar_core_nm/2*1852,color:'#b91c1c',
   weight:1.5,fillColor:'#ef4444',fillOpacity:dead?0.4:0.9,interactive:false})
   .addTo(groups['CB echo (core/amber)']);
  var z=zoneOf[c.zone]||{},sc=z.storm?cellOf[z.storm]:null;
  var why='<b>'+c.id+'</b> zone '+c.zone+(c.source?' \u00b7 from '+c.source:'')+
   '<br>'+c.oktas+'/8, base '+c.base_ft_msl+' ft, top '+c.top_ft_msl_psx+' ft (PSX)'+
   (c.top_ft_msl_fw!==c.top_ft_msl_psx?', '+c.top_ft_msl_fw+' ft (real)':'')+
   ' <span style="color:#94a3b8">(above sea level; PSX\'s 44,999 ft limit is above the '+
   'zone\'s ground)</span><br>echo core '+c.radar_core_nm+' nm, amber '+c.radar_amber_nm+' nm'+
   (ct?'<br>turbulence to '+(ct.core_nm+(ct.flank_nm||0)).toFixed(0)+' nm':'')+
   (c.drift?'<br>drifting '+('00'+c.drift.hdg_true).slice(-3)+'\u00b0/'+c.drift.kt+' kt'+
    (c.drift.until?' until '+hhmm(c.drift.until):''):'')+
   '<hr style="border-color:#334155"><b>Zone placed:</b> '+(z.placement||'?')+
   '<br><b>CB from:</b> '+(sc?'a radar storm, '+sc.dbz+' dBZ, '+sc.area_km2+' km\u00b2'+
    (sc.made_up?' (made up: no radar here)':'')+', lightning '+sc.lightning+
    (sc.top_ft?', satellite top '+fl(sc.top_ft):'')+(sc.score!=null?', planner score '+sc.score:'')
    :z.cb_sigmet?'the '+z.cb_sigmet:'the zone\'s weather')+
   (z.weather?'<br><span style="color:#94a3b8">'+z.weather+'</span>':'')+
   (z.drifts?'':'<br>not drifting'+(z.airport?' (airport zone)':z.anchor?' (anchor zone)':''))+
   (z.dead_storm_kept?'<br>its storm is gone; kept while the crew may see it':'')+
   (z.visible?'<br>the crew may see it: never moved':'');
  L.marker([c.lat,c.lon],{icon:L.divIcon({className:'',iconSize:[0,0],html:
   '<span class="lbl" style="color:'+col+'">CB '+c.oktas+'/8 '+fl(c.base_ft_msl)+'-'+
   fl(c.top_ft_msl_psx)+(c.top_ft_msl_fw>c.top_ft_msl_psx?' ('+fl(c.top_ft_msl_fw)+')':'')+
   '</span>'})}).bindPopup(why,{maxWidth:360}).addTo(groups['CBs (PSX)']);
  if(c.radar_core_nm)L.circle([c.lat,c.lon],{radius:Math.max(c.radar_core_nm/2,3)*1852,
   opacity:0,fillOpacity:0}).bindPopup(why,{maxWidth:360}).addTo(groups['CBs (PSX)']);
  // the drift in an hour (at least 12 nm, to be seen); the CB message's drift: hdg_true, kt
  if(c.drift&&c.drift.kt>0)arrow(groups['CB drift'],[c.lat,c.lon],
   c.drift.hdg_true,Math.max(c.drift.kt,12),dead?'#94a3b8':'#f97316',dead?'6 4':null);
  if(c.anvil&&c.anvil.length_nm){var a=c.anvil,w=[[c.lat,c.lon]];
   for(var k=-20;k<=20;k+=5)w.push(dest(c.lat,c.lon,a.hdg_true+k,a.length_nm));
   L.polygon(w,{color:'#a78bfa',weight:1.5,fillColor:'#a78bfa',fillOpacity:0.3})
    .bindTooltip('anvil: '+a.length_nm+' nm toward '+('00'+a.hdg_true).slice(-3)+'\u00b0')
    .addTo(groups['CB anvil']);}
 });
 // crew view
 if(v.window_nm)L.polygon(sector(ac.lat,ac.lon,ac.hdg,v.window_deg,v.window_nm),
  {color:'#22d3ee',weight:1,fillOpacity:0.04,interactive:false}).addTo(groups['Crew view']);
 if(v.nd_nm)L.polygon(sector(ac.lat,ac.lon,ac.hdg,v.nd_deg,v.nd_nm),
  {color:'#4ade80',weight:1,dashArray:'6 4',fill:false,interactive:false}).addTo(groups['Crew view']);
 if(v.scan_nm)L.circle([ac.lat,ac.lon],{radius:v.scan_nm*1852,color:'#64748b',weight:1,
  dashArray:'2 6',fill:false,interactive:false}).addTo(groups['Crew view']);
 if(d.route)L.polyline(d.route,{color:'#c084fc',weight:2}).addTo(groups['Route']);
 L.marker([ac.lat,ac.lon],{icon:L.divIcon({className:'',iconSize:[44,44],iconAnchor:[22,22],
  html:acSvg(ac.hdg)})})
  .bindTooltip(Math.round(ac.alt_ft||0)+' ft, heading '+Math.round(ac.hdg)+
   (ac.maneuvering?' (holding/vectored)':'')).addTo(groups['Aircraft']);
}
var lastEvents=[];
function showEvents(){var z=document.getElementById('evzone').value;
 var rows=lastEvents.slice().reverse().filter(function(e){
  return !z||new RegExp('Zone '+z+'\\b').test(e[1]);});
 document.getElementById('evlist').innerHTML=rows.map(function(e){
  return '<div class="evl"><span class="evt">'+new Date(e[0]*1000).toISOString().slice(11,19)+
   'Z</span> '+e[1].replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</div>';}).join('')||'none yet';}
document.getElementById('evbtn').onclick=function(e){e.preventDefault();
 var p=document.getElementById('ev');p.style.display=p.style.display==='block'?'none':'block';
 showEvents();};
document.getElementById('evclose').onclick=function(e){e.preventDefault();
 document.getElementById('ev').style.display='none';};
document.getElementById('evzone').onchange=showEvents;
(function(){var s=document.getElementById('evzone');for(var i=1;i<=7;i++){
 var o=document.createElement('option');o.value=o.textContent=i;s.appendChild(o);}})();
// "why no CB here?": the next click on the map asks frankenweather
var whyMode=false;
document.getElementById('whybtn').onclick=function(e){e.preventDefault();whyMode=!whyMode;
 this.style.fontWeight=whyMode?'bold':'normal';
 document.getElementById('map').style.cursor=whyMode?'crosshair':'';};
map.on('click',function(e){if(!whyMode)return;whyMode=false;
 document.getElementById('whybtn').style.fontWeight='normal';
 document.getElementById('map').style.cursor='';
 var p=L.popup({maxWidth:420}).setLatLng(e.latlng).setContent('asking frankenweather...').openOn(map);
 fetch('/devel/whynot?lat='+e.latlng.lat.toFixed(4)+'&lon='+e.latlng.lng.toFixed(4))
  .then(function(r){return r.json();}).then(function(d){
   p.setContent('<b>Why no CB here?</b><ul style="margin:4px 0;padding-left:16px">'+
    d.lines.map(function(l){return '<li>'+l.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</li>';})
    .join('')+'</ul>');}).catch(function(err){p.setContent('no answer: '+err);});});
function refresh(){fetch('/devel/data.json').then(function(r){return r.json();}).then(function(d){
 draw(d);lastEvents=d.events||[];
 if(document.getElementById('ev').style.display==='block')showEvents();})
 .catch(function(e){document.getElementById('info').textContent='no data: '+e;});}
refresh();setInterval(refresh,15000);
</script></body></html>
"""


def register_routes(routes, data_fn, why_not_fn) -> None:
    """Add GET /devel, /devel/data.json and /devel/whynot?lat=&lon= (worker threads)."""
    from aiohttp import web  # pylint: disable=import-outside-toplevel

    @routes.get('/devel')
    async def _page(_):
        return web.Response(text=_PAGE, content_type='text/html')

    @routes.get('/devel/data.json')
    async def _data(_):
        data = await asyncio.get_running_loop().run_in_executor(None, data_fn)
        return web.Response(text=json.dumps(data, separators=(',', ':'), default=str),
                            content_type='application/json')

    @routes.get('/devel/whynot')
    async def _why_not(request):
        try:
            lat, lon = float(request.query['lat']), float(request.query['lon'])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest(text='lat and lon wanted')  # pylint: disable=raise-missing-from
        data = await asyncio.get_running_loop().run_in_executor(None, why_not_fn, lat, lon)
        return web.Response(text=json.dumps(data, default=str), content_type='application/json')
