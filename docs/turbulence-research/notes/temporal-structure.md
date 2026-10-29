# Temporal and spatial structure of airliner turbulence encounters

Notes for a PSX 747-400 weather addon. Throughout, "measured" means instrumented-aircraft or recorder data, and "model" means an engineering or simulator assumption. Unit conversions to seconds assume a cruise TAS of about 250 m/s (about 485 kt), so 1 km is about 4 s and 10 statute mi (16 km) is about 64 s. These conversions are my own arithmetic, not taken from the sources.

## Encounter durations and patch lengths (how long does an encounter last?)

### Takeaway (1)

Measured patch-length distributions are strongly skewed. About half of high-altitude turbulent areas are shorter than about 10–20 statute mi (16–32 km), which is roughly 1–2 minutes at cruise. Only about 1% exceed 30 mi, except in jet-stream/mountain-wave situations, where 150–200 mi areas were seen. Injury-producing severe events in accident reports are short, typically 5–30 s of strong accelerations, often embedded in a longer bumpy period. Modern EDR studies quote longer median patch scales (about 100 km), but those come from 1-minute EDR averaging and different thresholds.

### Cited Findings (1)

- **Project Jet Stream.** More than 50% of turbulent patches were shorter than 20 mi, and nearly one-third were shorter than 10 mi (Cunningham, cited by Stephens & Reiter, Univ. of Texas Report P-12, 1966). The same report concludes that CAT patches "average from 500 to 3000 feet in depth and one-half have horizontal dimensions less than 20 miles." — [Stephens & Reiter 1966, NASA NTRS 19670003687](https://ntrs.nasa.gov/api/citations/19670003687/downloads/19670003687.pdf)
- **U-2 measurements at 20,000–55,000 ft over the W. US, UK/Europe, Turkey and Japan (about 150,000 mi of flight).**
  - "Rough air" was defined as continuous accelerometer disturbance with gusts above 2 ft/s.
  - Patch length was computed as TAS × time in rough air.
  - "Less than 50 percent of the turbulent areas exceeded 10 miles in length and, except for the operation over Japan, only about 1 percent exceeded 30 miles in length."
  - Over Japan (jet stream plus mountain wave), areas 150–200 mi long were encountered. Elsewhere the maximum was under 50 mi.
  - Measured data. — [Coleman & Meadows, NASA Memo 4-17-59L (1959)](https://ntrs.nasa.gov/api/citations/19980228362/downloads/19980228362.pdf)
- **Persistence (Reiter 1964, via Veazey).** A research aircraft followed a smoke-marked CAT patch over South Australia for about 45 minutes, covering about 100 mi, and its intensity and size stayed about the same. Moderate or severe CAT "may persist for many hours" (Endlich 1963), and CAT areas can persist up to a day (Colson). — [Veazey 1970, A Literature Survey of Clear Air Turbulence, NASA NTRS 19700022691](https://ntrs.nasa.gov/api/citations/19700022691/downloads/19700022691.pdf)
- **Vinnichenko et al. (1980), cited by Sharman et al.** The median persistence of upper-level CAT patches is about 6 h, the median horizontal dimension 60–80 km, and the median thickness 500–1000 m. — [Sharman, Wolff, Fowler & Brown, "Climatologies of upper-level turbulence over the continental U.S. and oceans," AMS conf. paper J1.8](https://ams.confex.com/ams/pdfpapers/39241.pdf)
- **NCAR CAT overview (Sharman).** CAT "occurs in pancake-shaped patches", which are "nonstationary and intermittent", with "median depth ~1 km, length ~100 km". This is a summary slide, and the underlying dataset and threshold are not stated on it. — [Sharman, "Clear-air turbulence" lecture slides, NCAR/RAL](https://www.eol.ucar.edu/sites/default/files/files_live/private/Sharman_CRITE_CAT_final.pdf)
- **In-situ EDR archive.** NCAR/FAA analyzed patch lengths and depths from more than 140 million per-minute EDR records from about 200 US airliners (as of January 2013), but the abstract gives no numbers. — [Sharman et al., EGU2013-6571](https://meetingorganizer.copernicus.org/EGU2013/EGU2013-6571.pdf)
- **Resolution limit of in-situ EDR.** Reports are per minute, each carrying a median EDR and a peak (95th-percentile) EDR for that minute. In-situ EDR therefore cannot resolve sub-minute patch structure, and gives only "peak vs median within a minute". — [AMS conf. paper on in-situ EDR reporting](https://ams.confex.com/ams/pdfpapers/134648.pdf)
- **Accident and incident durations (recorder data, individual cases):**
  - About 10 s of severe turbulence, with a Mach change and pitch excursion. — [NTSB WPR12LA053](https://data.ntsb.gov/carol-repgen/api/Aviation/ReportMain/GenerateNewestReport/82465/pdf)
  - B777: vertical acceleration went from 2.2 g to about 0.5 g within about 5 s, injuring 2 flight attendants. — [NTSB WPR11LA203](https://data.ntsb.gov/carol-repgen/api/Aviation/ReportMain/GenerateNewestReport/78892/pdf)
  - An event of about 30 s, with Nz between about −0.17 and +1.62 g. — [NTSB DCA15LA067](https://data.ntsb.gov/carol-repgen/api/Aviation/ReportMain/GenerateNewestReport/90744/pdf)
  - CAT with peaks of +1.81, −1.38 and +1.99 g, and oscillation continuing for 28 s. — [ASN 356769 (NTSB MIA96LA220)](https://asn.flightsafety.org/wikibase/356769)
  - Nz between +1.5 and −0.3 g over about 5 s. — [ASN 314621 (NTSB DCA10FA076)](https://asn.flightsafety.org/wikibase/314621)
  - Three separate significant encounters during one descent, max +1.75 g. — [UK AAIB report](https://assets.publishing.service.gov.uk/media/5422f52a40f0b613460005ad/dft_avsafety_pdf_501807.pdf)
  - Dash 8, about 10 s severe. — [ATSB AO-2013-084](https://www.atsb.gov.au/sites/default/files/media/4356236/ao-2013-084_final.pdf)
  - Caveat: these figures came via search summaries of the reports. I did not read each full report.

### Inferences (1)

- A practical target for a 747 at cruise in jet-stream CAT:
  - Patch durations should be log-normally or exponentially distributed, with a median of about 1–2 min (10–20 mi), a long tail to about 5–10 min, and rare jet-stream or mountain-wave cases of 20+ min (150–200 mi).
  - Severe content within a patch should last seconds (about 5–30 s bursts), not minutes.
- The roughly 10x discrepancy between the older "median <10–20 mi" figures and the newer "60–100 km" figures probably comes from definitions. The old studies used a "continuous accelerometer disturbance" threshold of 2 ft/s, and the newer studies use 1-min EDR above a threshold plus the persistence of whole turbulent regions. A sim could model a turbulent region of about 60–100 km containing several shorter patches of about 10–30 km, which is consistent with both.
- Patches persist for hours. Re-entering the same area, or the next aircraft on the same track, should hit similar turbulence, so the sim should not re-roll randomly every pass.

### Gaps (1)

- I found no published numeric distribution of patch length or duration from the modern US in-situ EDR archive. The Sharman et al. 2014 JAMC paper ("Description and derived climatologies of automated in situ EDR reports") is paywalled (HTTP 403), and the EGU abstract gives no numbers.
- I found no source giving encounter-duration statistics split by light, moderate and severe intensity.
- IAGOS/MOZAIC patch statistics: not found within the search budget.

## Spatial extent by turbulence type (horizontal and vertical)

### Takeaway (2)

CAT is thin and flat ("pancake-shaped"): typically about 300–1000 m (1000–3000 ft) thick and tens of km long, elongated along the wind. Mountain-wave and jet-stream-plus-mountain-wave areas can be much longer (150–200 mi). Convective turbulence is far denser in gusts inside radar echo than in clear air near clouds.

### Cited Findings (2)

- **Thickness of CAT areas:**
  - CAT areas are "generally quite thin in the vertical with 2000 ft being the average thickness".
  - Press et al. (1953) report that 50% of cases were less than 2000 ft thick.
  - Extremes run from 100 to 20,000 ft.
  - Patches are elongated along the wind (Clem).
  - The ratio of turbulent to smooth patches within a turbulent area varies from about 1:1 to about 1:10.
  - [Veazey 1970, NASA NTRS 19700022691](https://ntrs.nasa.gov/api/citations/19700022691/downloads/19700022691.pdf)
- **Depth range.** Patches are 500–3000 ft deep. — [Stephens & Reiter 1966](https://ntrs.nasa.gov/api/citations/19670003687/downloads/19670003687.pdf)
- **Median thickness and length.** The median thickness is 500–1000 m and the median horizontal dimension 60–80 km (Vinnichenko 1980). — [Sharman et al. AMS J1.8](https://ams.confex.com/ams/pdfpapers/39241.pdf)
- **Pancake shape.** The median depth is about 1 km and the length about 100 km. — [Sharman NCAR slides](https://www.eol.ucar.edu/sites/default/files/files_live/private/Sharman_CRITE_CAT_final.pdf)
- **Fraction of distance in turbulence by altitude.** At 40,000–55,000 ft, turbulence was met over only about 2% of the flight distance, against 5% or more at 20,000–40,000 ft. The very long areas over Japan were attributed to the strong jet stream plus mountain wave. — [Coleman & Meadows 1959](https://ntrs.nasa.gov/api/citations/19980228362/downloads/19980228362.pdf)
- **Fraction of flight time in rough air (>2 ft/s gusts), from airline VGH data.** About 20% for feeder-line, 12% for short-haul and 7% for long-haul turbojets at about 30,000 ft. — [Coleman, NASA TN D-4433-era, "Trends in repeated loads on transport airplanes" (1968), NTRS 19680015004](https://ntrs.nasa.gov/api/citations/19680015004/downloads/19680015004.pdf)
- **Clodman et al. (1961).** About 3% of distance flown is in CAT. This is quoted in the Veazey survey, but the text extraction was partly garbled, so the figure is low-confidence. — [Veazey 1970](https://ntrs.nasa.gov/api/citations/19700022691/downloads/19700022691.pdf)
- **Convective areas (Steiner, NACA radar evaluation).** The mean gust spacing (gusts of all intensities) was 0.087 mi inside cloud radar echo, against up to 0.73 mi in clear air near clouds. The miles flown to meet a gust of a given size were about one-third to one-half as many in radar echo as in non-echo cloud. — [Steiner, NACA RM, "An evaluation of airborne radar as a means of avoiding atmospheric turbulence", NTRS 19930085520](https://ntrs.nasa.gov/api/citations/19930085520/downloads/19930085520.pdf)
- **Effect of weather radar.** After airborne radar was installed, the largest gusts per mile were about 25% lower, attributed to storm avoidance. — [Copp & Walker, NACA TN 4129 (1957)](https://ntrs.nasa.gov/api/citations/19930084897/downloads/19930084897.pdf)

### Inferences (2)

- For CAT zones in the sim, use vertical layers about 300–1000 m (1000–3300 ft) thick. A 2000 ft step climb or descent should often get the aircraft out, which matches the standard pilot remedy.
- Convective, in-echo turbulence should have about 5–8 times denser gust spacing than CAT near clouds (0.087 vs 0.73 mi, from Steiner's numbers).
- Mountain-wave and jet-plus-wave cases deserve long, coherent areas (100–200 mi) and can extend to high altitude, as over Japan at FL520.

### Gaps (2)

- Mountain-wave breaking patch sizes specific to airliner cruise altitudes: I found only the U-2 Japan anecdote.
- I found no modern (post-2000) breakdown of patch dimensions by type (CAT vs convective vs MWT).

## Intermittency inside a patch: jolts, peak-to-mean, spectra and scale lengths

### Takeaway (3)

Real turbulence comes in "bursts of relatively intense motion separated by areas of relative calm". Constant-intensity Gaussian (Dryden/von Kármán) noise is rated "too continuous" and unrealistic by pilots. EDR follows a log-normal distribution. For airliner response, the standard medium/high-altitude von Kármán scale length is 2500 ft (762 m), which gives a spectral knee of a few seconds at cruise TAS.

### Cited Findings (3)

- **Patchiness is measured, not assumed.** "Turbulence has a non-Gaussian patchy structure which seems to occur in bursts of relatively intense motion separated by areas of relative calm." The report shows a 40-s sample of real turbulence illustrating this. — [Univ. of Virginia, "Effects of simulated turbulence on aircraft handling qualities", NASA CR-152621 (1977)](https://ntrs.nasa.gov/api/citations/19770013171/downloads/19770013171.pdf)
- **Gaussian Dryden in a simulator.** "The intensity for the Gaussian model is nearly constant whereas measured ('real') turbulence exhibits a patchy nature or intensity bursts. Test pilots... rated the realism fair to poor." — [NASA CR-152621](https://ntrs.nasa.gov/api/citations/19770013171/downloads/19770013171.pdf)
- **Pilot correlations (same report):**
  - Pilot-rated realism correlated with patchiness (0.58), element of surprise (−0.63) and frequency content (0.52).
  - Patchiness correlated with RMS intensity at only 0.07. In the report's words, "non-Gaussian patchiness characteristics cannot be induced by simply chasing a higher level of intensity (rms)".
  - Almost all pilots rated the Gaussian model "a little too continuous".
  - The Rayleigh model and the patch-structured model were rated "about right".
  - Pilots rated Gaussian turbulence as typical of altitudes above 10,000 ft, and the patchy models as low-altitude turbulence. This was a light-aircraft approach task.
  - At about the same RMS, handling-quality ratings went from satisfactory (Gaussian) to unacceptable (patchy composite model). — [NASA CR-152621](https://ntrs.nasa.gov/api/citations/19770013171/downloads/19770013171.pdf)
- **Measured RMS-intensity distribution.** Analysis of several atmospheric datasets showed that a truncated Gaussian distribution best fits the probability density of the patch RMS intensity. This was used as a "distribution modifier", a second process that sets intensity from patch to patch. — [NASA CR-152621](https://ntrs.nasa.gov/api/citations/19770013171/downloads/19770013171.pdf)
- **In-situ EDR probability.** The PDF of ε or ε^(1/3) from in-situ data is log-normal. About 95% of the time is "smooth", "moderate" has a probability of about 10⁻³ or less, and "severe" about 10⁻⁴ or less. — [Sharman NCAR slides](https://www.eol.ucar.edu/sites/default/files/files_live/private/Sharman_CRITE_CAT_final.pdf)
- **EDR PDFs** are fairly well matched by log-normal distributions. — [JAMC 56(6) 2017, "Comparison of Turbulence Indicators Obtained from In Situ Flight Data"](https://journals.ametsoc.org/view/journals/apme/56/6/jamc-d-16-0291.1.xml) (search snippet only; full text 403)
- **Fine-scale CAT is not always Kolmogorov.** A 2023 JGR study of a HALO encounter found strong patchiness, with separate bursts in TKE and dissipation rate. — ["Not All Clear Air Turbulence Is Kolmogorov—The Fine-Scale Nature of Atmospheric Turbulence"](https://e-docs.geo-leo.de/entities/publication/d13c4742-9d35-4fc2-9a5d-c28bf0b55650) (search snippet only; page 403)
- **Scale lengths (model, MIL standards):**
  - Above 2000 ft, MIL-F-8785C uses Lu = Lv = Lw = 2500 ft (762 m) for von Kármán.
  - MIL-HDBK-1797/1797B uses Lu = 2Lv = 2Lw = 2500 ft, with a correspondingly modified spectral constant (2.678 instead of 1.339 for the v and w spectra).
  - The von Kármán spectra use exponents of 5/6 (longitudinal) and 11/6 (lateral/vertical).
  - [MathWorks, von Kármán Wind Turbulence Model (Continuous)](https://www.mathworks.com/help/aeroblks/vonkarmanwindturbulencemodelcontinuous.html)
- **High-altitude intensity (model).** σu = σv = σw is taken from an altitude vs probability-of-exceedance table. The conventional labels are light = 10⁻², moderate = 10⁻³ and severe = 10⁻⁵. The Dryden variant uses a 1750 ft scale length at altitude (MathWorks Dryden doc). The σ table values themselves are shown only as a figure and were not extracted. — [MathWorks von Kármán doc](https://www.mathworks.com/help/aeroblks/vonkarmanwindturbulencemodelcontinuous.html); [MathWorks Dryden doc](https://www.mathworks.com/help/aeroblks/drydenwindturbulencemodelcontinuous.html)
- **Measured velocity vs acceleration PDFs.** Measured PDFs of atmospheric turbulence have somewhat wider tails than a Gaussian, especially for accelerations, while velocities are close to Gaussian. — [DTU "Non-Gaussian turbulence"](https://orbit.dtu.dk/en/publications/non-gaussian-turbulence/) (search snippet only)

### Inferences (3)

- **Time scale.** With L = 762 m and V ≈ 250 m/s, L/V ≈ 3 s. Most of the gust energy the aircraft feels as discrete bumps sits at periods of roughly 1–10 s (about 0.1–1 Hz). That band matches heave and short-period response. A sim jolt generator should concentrate energy there, and avoid buzzing above about 2–3 Hz or slow 30-s swells, unless the swell is meant to be mountain wave.
- **Two-layer structure.** Build turbulence as a product of a slowly varying intensity envelope (patch to patch, tens of seconds to minutes, truncated-Gaussian or log-normal RMS) and a fast Gaussian or von Kármán carrier. This is the Reeves/UVA product-of-processes approach, which gives bursts with a K0 (modified-Bessel) amplitude PDF and heavy tails.
- **Severe as tails.** Severe should appear as a few large excursions (for example, 2–5 peaks of 1.5–2 g / 0–0.5 g over 5–30 s, matching the accident cases above) on top of moderate or light background shaking, not as sustained ±1 g noise.

### Gaps (3)

- I found no published "number of significant jolts per encounter" statistic, and no peak-to-mean or kurtosis value for airliner Nz from QAR studies.
- I could not retrieve the van de Moesdijk (Delft, 1975/1978) reports on patchy non-Gaussian turbulence. Only their titles are cited. I also found no "Jansen non-Gaussian turbulence" paper, so that attribution is unconfirmed.
- I could not obtain the σ values in MIL-HDBK-1797's high-altitude exceedance table (figure only).

## How simulators and research models generate realistic turbulence

### Takeaway (4)

The standard simulator approach (Dryden/von Kármán Gaussian filters at a fixed σ) is known to be unrealistic because it is too continuous. Research models add intermittency by modulating a Gaussian carrier with a second random envelope (Reeves 1969/70, the UVA/NASA 1977 variants, and Delft work by van de Moesdijk). I found no turbulence-specific realism standard in the FAA UPRT guidance.

### Cited Findings (4)

- **Reeves (1970, USAF report).** Generates three simultaneous gust components by multiplying independent random processes with Dryden spectra. The amplitude PDF is a modified Bessel function of the second kind, order zero (K0), which is heavy-tailed. — [Reeves, DTIC AD0701735](https://apps.dtic.mil/sti/pdfs/AD0701735.pdf) (via search summary)
- **Modified Reeves model (UVA/NASA).** The output is c(t) = a(t)·b(t) (a modified Bessel process) plus an independent Gaussian d(t). Patchiness and other statistics are tuned through the standard-deviation ratio R = σc/σd. — [NASA CR-152621](https://ntrs.nasa.gov/api/citations/19770013171/downloads/19770013171.pdf)
- **Complex-terrain simulation review.** Reeves' model is described as a representative non-Gaussian model "intended to be used in piloted flight simulators", which overcomes the Gaussian underestimate of large velocities. — [MDPI Aerospace 9(2):103 "Atmospheric Disturbance Modelling for a Piloted Flight Simulation Study of Airplane Safety Envelope over Complex Terrain"](https://www2.mdpi.com/2226-4310/9/2/103) (search snippet; page 403)
- **Delft (van de Moesdijk).** A 1975 report on patchy turbulence based on a non-Gaussian simulation technique, and a 1978 memo on the non-Gaussian structure of simulated turbulence in piloted simulation. Only the citations were found. — [core.ac.uk listing](https://core.ac.uk/works/24924206)
- **Intermittency-based alternative.** A Beihang University paper proposes an intermittency-based 1D model instead of Dryden with Gaussian patches. — [BUAA](https://research.buaa.edu.cn/en/publications/generation-of-spatial-atmospheric-turbulence-field-in-aircraft-mo/)
- **FAA guidance.** AC 120-111 (UPRT) covers simulator fidelity limits and instructor training, but the search found no turbulence-model specification in it. — [FAA AC 120-111](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_120-111.pdf)
- **Part 60 (2016 revision).** Requires "realistic gusting crosswind profiles" with a statement of compliance on source data (Table A1A, 2.d.3). — [FAA NSP Guidance Bulletin 16-02](https://www.faa.gov/sites/faa.gov/files/about/initiatives/nsp/fstd_dir/16-02.pdf)

### Inferences (4)

- PSX's WxBurst-style discrete events fit the "burst within background" pattern better than a constant Gaussian. The model literature supports driving burst amplitude from a slow random envelope rather than raising RMS.

### Gaps (4)

- I could not check what ICAO Doc 9625 and 14 CFR Part 60 Appendix A require for cruise turbulence models (search budget). I found no evidence of a requirement for non-Gaussian or patchy turbulence.

## Rate of discrete gusts (how often per minute does a pilot feel a bump?)

### Takeaway (5)

Measured gust counts vary enormously, by about 100:1 across operations, and are usually reported per mile against a threshold. Steiner's data imply mean gust spacings of about 0.09 mi (about 140 m) inside convective radar echo and up to 0.73 mi (about 1.2 km) in clear air near clouds. At cruise TAS that is roughly one gust every 0.5 s in a storm core and one every 4–5 s near clouds, counting gusts of all sizes. Large gusts are far rarer.

### Cited Findings (5)

- **Gust spacing.** The mean spacing was 0.087 mi in cloud radar echo and up to 0.73 mi in clear air near clouds, with smaller spacing in clear air over the SW US because of many small gusts (<10 ft/s). — [Steiner, NACA RM, NTRS 19930085520](https://ntrs.nasa.gov/api/citations/19930085520/downloads/19930085520.pdf)
- **Variation in gust frequency.** Gust-acceleration frequencies span about a 100:1 range across airplane operations, and about one-third of that is explained by the different amounts of rough air (7–20% of flight time). — [Coleman 1968, NTRS 19680015004](https://ntrs.nasa.gov/api/citations/19680015004/downloads/19680015004.pdf)
- **Per-mile vs per-flight.** Repeated-load distributions vary much less per flight than per flight mile. — [Coleman, "Trends in repeated loads on transport airplanes" (1965 ICAF), NTRS 19660009086](https://ntrs.nasa.gov/api/citations/19660009086/downloads/19660009086.pdf) (via search summary)
- **Gusts vs maneuvers.** At a 0.5 g increment, gust accelerations were about 55 times more frequent than maneuver accelerations on a US–Alaska route (about 673 flight hours). Rough air was defined as accelerations of 0.3 g or more in one analysis. — [Copp & Coleman, NACA TN 3475 (1955)](https://ntrs.nasa.gov/api/citations/19930084253/downloads/19930084253.pdf) (via search summary)
- **Hunter 1968, TN D-4330.** Gives cumulative derived-gust-velocity frequency per mile for four-engine turbojet airline operations, and shows the rough-air percentage falling with altitude. — [Hunter, NASA TN D-4330 (1968)](https://ntrs.nasa.gov/api/citations/19680007053/downloads/19680007053.pdf)

### Inferences (5)

- **Suggested sim targets.** These are derived, not measured as "per minute felt by pilots":
  - Light CAT: a noticeable bump every few seconds (about 10–20 per minute), mostly small.
  - Moderate: bumps every 1–3 s, with a distinctly larger one every 10–30 s.
  - Severe: the moderate background plus a cluster of 2–5 large jolts over 5–30 s.
  - Convective in-echo: gusts nearly continuous (about 2 per second at 250 m/s if all magnitudes are counted).
- Gust rate per minute scales with TAS for a fixed spatial spacing. A 747 at cruise covers about 15 km per minute, so the per-mile figures translate to about 7 times more gusts per minute than at 60 kt.

### Gaps (5)

- I found no study that directly measures "distinct bumps per minute" as perceived by pilots or passengers at a given PIREP intensity.
- The NACA/NASA VGH per-mile exceedance curves (Hunter 1968; Copp & Coleman 1955) contain the numbers needed to derive the rate of exceeding X g per minute for jet transports, but I did not digitize the figures.
