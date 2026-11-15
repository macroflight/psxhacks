# Airliner turbulence frequency and forecast-area hit rates

Scope note: "PIREP" = subjective pilot report (voluntary, US-centric). "EDR" = objective in situ eddy dissipation rate computed onboard (NCAR algorithm; Delta/United/Southwest in the US, IATA Turbulence Aware worldwide). EDR thresholds differ between papers: for mid-size aircraft, light/moderate/severe ≈ 0.15/0.22/0.34 m^2/3 s^-1 (Sharman & Pearson 2017, as cited by AMT 2020); ICAO MoG ≈ 0.20; ECMWF guide moderate 0.20-0.45, severe >0.45; Williams 2017 uses 0.1/0.3/0.5 for a large transport.

## Q1. Share of cruise time and per-flight-hour probability at light / moderate / severe (EDR and PIREP data)

### Takeaway (1)

At cruise altitudes roughly 3% of airspace/time is light-or-greater, ~0.4% moderate-or-greater and ~0.1% severe (≈14 min LOG, ≈2 min MOG, ≈30 s SOG per 8 h flight, averaged). Objective EDR data are dominated by smooth air: about 90-95% of in situ reports are essentially null. US PIREP counts (65,000 MOG, 5,500 SOG per year) against ~19-20 million Part 121 flight hours give roughly 3 MOG and 0.3 SOG reports per 1,000 airline flight hours, an upper bound per airline hour because GA pilots also file PIREPs.

### Cited Findings (1)

