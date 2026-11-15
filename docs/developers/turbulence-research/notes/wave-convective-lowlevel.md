# Mountain-wave, convective and low-level turbulence as experienced by airliners

Research notes, 2026-10-26. About 17 search/fetch calls. Several primary pages could not be read: SKYbrary returned HTTP 429, the ATSB page timed out, and the AMS journal returned 403. Some numbers therefore rest on search-result excerpts of those pages, and each one is marked. Labels used below: **[measured]** means instrumented or recorded data; **[rule]** means operational guidance or a rule of thumb; **[model]** means numerical simulation.

## Q1. Mountain wave at cruise: vertical speeds, wavelengths, excursions, smooth vs breaking, rotors, SEV MTW criteria, notable cases

### Takeaway (1)

The operational boundary for "severe mountain wave" is a downdraft of 600 ft/min or more (about 3 m/s), or observed/forecast severe turbulence. A graded US scale puts moderate at 350–599 ft/min. Vertical currents in strong waves reach about 2,000 ft/min (about 10 m/s, rule-of-thumb level). At cruise the main airliner hazard is often not bumpiness. It is smooth, large wind and airspeed changes that push the aircraft toward overspeed or stall margins. Violent turbulence comes from wave breaking aloft and from rotors at low level.

### Cited Findings (1)

- **[rule]** Australia (ATSB): mountain waves are "severe" when the associated downdrafts exceed 600 ft/min and/or severe turbulence is observed or forecast. — [ATSB, Mountain wave and associated turbulence](https://www.atsb.gov.au/node/9936) (via search excerpt; the page fetch timed out)
- **[rule]** US NWS training slide: moderate mountain wave is 350–599 ft/min. Severe is >600 ft/min, together with an aircraft altitude change >1,000 ft. The slide says there are no "official" ICAO criteria for consistency. — [NWS Reno, D. Craft, Anticipating Winds Near Complex Terrain (2022)](https://www.weather.gov/media/rev/IWAWS/PDFs/2022/David%20Craft%20-%20Anticipating%20Winds%20Near%20Complex%20Terrain.pdf) (via search excerpt; the PDF fetch later returned 404)
- **[rule]** Vertical currents within mountain-wave oscillations can reach 2,000 ft/min. Strong vertical currents combined with surface friction can form rotors beneath the waves, which give severe turbulence. — [SKYbrary, Mountain Waves](https://skybrary.aero/articles/mountain-waves) (via search excerpt)
- **[rule]** FAA AC 00-6B (ch. 17): mountain waves are a form of mechanical turbulence above and downwind of mountains. They "often produce violent downdrafts" on the lee side, which at times exceed an aircraft's maximum climb rate. — [AC 00-6B copy hosted by EAA](https://chapters.eaa.org/-/media/491FD8F5F2DA4CEFB804C53E46900A87.ashx?sc_lang=en) (via search excerpt)
- **[rule]** The turbulence in and below rotor clouds is the most dangerous feature of mountain waves. — [NWS Honolulu, Mountainous Environment Hazards to Rotary Wing Ops (Gumeniuc)](https://www.weather.gov/media/hfo/hpaws/Mountainous%20Environment%20Hazards%20to%20Rotary%20Wing%20Operations%20-%20Vitalie%20Gumeniuc.pdf) (via search excerpt)
- **[rule]** In mountain wave the autopilot lets airspeed vary in order to hold altitude, so speed excursions are the main symptom. — [AOPA, Career pilot: Mountain waves (2021)](https://aopa.org/news-and-media/all-news/2021/august/flight-training-magazine/career-pilot-mountain-waves) (via search excerpt)
- **[measured / incident]** A340-600 ZS-SNF, Clariden, Switzerland, 6 Nov 2018, cruising at FL380:
  - An "unpredictable, rapid change in wind direction ... attributable to mountain wave effects" caused an overspeed warning.
  - The captain disconnected the autopilot and climbed steeply. The aircraft climbed above its cleared level, then had a brief loss of control in a high-speed descent to below the cleared level, with PAN and MAYDAY calls.
  - The wind change in kt and the excursion numbers were not retrieved.
  - [SKYbrary A346 Clariden 2018](https://skybrary.aero/accidents-and-incidents/a346-en-route-clariden-switzerland-2018); [Final report on SKYbrary bookshelf](https://skybrary.aero/bookshelf/final-report-a346-zs-snf-en-route-clariden-switzerland-6-november-2018)
- **[rule]** Airbus: overspeed events are more frequent in cruise because of shifting winds. Keeping the AP and A/THR engaged minimises altitude excursion; crews often disconnected anyway. Airbus recommends a lower speed target to gain margin to VMO/MMO. — [Airbus Safety First (overspeed article)](https://safetyfirst.airbus.com/app/themes/mh_newsdesk/iframe.php?p=68720) (via search excerpt)
- **[measured, research]** Bramberger et al. (2018, JAMC), "Vertically propagating mountain waves — a hazard for high-flying aircraft?":
  - Wave-induced changes in along-track wind can reduce airspeed toward stall speed.
  - The case studied is a stall warning at FL410 over Italy.
  - [UCAR record](https://impacts.ucar.edu/en/publications/vertically-propagating-mountain-waves-a-hazard-for-high-flying-ai/)
- **[measured]** T-REX campaign (Sierra Nevada, March–April 2006), NSF/NCAR GV flights at 9, 11 and 13 km, spanning the tropopause:
  - Vertical velocities in the primary wave varied by up to a factor of 2 along the barrier.
  - The largest waves were over the lower-terrain southern leg, consistently on repeated legs at 11.7 and 13.1 km.
  - Simulations attribute this to 3-D blocking and Coriolis effects.
  - [Smith et al. AMS 2008 paper](https://ams.confex.com/ams/pdfpapers/114749.pdf); [UCAR T-REX stratospheric waves dataset](https://data.ucar.edu/en/dataset/three-dimensional-characteristics-of-stratospheric-mountain-waves-during-t-rex) (via search excerpts; peak m/s value not retrieved)
- **[case]** BOAC 911 (Boeing 707, Mt Fuji, 5 Mar 1966):
  - Filed for FL310, but deviated to about 16,000 ft (summit 12,388 ft) at 320–370 kt IAS, heading about 298°.
  - Conditions: strong WNW flow behind a cold front.
  - The report concluded "abnormally severe turbulence ... gust load considerably in excess of the design limit", and that "powerful mountain waves existed in the lee of Mt Fuji", with their breakdown producing small-scale turbulence.
  - Tests suggested loads up to **7.5 g** momentarily jammed a passenger's cine camera (two blank frames). This is an inference from the film, not a flight recorder value.
  - The debris field was 16 km long.
  - [Wikipedia, BOAC Flight 911](https://en.wikipedia.org/wiki/BOAC_Flight_911)
- **[case]** Dec 1992, Colorado: a DC-8 lost a large part of its left wing, including an engine, in mountain-wave-related turbulence. Simulation ("The Calculators of Calm", Air & Space) found "tornado-like tubes" (horizontal vortex tubes) above the mountain-wave activity. No numbers were retrieved. — [AirFacts Journal](https://airfactsjournal.com/?p=13139)
- **[model]** At Denver International, numerous aircraft reported significant mountain-wave activity on the day of a jetliner accident. Modelled surface gusts were tied to undulations of lee waves in a midtropospheric stable layer. — [UCAR, Lee waves associated with a commercial jetliner accident at Denver](https://impacts.ucar.edu/en/publications/lee-waves-associated-with-a-commercial-jetliner-accident-at-denve/)

### Inferences (1)

- **Moderate wave target for a sim** (from the NWS scale): vertical air motion of 350–600 ft/min (1.8–3 m/s) at cruise. It should be mostly smooth. On autopilot it should show up as sustained pitch changes and airspeed oscillation, not as g-jolts.
- **Severe wave target:** ≥600 ft/min, up to about 2,000 ft/min in extreme cases. If the AP is unable to hold altitude, or is disconnected, altitude deviations exceed 1,000 ft.
- The Clariden and Bramberger cases suggest that a realistic "SEV MTW at cruise" is mostly about **horizontal wind / IAS swings** that are large enough to trigger an overspeed warning or erode the stall margin. Bumps are secondary.
- **Smooth vs rough:** the sources describe the violent turbulence as coming from wave *breaking* (BOAC 911, 1992 DC-8) and from *rotors* below the crest level. They do not support constant roughness in all wave conditions. No source found gives a measured fraction of smooth vs turbulent wave encounters.
- **Rotor turbulence** is a low-level phenomenon, below roughly mountain-top / crest level, on the lee side. It should be modelled as the worst mechanical turbulence for departures and arrivals near the lee of ranges.

### Gaps (1)

- No verified ICAO Annex 3 wording for SIGMET SEV MTW was found. The 600 ft/min figure is confirmed only for ATSB and NWS usage, and NWS states that ICAO has no official criterion.
- Measured peak vertical velocities at airliner altitudes (T-REX, Greenland, Andes, NZ Alps, Scotland) were not retrieved as numbers.
- No sourced typical wavelengths. The figure of 5–30 km for trapped lee waves is textbook knowledge and was not verified in this session.
- The Clariden SUST numbers (wind shift in kt, Mach excursion, altitude loss) were not retrieved; read the final report linked above.
- No statistics on how often cruise-level waves are smooth vs turbulent.
- No numbers for the 1992 DC-8 case (the NTSB report was not retrieved). The searches gave no Greenland, Andes, NZ or Scotland case data.

## Q2. Convective turbulence: in-cloud vs out-of-cloud, above tops, distance from cells, magnitudes, probability

### Takeaway (2)

In-cloud updrafts in deep convection measured by research aircraft reach about 25 m/s. Isolated gusts have been reported up to about 60 m/s. Turbulence of convective origin extends well beyond visible cloud: severe encounters have been documented about 20 km from optically thick cloud. MOG probability stays about 2× background out to about 70 km from convection. Research describes the FAA 20 nm rule as inadequate.

### Cited Findings (2)

- **[rule]** FAA thunderstorm avoidance guidance says not to fly within 20 nm of a thunderstorm, above thunderstorm tops, or beneath anvils. Sharman & Williams argue that recent out-of-cloud CIT research shows deficiencies in this guidance and the need to revise it. — [AMS 10th ARAM conf., Sharman & Williams paper 39089](https://ams.confex.com/ams/13ac10av/webprogram/Paper39089.html)
- **[measured + model]** Fovell, Sharman & Trier (2007 conf. paper), case study of two commercial 757s:
  - Both hit severe turbulence over NW Indiana at about 11.9 km MSL, in ostensibly clear air.
  - They had flown over or around a large convective storm and were about **20 km from any cloud of appreciable optical depth** (satellite).
  - Mechanism: divergent anvil-level outflow decelerated and turned the environmental winds. It also raised shear and lowered stability beyond the visible cloud, driving Ri to about 1 or lower.
  - Two anvils may constructively interfere, lowering Ri in the clearing between storms.
  - [Fovell, Sharman & Trier, AMS 12th Mesoscale conf. paper 126190 (PDF)](https://ams.confex.com/ams/pdfpapers/126190.pdf); [abstract page](https://ams.confex.com/ams/12meso/webprogram/Paper126190.html)
- **[model]** Lane & Sharman (2008) attribute turbulence above deep convection to two causes: shear instabilities from strong flow deformation near cloud top, and breaking gravity waves generated by cloud–environment interaction. Background winds and shear control the breaking. — [Univ. Melbourne record, Lane & Sharman 2008](https://findanexpert.unimelb.edu.au/scholarlywork/302280-some-influences-of-background-flow-conditions-on-the-generation-of-turbulence-due-to-gravity-wave-breaking-above-deep-convection)
- **[review]** Lane, Sharman, Trier, Fovell & Williams (2012 BAMS, "Recent advances in the understanding of near-cloud turbulence"):
  - Turbulence outside cloud is particularly dangerous because standard radar and satellite tools struggle to detect it.
  - High-frequency gravity waves above convective cloud are one source (Trier et al. 2010).
  - The motivating case was a 1997 encounter near Dickinson, ND, out of cloud at about 11 km over a developing cell.
  - [UCAR record](https://impacts.ucar.edu/en/publications/recent-advances-in-the-understanding-of-near-cloud-turbulence/); [Météo-France library](https://bibliotheque.meteo.fr/pub/DOC00018709-recent-advances-the-understanding-near-cloud-turbu.html) (abstract only; full text returned 403)
- **[model]** Trier et al. (2020): in a midlatitude cyclone, much of the severe turbulence lay outside the cloud shield. — [Univ. Melbourne record](https://findanexpert.unimelb.edu.au/scholarlywork/1493417-environment-and-mechanisms-of-severe-turbulence-in-a-midlatitude-cyclone)
- **[measured, statistical]** Hitchcock et al. (2025), as cited in an OU REU paper: at horizontal distances up to **70 km from convection, the likelihood of MOG turbulence is about twice the background risk**. — [Heaton, OU CAPS REU 2025 final paper](https://caps.ou.edu/reu/reu25/finalpapers/Heaton_FinalPaper.pdf)
- **[measured, statistical]** OU thesis, a climatology from more than 37 million commercial-aircraft in-situ EDR reports paired with GridRad radar:
  - The SE United States is the main hotspot for turbulence near thunderstorms.
  - Convective frequency alone does not explain the distribution. Upper-level winds, temperature and static stability control how far the turbulence extends.
  - [ShareOK, Spatiotemporal Trends ... Turbulence Near Thunderstorms](https://shareok.org/handle/11244/342840)
- **[measured]** An AMS conference paper binned airline EDR reports by distance from NCWD convection for VIP levels 1–5, giving relative frequencies of 4 turbulence levels per distance bin. Numbers were not retrieved. — [AMS pdfpapers 103420](https://ams.confex.com/ams/pdfpapers/103420.pdf)
- **[measured]** Research penetrations of thunderstorms (NOAA document):
  - Maximum recorded updraft was about **25 m/s**, with the strongest updrafts at 10.7–13.7 km. Vertical velocity increased with height up to the 7.6 km ceiling in one dataset.
  - A 1973 Colorado penetration had a peak mean vertical velocity of about 26 m/s.
  - Steiner & Rhyne (1962) reported a **63 m/s vertical gust** at 12.2 km in a 15.2 km storm. This is cited secondhand.
  - [NOAA repository noaa_17638](https://repository.library.noaa.gov/view/noaa/17638/noaa_17638_DS1.pdf)
- **[rule]** FAA Aviation Weather Handbook (FAA-H-8083-28, ch. 22): thunderstorm updraft speeds can exceed 3,000 ft/min (about 15 m/s). — [FAA-H-8083-28 ch. 22](https://www.faasafety.gov/files/events/SO/SO15/2024/SO15129441/FAA-H-8083-28-Chpt22.pdf)
- **[rule / secondary]** Turbulence beneath a thunderstorm can be absent and is usually no more than moderate. Most thunderstorm-related crashes come from stalls near the ground after sudden wind shifts, that is, wind shear rather than turbulence. — [Wikipedia, Cumulonimbus and aviation](https://en.wikipedia.org/wiki/Cumulonimbus_and_aviation)

### Inferences (2)

- **Sim targets, in cloud (CB core):** vertical drafts 10–25 m/s are typical maxima, with rare gusts far higher. Severe to extreme turbulence should be possible in the core and near the top. Below cloud base, outside the shaft, it should be usually light to moderate, with wind shear being the main threat.
- **Sim targets, out of cloud:**
  - Severe turbulence should be *possible* (low probability) out to about 20 km from the anvil edge and in the clear slot between two anvils.
  - An elevated MOG probability (about 2× background) should decay out to roughly 70 km.
  - Above and just downwind of tops, elevated turbulence should come from gravity-wave breaking, especially with strong upper-level winds and shear.
- The FAA "20 nm" figure (about 37 km) is a reasonable *avoidance* buffer. The physical tail of elevated risk extends further (about 70 km), but with low magnitude on average.

### Gaps (2)

- No quantitative in-cloud Δg distributions for airliners were retrieved.
- The probability of MOG *inside* CB vs at given distances was not obtained as a numeric curve. The EDR-by-distance figures in AMS paper 103420 and Kim & Chun (2016) were not read.
- No height-above-top profile (for example "turbulence up to X km above tops") was found with numbers.
- TS SIGMET area conventions (for example coverage OCNL/FRQ/EMBD/OBSC, and that TS implies SEV TURB and SEV ICE) were not verified in this session.

## Q3. Low-level / boundary-layer turbulence on departure and approach

### Takeaway (3)

Few hard numbers were retrieved. Standard engineering models (Dryden, MIL-F-8785) set low-altitude turbulence intensity σ at about 10% of the 20-ft wind speed. That gives σ of about 1.5 m/s for a 30 kt surface wind, with a scale length equal to height. Surface roughness has a stronger effect on gust factors than stability. Wind shear (sustained change in wind or downdraft, especially near thunderstorms) is distinct from turbulence and is the bigger accident cause.

### Cited Findings (3)

- **[rule / engineering model]** In the Modelica implementation of the MIL-F-8785 Dryden model, low-altitude turbulence intensity is σ = 0.1 × the wind at 20 ft, with "typically 30 kt" given, which is about 1.5 m/s. The scale length equals flight altitude. The approach speed is given as 140 kt in one place and 150 kt in another, an inconsistency inside the source. — [Modelica DrydenContinuousTurbulence doc](https://doc.modelica.org/Modelica%204.0.0/Resources/helpWSM/Modelica/Modelica.Blocks.Examples.Noise.DrydenContinuousTurbulence.html)
- **[rule]** In the planetary boundary layer, turbulence is only approximately isotropic in the horizontal plane, unlike the isotropic Dryden spectra assumed in the free atmosphere. Vertical gusts are therefore smaller than horizontal gusts near the ground. — [NASA NTRS 20190000875](https://ntrs.nasa.gov/api/citations/20190000875/downloads/20190000875.pdf) (via search excerpt)
- **[measured]** Research-aircraft measurements in the Arctic marine boundary layer show gust factors depend strongly on surface roughness, and roughness matters more than stability. — [AWI, Gust factor based on research aircraft measurements](https://epic.awi.de/41431)
- **[rule]** Pilot-report g thresholds (UCAR presentation): moderate is above about 0.5 g Δg (advise avoidance) and severe above about 1 g (must avoid). These are aircraft-dependent and subjective. — [Sharman, UCAR CPAESS slides](https://cpaess.ucar.edu/sites/default/files/2023-08/Sharman-Bob.pdf)
- **[rule]** EDR (peak, m^2/3 s^-1) category boundaries vary by standard:
  - One ICAO-style set: light 0.10–0.20, moderate 0.20–0.45, severe ≥0.45. The same source notes that perceived intensity depends on aircraft type, mass, altitude and airspeed. — [ICAO-related doc (imibo bookshelf 33157)](https://drupal.imibo.com/sites/default/files/bookshelf/33157.pdf)
  - ICAO A41 WP/235: light 0.1–0.4, moderate 0.4–0.7, severe >0.7. — [ICAO A41 WP/235](https://www.icao.int/sites/default/files/sp-files/Meetings/a41/Documents/WP/wp_235_en.pdf)
  - An NCAR workshop table lists ICAO 2001 (severe >0.5), ICAO 2007 (severe >0.7) and UCAR variants with severe thresholds of 0.45–0.8. — [NCAR EDR performance standards slides](https://ral.ucar.edu/sites/default/files/docs/emanuel-turbulenceworkshop2edrperformancestandards.pdf)

### Inferences (3)

- **Sim target, light mechanical turbulence** (moderate surface wind 15–25 kt, flat terrain): σ_w of about 0.5–1 m/s. Δg should mostly be <0.2–0.3 g, perceived as "light chop".
- **Moderate** (30+ kt winds, rough terrain, or strong surface heating): σ of about 1.5–2.5 m/s. Occasional Δg of 0.5 g; IAS fluctuations ±5–15 kt.
- **Severe low-level turbulence** should essentially require rotors (lee of high terrain in strong cross-ridge flow) or proximity to convection.
- These Δg ranges are derived from the threshold tables and the Dryden σ rule above. They are *not* measured airliner statistics.
- The sources support scaling intensity with wind speed (σ ∝ U) and terrain roughness more than with stability. Thermal (convective boundary layer) turbulence should be added on sunny afternoons, but no numbers were retrieved for it.
- Treat wind shear (sustained changes of about 15+ kt, microburst downdrafts) separately from random turbulence.

### Gaps (3)

- No measured Δg statistics for airliners in light or moderate BL turbulence on approach were found.
- No thermal-turbulence magnitudes (for example dry thermals of 2–5 m/s), which are common in glider literature but not retrieved here.
- The FAA AIM 7-1 PIREP turbulence table (light/moderate/severe/extreme pilot-perception descriptions) was not fetched. Its wording is well known but not verified this session.
- The 14 CFR 25.341 design gust values (for example U_ref of 56 ft/s EAS at sea level) were not retrieved.

## Q4. Clear-air turbulence near jet streams (context only)

### Takeaway (4)

CAT is expected where the Richardson number Ri = N²/(dU/dz)² falls below about 0.25. For typical upper-troposphere stability that corresponds to vertical shear of roughly 10–12 kt per 1,000 ft. That figure is a derived estimate, not a published threshold.

### Cited Findings (4)

- **[theory]** Turbulent (Kelvin–Helmholtz) instability requires Ri ≲ 0.25 (Miles–Howard 1961/1964). Ri is the ratio of static stability (N²) to the square of vertical wind shear. — [DTIC ADA019708](https://apps.dtic.mil/sti/pdfs/ADA019708.pdf); [SKYbrary, Richardson Number](https://skybrary.aero/articles/richardson-number)
- **[model/case]** In the 757 Indiana case, convective outflow drove Ri locally to about 1, near the threshold for 3-D turbulence, beyond the visible anvil. This shows convection can create CAT conditions. — [Fovell, Sharman & Trier, AMS 126190](https://ams.confex.com/ams/pdfpapers/126190.pdf)
- **[theory]** The Richardson-number diagnostic can be generalised to include horizontal shear, which matters near jet streams. — [arXiv 2602.21770](https://arxiv.org/html/2602.21770v1)
- **[rule / secondary]** A study-guide criterion pairs Ri < 0.25 with horizontal shear ≥40 kt per 150 nm. — [open-exam-prep ATP turbulence guide](https://open-exam-prep.com/study-guides/atp-pilot/aviation-meteorology-atmospheric-hazards/turbulence-mountain-waves)
- **[measurement caveat]** Vertical shear measured from near-horizontal flight paths or drifting balloons tends to be underestimated. — [NASA NTRS 19910008196](https://ntrs.nasa.gov/api/citations/19910008196/downloads/19910008196.pdf)

### Inferences (4)

- Ri = 0.25 means shear = 2N. With N of about 0.01 s⁻¹ (upper troposphere), the critical shear is about 0.02 s⁻¹, roughly 12 kt per 1,000 ft. This is my arithmetic, not a sourced threshold. It fits the operational lore of "≥6 kt/1,000 ft for possible CAT", which is also unverified here.
- In the stratosphere N roughly doubles, so a much higher shear is needed. CAT should therefore decay quickly above the tropopause unless gravity waves or convection overshoot are present.

### Gaps (4)

- No official FAA/NWS shear threshold in kt/1,000 ft was found. Ellrod index thresholds and CAT Δg statistics were not retrieved.