- Williams (2017) calibrates a log-normal EDR^(1/3) distribution so that P(light-or-greater, EDR^(1/3) > 0.1) = 3.0% (Watkins & Browning 1973; Kim & Chun 2016) and P(moderate-or-greater, > 0.3) = 0.4% (Sharman et al. 2006; Kim & Chun 2016). Resulting category probabilities for a large commercial aircraft: null 97.0%, light 2.1%, light-to-moderate 0.5%, moderate 0.2%, moderate-to-severe 0.1%, severe 0.1% (Table 1). The author says these values agree with Sharman et al. (2014) and Sharman & Pearson (2017) "to within a factor of about two". — [Williams 2017, Adv. Atmos. Sci. 34, 576-586](https://d-nb.info/1130499898/34)
- Same paper, citing Sharman et al. (2014): median turbulence patch length ≈ 60 km (≈5 min of flying), median thickness ≈ 1 km. — [Williams 2017](https://d-nb.info/1130499898/34)
- Williams (via Scientific American): "roughly 0.1 percent of the atmosphere at cruising altitudes contains severe turbulence... around 30 seconds on an average eight-hour flight"; about 1 in 10 flights gets "several minutes" of severe turbulence and 9 get none; an 8-hour flight may contain "10 to 15 minutes" of clear-air turbulence. — [Scientific American](https://www.scientificamerican.com/article/yes-airline-flights-are-getting-bumpier-heres-why/)
- A 2017 global CAT study (Storer et al. GRL, as summarized in search results) uses the same 0.1% SOG / 0.4% MOG / 3.0% LOG global cruise-altitude figures, and its seasonal table shows moderate and severe CAT at 200/250 hPa peaking in MAM and JJA rather than DJF. Not verified from full text (403). — [Storer et al. 2017, GRL](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1002/2017GL074618)
- Delta in situ EDR (NOAA GTG3 assessment, Jan-Mar 2013 data): including interpolated zeros, "almost 95% of all intensities are in the zero bin" (0.0-0.02); heartbeat (routine every-15-min) reports 82% zero; triggered reports 25% zero; "almost all heartbeat reports have intensities less than about 0.1" except in the 0-10,000 ft layer. The authors note EDR undersamples turbulence because pilots avoid it and 15-min heartbeats miss most short events. — [Wandishin et al. 2014, NOAA TM OAR GSD-53](https://repository.library.noaa.gov/view/noaa/16874/noaa_16874_DS1.pdf)
- Sharman et al. (2014) (as quoted in a search-result summary of later literature, not verified in full text): only ~10% of UAL and DAL EDR data exceed 0.1 m^2/3 s^-1, and the frequency of EDR > 0.5 is only ~2 × 10^-5. Dataset: ~200 aircraft, >137 million EDR records through 2013, mostly cruise, US only. — [UCAR record of Sharman et al. 2014](https://impacts.ucar.edu/en/publications/description-and-derived-climatologies-of-automated-in-situ-eddy-d/); [AMT 2022 QAR EDR paper](https://amt.copernicus.org/articles/15/2277/2022/)
- DTN verification set (121,576 airline EDR obs, Aug-Oct 2014): roughly 85% of observations were zero. — [DTN verification overview](https://aviationdata.dtn.com/wp-content/uploads/sites/26/2023/03/The-DTN-Aviation-Numerical-Turbulence-Forecast-Overview-and-Verification.pdf)
- IATA Turbulence Aware EDR, 20,000-60,000 ft, clear-air reports (2019-2022): grid-point MoG fraction (peak EDR ≥ 0.2) rises from about 0.01 to about 0.08 moving into the highest horizontal-deformation bins; 4,880 NH MoG events (≥10 MoG reports within 40 min/250 km). — [WCD 2025, "Clear-air turbulence derived from in situ aircraft observation"](https://wcd.copernicus.org/articles/6/1583/2025/)
- NCAR: over the US, "moderate-or-greater pilot reports (PIREPs) average about 65,000/year" and "severe-or-greater PIREPs average about 5,500/year"; reporting is "voluntary, and subjective" and "incomplete". In situ EDR software gives "tens of thousands of reports per day". — [NCAR RAL turbulence page](https://ral.ucar.edu/aap/turbulence)
- Older estimate: "over 63 000 encounters with moderate-or-greater turbulence and 5000 encounters with severe-or-greater turbulence annually" (Sharman et al. 2006), vs Kauffmann (2002) "790 turbulence encounters annually for scheduled United States carriers" with injuries (687 minor + 38 serious FA injuries, 120 minor + 17 serious passenger injuries). — [Williams 2017](https://d-nb.info/1130499898/34)
- US Part 121 flew about 19.3 million flight hours in 2018 and 19.8 million in 2019. Departures were 9.3 million in 2017. — [AvWeb summary of NTSB 2019 statistics](https://www.avweb.com/aviation-news/accidents-edge-up-in-2019-ntsb/)

### Inferences (1)

- PIREP rates (my arithmetic): 65,000 / ~19.5 M h ≈ 3.3 MOG PIREPs per 1,000 airline flight hours; 5,500 / 19.5 M h ≈ 0.28 SOG per 1,000 h. Per departure (~9.3-10 M/yr), that is ≈0.7% of flights with a MOG PIREP and ≈0.06% with a SOG PIREP, if each report were a separate flight. These are rough bounds: PIREPs include GA and regional traffic, one event can produce several reports, and many encounters go unreported. Also, PIREP "severe" is known to be over-called relative to EDR severe.
- Time-fraction targets for a sim (cruise, global average): LOG ≈ 3% of cruise time, MOG ≈ 0.4%, SOG ≈ 0.1% (pure model calibration, uncertain to about a factor 2). Per hour of cruise: ~1.8 min light-or-greater, ~15 s MOG, ~4 s SOG, on average. With a median patch of ~5 min, that is on the order of one light patch every 2-3 hours and one MOG patch every ~20 flight hours. In real flying the distribution is very clumpy: most flights have none, a few have a lot.
- The DAL heartbeat result (82% zero, almost all <0.1) and DTN's 85% zeros suggest that in objectively sampled cruise, ≥85-95% of time is smooth to barely perceptible. Turbulence an addon generates outside forecast areas should be mostly null or light chop.
- The WCD result (MoG fraction 1% baseline, up to ~8% in the most deformed flow) gives a useful scaling: being in a dynamically favoured region raises MoG probability by about an order of magnitude, but even there >90% of samples are less than moderate.

### Gaps (1)

- Could not access Sharman et al. 2014 or Sharman & Pearson 2017 full text (AMS 403), so altitude-binned exceedance percentages at 0.15/0.22/0.34 and the per-flight-hour MOG encounter rate from EDR are not verified here.
- No per-flight-hour EDR encounter rate (e.g., "MOG events per 1,000 flight hours") was found from an objective dataset.
- IAGOS/MOZAIC turbulence frequency numbers were not found in this session.

## Q2. Variation by altitude, season, region, and jet stream

### Takeaway (2)

Turbulence frequency is highest near jets and in winter over the North Atlantic/North Pacific for CAT. Convective turbulence peaks in summer. Low levels (<10,000 ft) carry the most frequent light turbulence. Accident data are dominated by convection and by descent below FL200.

### Cited Findings (2)

- North Atlantic point (Prosser et al. 2023 via Reading press summary): annual severe-CAT duration rose from 17.7 h (1979) to 27.4 h (2020); moderate rose 37% from 70.0 to 96.1 h; light rose 17% from 466.5 to 546.8 h. — [Univ. of Reading news 2023](https://www.reading.ac.uk/news/2023/Research-News/Aviation-turbulence-strengthened-as-the-world-warmed); [Prosser et al. 2023 GRL](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2023gl103814)
- Projected 2xCO2 transatlantic winter increases: light +59%, light-to-moderate +75%, moderate +94%, moderate-to-severe +127%, severe +149% (ensemble means). Earlier: MOG on transatlantic winter routes +40-170% (Williams & Joshi 2013). — [Williams 2017](https://d-nb.info/1130499898/34)
- Delta EDR heartbeat reports: almost all <0.1 except in the 0-10,000 ft layer, i.e. light turbulence is much more common near the surface. — [NOAA TM GSD-53](https://repository.library.noaa.gov/view/noaa/16874/noaa_16874_DS1.pdf)
- China in situ EDR (Xiamen Airlines, 2018-2025): turbulence frequency falls with altitude; severe turbulence is rare below 3 km, peaks at 3-8 km in summer (60-70%) and above 8 km in winter; highest-EDR routes mainly north-south over eastern China. (Search summary; full text not read.) — [Meteorological Applications 2026](https://rmets.onlinelibrary.wiley.com/doi/10.1002/met.70224)
- IATA EDR MoG events: jet-stream occurrence near events is <0.25 over Eurasia vs ≥0.38 in the other four regions studied. Rossby-wave breaking is present at ~0.38 of event locations and warm conveyor belts at ~0.25. Observations are heavily concentrated over the contiguous US and sparse in the Southern Hemisphere. — [WCD 2025](https://wcd.copernicus.org/articles/6/1583/2025/)
- NTSB Part 121 turbulence accidents 2009-2018: convective turbulence present in 57.7% (64 of 111), clear-air in 28.8% (32 of 111), with mountain wave/wake/mechanical smaller and 3.6% unknown. More convective accidents occur in summer. Phase: en route-descent 36.0%, en route-cruise 30.6%; descent+approach ≈ half (50.5%); "60% of these accidents occurred below 20,000 feet"; 65.3% (32 of 49) of descent accidents with known altitude occurred below a stated altitude (figure 11). — [NTSB SS-21-01](https://www.ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- GTG3 mountain-wave component catches 99% of light-or-greater explicit MW PIREPs (with ~60% false alarms) and 70% of MOG MW PIREPs (with ~5-6% false alarms) in winter. — [NOAA TM GSD-53](https://repository.library.noaa.gov/view/noaa/16874/noaa_16874_DS1.pdf)

### Inferences (2)

- In a sim, a reasonable regional/seasonal multiplier on the global cruise base rate is a few times higher in the winter jet over the North Atlantic, North Pacific and East Asia, and over the Rockies, Andes and Himalaya in strong cross-mountain flow. Tropics are dominated by convective (in or near cloud) rather than clear-air turbulence. This is qualitative; I found no single table of regional multipliers.
- Injury risk is concentrated in descent and convective weather, not cruise CAT, partly because cabin crew are moving about. Felt turbulence frequency below FL100 is higher than at cruise.

### Gaps (2)

- No verified numeric regional or seasonal MOG percentages (e.g., North Pacific winter vs tropics) from EDR climatologies; Storer et al. 2017/2019 tables could not be read.
- No altitude profile of exceedance percentages from Sharman et al. 2014.

## Q3. Forecast verification: how much of a forecast/SIGMET/G-AIRMET area is actually turbulent

### Takeaway (3)

Turbulence advisories are broad and miss a lot. US G-AIRMETs (MOD TURB) caught only ~45% (EDR) to ~55% (PIREP) of MOG events. Inside them most observations are less than moderate: GTG can halve false alarms inside G-AIRMETs while keeping 80-90% of the MOG hits. Severe is barely forecastable (GTG captures ≤10% of EDR > 0.4 events). I found no published "fraction of a SEV TURB SIGMET area that is actually severe". The NWS says most turbulence SIGMETs are issued only after a PIREP.

### Cited Findings (3)

- G-AIRMET vs GTG3 (US, 2013): for MOG (EDR ≥ 0.18 or PIREP ≥ 3), the average G-AIRMET volume is about the same as GTG3 > 0.18, but POD ≈ 0.75 for GTG3 vs ≈ 0.45 (EDR) / 0.55 (PIREPs) for G-AIRMETs. GTG3 matches the G-AIRMET POD using one-third of the volume. — [NOAA TM GSD-53](https://repository.library.noaa.gov/view/noaa/16874/noaa_16874_DS1.pdf)
- Inside G-AIRMETs, GTG3/GTG2.5 "reduce false alarms by about half while still capturing 80-90% of all the MOG PIREPs". Outside G-AIRMETs, GTG captures ~60% of the MOG PIREPs missed by G-AIRMETs while flagging ~20% of non-MOG PIREPs. With a higher threshold, GTG3 captures 60% of MOG inside a G-AIRMET while cutting false alarms by 80%. — [NOAA TM GSD-53](https://repository.library.noaa.gov/view/noaa/16874/noaa_16874_DS1.pdf)
- GTG3 by EDR threshold: 90 to nearly 100% of turbulence at 0.1 captured, but "10% or less of the severe turbulence exceeding 0.4"; false detections fall from ~80% (light) to ~0% at 0.4. — [NOAA TM GSD-53](https://repository.library.noaa.gov/view/noaa/16874/noaa_16874_DS1.pdf)
- Hong Kong Observatory verification of WAFC CAT potential vs airline EDR: hit rate ≈ 0.5 at false-alarm rate ≈ 0.35 for 6-12 h forecasts (max-EDR threshold 0.4). — [AMS 91st Annual, P4.53](https://ams.confex.com/ams/91Annual/webprogram/Manuscript/Paper177142/Verification_NWPTurb_AMS91_P453.pdf)
- Multi-diagnostic, multi-model ensemble turbulence forecasts: AUC 0.86, max True Skill Statistic 0.552. — [Multi-diagnostic multi-model ensemble (ResearchGate)](https://www.researchgate.net/publication/339124577_Multi-diagnostic_multi-model_ensemble_forecasts_of_aviation_turbulence)
- Gill (2014) objectively verified WAFC CAT forecasts against flight data using ROC/AUC (numbers not retrieved). — [Gill 2014, Met. Apps](https://rmets.onlinelibrary.wiley.com/doi/10.1002/met.1288)
- DTN EFH vs GTG (121,576 EDR obs): EFH ≈ 11% higher hit rate; GTG ≈ 14% higher false-alarm rate; ~85% of obs zero, which inflates hit/no scores. — [DTN](https://aviationdata.dtn.com/wp-content/uploads/sites/26/2023/03/The-DTN-Aviation-Numerical-Turbulence-Forecast-Overview-and-Verification.pdf)
- NTSB interviews: "NWS meteorologists stated that most turbulence SIGMET advisories are only issued after a PIREP is reported"; verification is hampered by subjective PIREPs and limited objective in situ data. — [NTSB SS-21-01](https://www.ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- US SIGMETs cover severe/extreme turbulence (valid up to 4 h); AIRMETs cover moderate turbulence; G-AIRMETs are issued as 3-hourly snapshot polygons with altitude limits; text AIRMETs (6 h) cover a larger area. — [NTSB SS-21-01](https://www.ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)

### Inferences (3)

- Conditional probability inside a MOD TURB advisory: if half the false alarms can be removed while keeping 80-90% of hits, most observations inside a G-AIRMET are non-MOG. Combined with the 1%-to-8% MoG fraction in favoured flow (WCD 2025), a plausible sim target is about 5-15% of flight time at moderate inside a G-AIRMET/AIRMET or WAFS MOD area, with the rest smooth or light. Treat this as an engineering estimate, not a measured figure.
- Inside a SEV TURB SIGMET: severe is localized (patches ~60 km × 1 km, and GTG catches ≤10% of EDR > 0.4). A plausible target is mostly light/moderate with brief severe cores, e.g. severe for ~1-5% of the time spent in the polygon and MOG 10-30%. This is also an estimate. Because SIGMETs usually follow a PIREP, the probability is higher near the reporting position and altitude than across the whole polygon.
- About half of MOG events occur outside any G-AIRMET, so a realistic sim should keep a low background of unforecast MOG (≈0.1-0.4% of cruise time) outside advisory areas.

### Gaps (3)

- No published fraction-of-area-turbulent statistic specifically for SEV TURB SIGMETs (US or international) was found, and no verified POD/FAR for ICAO SIGMETs for turbulence.
- G-AIRMET average percent volume (Fig. 5.11 in GSD-53) is only in a figure; numbers were not extractable.
- The GTG2.5 quality assessment (AIRMET POD vs percent volume) was not downloadable (403).

## Q4. Injury and accident scale (US and worldwide)

### Takeaway (4)

US Part 121 had 111 turbulence-related accidents in 2009-2018 (~11 per year, 37.6% of all Part 121 accidents), each with at least one serious injury (123 seriously injured passengers and flight attendants in total). That is about 0.05-0.06 accidents per 100,000 flight hours, or about 1 per million departures. Flight attendants make up most of the seriously injured.

### Cited Findings (4)

- 2009-2018 Part 121: 111 turbulence-related accidents of 295 total (37.6%); yearly counts 1989-2018 ranged 4-18; no obvious trend in the rate normalized by flight hours; all 111 had ≥1 serious injury, mostly one occupant; 123 passengers and flight attendants seriously injured; 81 of 97 (83.5%) seriously injured flight attendants were documented as not wearing seat belts; flight attendant was the most commonly injured person type; aft cabin accounted for more than three-quarters of FA injuries. — [NTSB SS-21-01](https://www.ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- NTSB summary page: turbulence-related accidents are "the most common type of accident involving air carriers operating under ... Part 121". — [NTSB DCA18SS003 page](https://www.ntsb.gov/safety/safety-studies/Pages/DCA18SS003.aspx)
- Secondary report: the turbulence accident rate fluctuates around ~0.05 per 100,000 flight hours. — [Aviation Week](https://aviationweek.com/business-aviation/safety-ops-regulation/turbulence-not-just-airline-problem)
- NCAR: turbulence ≈ 75% of all weather-related accidents and incidents (NTSB attribution); US airline cost estimate $150-500 million/year; ~40 GA fatalities/year from turbulence encounters. — [NCAR RAL](https://ral.ucar.edu/aap/turbulence)
- Kauffmann (2002): 790 injury-causing encounters/year for scheduled US carriers (687 minor + 38 serious FA; 120 minor + 17 serious pax). — [Williams 2017](https://d-nb.info/1130499898/34)

### Inferences (4)

- Rate (my arithmetic): ~11 accidents/yr ÷ ~19-20 M Part 121 flight hours ≈ 0.056 per 100,000 h, consistent with the ~0.05 secondary figure. ÷ ~9.3-10 M departures/yr ≈ 1.1-1.2 per million departures.
- Scale for the sim: injury-causing severe encounters are about 1 in 2 million flight hours, while SOG PIREPs are about 1 per 3,500 airline hours. Severe-as-reported is therefore far more common than injury-level severe, and an occasional brief severe jolt in SIGMET areas is realistic. Sustained severe should be extremely rare.

### Gaps (4)

- No exact NTSB per-million-departure figure was found (computed above instead).
- No worldwide ICAO/IATA turbulence injury rate was retrieved in this session.
