# Measured g levels in airliner turbulence: categories, aircraft response, and incidents

Scope: Δg at the centre of gravity (CG) for each turbulence category, how it scales with aircraft type, autopilot behaviour, design-gust scale, and incidents with measured flight-data accelerations. Researched 2026-10-26.

Definitions used below: "Δg" means the deviation of normal load factor (nz) from 1.0 g. "p-p" means peak-to-peak, the swing from the lowest nz to the highest. Recorder values are absolute nz, so 1.0 g is level flight.

## 1. Category definitions: EDR thresholds and Δg per intensity

### Takeaway (1)

No current regulatory document defines light/moderate/severe in g. FAA and ICAO define the categories by aircraft and occupant behaviour, and ICAO adds EDR thresholds. The de facto g scale used by NCAR and by older ICAO material is: moderate about 0.5 to 1.0 g change at the CG, severe more than 1.0 g, extreme more than 2 g. That scale is best read as a peak deviation from 1 g, not peak-to-peak. EDR thresholds differ between sources: ICAO 2001 used 0.1/0.3/0.5, Annex 3 Amendment 79 uses 0.10/0.20/0.45, and NCAR's GTG uses 0.15/0.22/0.34.

### Cited Findings (1)

- FAA definitions are qualitative and contain no g values. Light: "slight, erratic changes in altitude and/or attitude". Moderate: "Changes in altitude and/or attitude occur, but the aircraft remains in positive control at all times. It usually causes variations in indicated airspeed." Severe: "large, abrupt changes in altitude and/or attitude. It usually causes large variations in indicated airspeed. Aircraft may be momentarily out of control." Extreme: "violently tossed about and is practically impossible to control. It may cause structural damage." The handbook also says "the effect of turbulence varies based on the size of the aircraft." — [FAA Aviation Weather Handbook ch. 19 excerpt (NTSB docket)](https://data.ntsb.gov/Docket/Document/docBLOB?ID=19973276&FileExtension=pdf&FileName=FAA+Aviation+Weather+Handbook+-Turbulence+Excerpts-Rel.pdf)
- NCAR (Sharman, 2019/2023 slides) gives the PIREP categories as: "moderate" (>1/2 g, may avoid), "severe" (>1 g, must avoid), "extreme" (>2 g, must avoid). The slides add that the categories depend on the aircraft. Moderate-or-greater turbulence is about 1% of flight time and severe is about 0.1% or less. The slides also state "peak g ~ σg ~ ε^1/3". — [Sharman, NCAR, "Methods for prediction of aircraft turbulence and their validation"](https://cpaess.ucar.edu/sites/default/files/2023-08/Sharman-Bob.pdf)
- The same slides give "ICAO standard thresholds (2001) for mid-sized aircraft": EDR = 0.10, 0.3 and 0.5 for light, moderate and severe. NCAR's GTG instead uses EDR = 0.15, 0.22 and 0.34 m^2/3 s^-1. — [Sharman slides](https://cpaess.ucar.edu/sites/default/files/2023-08/Sharman-Bob.pdf)
- ICAO Annex 3, Appendix 4, Amendment 79 (peak EDR, medium-sized transport, typical en-route conditions): nil ≤0.10, light >0.10 to <0.20, moderate 0.20 to <0.45, severe ≥0.45. Heavy aircraft need a higher EDR to experience the same category and light aircraft a lower one. Type-specific thresholds should come from the aircraft's technical documentation. — [ICAO METP WG-MOG SN.35, revised EDR values Amd 79](https://www.icao.int/airnavigation/METP/Seventh%20Meeting%20of%20the%20MOG/METPWGMOG_7_SN.35_Revised_EDR_values_Amd_79_vs_final_22mar2018.pdf) (as summarised in search results); [ICAO WAFS gridded data training](https://www2023.icao.int/WACAF/Documents/Meetings/2025/Workshop-SADIS/2%20WAFS%20Gridded%20Data.pdf)
- The older ICAO-style wording appears in ATPL question banks: moderate is "changes in accelerometer readings of 0.5 g to 1.0 g at the aircraft's centre of gravity" and severe is "greater than 1.0 g". This is a study-aid source and has not been verified against the ICAO original. — [furious-atpl.com](https://www.furious-atpl.com/mcq-exam/answer,177-practice-protocol)
- An ICAO A41 working paper uses a different scale: light 0.1 to 0.4, moderate 0.4 to 0.7, severe >0.7. It is not the Annex 3 scale, so the two should not be mixed. — [ICAO A41 WP/235](https://www.icao.int/sites/default/files/sp-files/Meetings/a41/Documents/WP/wp_235_en.pdf) (from search summary)
- Airborne weather radar standard (RTCA, cited by NTSB): radar "severe turbulence" is defined as 0.3 g RMS vertical acceleration. — [NTSB SS-21/01, footnote 46](https://ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- A commercial blog gives light ≈ ±0.1 g, moderate ≈ ±0.5 g, severe ≈ ±1.0 g and extreme > ±1.5 g. It is not authoritative. — [Turbcast](https://www.turbcast.com/blog/turbulence-severity-levels)
- Patents in this area use quite different g thresholds, for example moderate 0.1 to 0.3 g RMS. One notes that a 2 g event may be moderate for a light aircraft and severe for a heavy one. — search summary of USPTO patents [9070284](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9070284), [6160498](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6160498)

### Inferences (1)

- A defensible target table for Δg at the CG, as peak deviation from 1 g:

| Category | Peak deviation | Peak-to-peak |
|---|---|---|
| Light | about 0.05 to 0.2 g | up to about 0.4 g |
| Moderate | about 0.2 to 0.5 g, up to 1.0 g in the old ICAO wording | about 0.5 to 1.0 g |
| Severe | more than about 0.5 to 1.0 g; reaching negative g (nz < 0) is the usual mark of severe | 1.5 to 3 g |
| Extreme | more than 1.5 to 2 g | 3 g or more |

  The incidents in section 4 support these figures: every injury-causing "severe" event reached nz ≤ about +0.0 to −0.8 or nz ≥ about +1.8.
- Peak deviation versus peak-to-peak is the main source of confusion. NCAR and ICAO wording ("changes in accelerometer readings") is usually read as an excursion from 1 g. Media and lawyers often quote peak-to-peak; for example, SQ321 is described as "a change of 2.85 g".
- EDR and Δg are roughly linear (peak g ∝ σg ∝ EDR). With Annex 3 thresholds, a medium transport at EDR 0.45 should therefore see about twice the Δg it sees at EDR 0.20.

### Gaps (1)

- The exact current ICAO Annex 3 text and the original ICAO 2001 table could not be retrieved; both are cited through ICAO training material and NCAR slides.
- FAA AIM 7-1-21 and AC 00-45H turbulence tables were not fetched. As far as I know they are qualitative with no g values, consistent with the FAA handbook excerpt, but this was not verified.
- The Sharman et al. 2014 JAMC paper (doi 10.1175/JAMC-D-13-0329.1), which holds the B737/B757 PIREP-to-EDR regressions, returned HTTP 403. One secondary citation says B737 "moderate" ≈ 0.22 m^2/3 s^-1, but this is unverified.

## 2. EDR to Δg, and how the aircraft's gust response scales it

### Takeaway (2)

For the same atmospheric EDR, Δg grows with true airspeed, air density, and lift-curve slope, and falls with wing loading and mass ratio. ICAO and NTSB both say heavy aircraft need a higher EDR to reach the same reported category. I found no published 747-400-specific EDR-to-g coefficients.

### Cited Findings (2)

- The relationship between EDR and vertical acceleration "is a function of the aircraft response characteristics and varies with altitude, aircraft mass, and airspeed", so deriving EDR from accelerations needs substantial aircraft-specific information. — [Sharman et al. 2017, JAMC 56(6), "Comparison of Turbulence Indicators Obtained from In Situ Flight Data"](https://journals.ametsoc.org/view/journals/apme/56/6/jamc-d-16-0291.1.pdf) (search summary)
- Annex 3 EDR thresholds are for a "medium-sized transport aircraft". Heavy aircraft need a higher EDR to experience severe and light aircraft a lower one. — [ICAO WAFS material](https://www2023.icao.int/WACAF/Documents/Meetings/2025/Workshop-SADIS/2%20WAFS%20Gridded%20Data.pdf) (search summary)
- The NTSB found that EDR software "must be tuned for each aircraft type". It also recommended developing methods to translate between EDR algorithms, because different implementations give different values. — [NTSB SS-21/01](https://ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- Load factor varies along the cabin. On a B757-200 research aircraft, load factors at the pilot station were 12% higher than at the CG and at the aft galley 40% higher (unpublished; NASA/Proctor 2006 work). On a B737-900ER accident (DCA16CA010), the aft cabin peaked at about −0.4 g and then 2.0 g, about 0.2 g larger in magnitude than at the CG. On an E190 (DCA20CA043) the differences along the cabin were smaller. — [NTSB SS-21/01 §on load factors, figs 22–23](https://ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- In-situ EDR algorithm (Boeing/NCAR): based on about 8 Hz avionics data and a k^-5/3 spectrum fitted over 0.5 to 3.5 Hz, using a 10 s sliding window. Peak and mean EDR are reported every minute (about 12 km of flight). — [Sharman slides](https://cpaess.ucar.edu/sites/default/files/2023-08/Sharman-Bob.pdf)

### Inferences (2)

- This relation is unsourced here (it is the classical Pratt gust-load formula from older FAR 25.341 and textbooks): Δn = Kg·ρ0·V_EAS·a·U_de / (2·W/S), with Kg = 0.88μ/(5.3+μ) and μ = 2(W/S)/(ρ·c̄·a·g).
  - Typical 747-400 cruise wing loading (about 280 to 330 t on 541 m², about 520 to 610 kg/m²) is similar to an A320/737 at cruise (about 60 to 70 t on 122 to 125 m², about 500 to 560 kg/m²).
  - So for the same gust velocity, the per-gust Δg of a 747 and a narrowbody differs less than intuition suggests. The 747 has higher cruise TAS and Mach (M0.85 versus M0.78) but a larger mass ratio and a longer chord.
  - The larger difference is in the frequency content. A long-span, heavy aircraft filters out short-wavelength gusts (span and chord averaging, slower short-period mode), so it feels lower-frequency, smoother "heave" for the same EDR.
  - Rough practical rule from the ICAO "heavier needs higher EDR" statement: a 747 at a given EDR sits about one category notch lower than a medium jet, or about 0.6 to 0.8 times the Δg. This is an inference, not a measured coefficient.
- Seat position matters for a sim that drives seat shakers: the cockpit sees about 10% more Δg than the CG and the aft cabin up to about 40% more. Pilots in a 747 sit far forward of the CG.

### Gaps (2)

- No published EDR-to-peak-g regression for the B747-400 or A320 was found. The Sharman 2014 regressions (B737/B757) could not be accessed.
- No quantitative gust-alleviation data for the 747-400 was found. As far as I know the 747-400 has no active gust load alleviation in the A320/787 sense, but this was not verified here.

## 3. Aircraft and autopilot response, and design gust loads for scale

### Takeaway (3)

The 747-400 turbulent air penetration speed is 290 to 310 KIAS / M0.82 to 0.85, whichever is lower. Boeing guidance is to keep the autopilot engaged, accept altitude variations, and not chase airspeed. NTSB case studies show that in severe convective turbulence the autopilot often disconnects on its own. Overspeed warnings, stick shaker, large vertical-speed swings and pitch-down upsets are typical. FAR 25 design gusts give the physical scale: discrete gusts up to 56 ft/s EAS at sea level (20.86 ft/s at 60,000 ft), gust gradients of 30 to 350 ft, and limit load +2.5/−1.0 g.

### Cited Findings (3)

- 747 FCOM (EVA Air copy, March 2015): turbulent air penetration speed is "290 to 310 KIAS/.82 to .85M, whichever is lower" and is a memory limitation. — [Scribd: 747 Operating Limitations](https://www.scribd.com/document/461296148/Operating-Limitations)
- B777 operator OM-B (Boeing-derived) guidance on autopilot use:
  - "the use of the autopilot is recommended for flight through turbulence" (after takeoff, once clean).
  - ALT HOLD and VNAV can be used in cruise because speed is managed by the autothrottle.
  - In climb and descent, VNAV or FLCH "may result in excessive pitch changes", so V/S is suggested in severe turbulence.
  - Altitude variations "are likely in severe turbulence and should be allowed to occur" if terrain clearance is adequate.

  This is a third-party-hosted copy whose status is unverified. — [Severe Turbulence and Windshear (B777 OM-B)](https://files.bavirtual.co.uk/bavirtual-docstore/om-b/b777/Severe%20Turbulence%20and%20Windshear.pdf) (search summary)
- FAA AC 120-5 (1963): avoid known severe turbulence, fly a turbulence target speed, do not chase airspeed, and choose an altitude with adequate manoeuvring margins. — [FAA NWA FSB 8-63](https://www.faa.gov/sites/faa.gov/files/NWA%20FSB%208-63%20Soderlind.pdf) (search summary)
- NTSB case studies:
  - Compass 5763, E175, FL340, Feb 2019. A severe updraft gave a sharp vertical-speed increase that triggered a TCAS "Descend" RA. The pilot disconnected the autopilot, but the updraft "prevented the pilot flying from complying with the RA", and was followed by a "sharp drop".
  - Another case at FL370 near convection with no radar returns: "Airspeed and altitude began fluctuating rapidly". On an overspeed warning the crew went to idle and deployed speedbrakes. "Large fluctuations in vertical speed continued", and "the autopilot automatically disengaged simultaneously with stick shaker activation."
  - A third case descending through about FL185 in cloud: severe turbulence caused "the autopilot to disengage and the airplane to pitch nose down rapidly."

  — [NTSB SS-21/01, Appendix](https://ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- Ryanair 737 EI-HGO, 28 Dec 2025, severe turbulence near FL370: the autopilot disengaged, and the crew flew manually and then re-engaged it. — [BEA notified event](https://bea.aero/en/investigation-reports/notified-events/detail/accident-to-the-boeing-737-registered-ei-hgo-operated-by-ryanair-on-28-12-2025-en-route/) (search summary)
- Turbulence statistics:
  - 2009–2018: turbulence caused more than a third of all Part 121 accidents.
  - Flight attendants are about 80% of the injured.
  - 55% of moderate-or-greater observations were below 15,000 ft, and another 17% between 15,000 ft and FL200.

  — [NTSB SS-21/01](https://ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- CAT patches are typically 100 to 300 mi long, 50 to 100 mi wide and about 5,000 ft deep, and persist from 30 min to a day. Moderate CAT is likely when vertical shear is ≥5 kt/1,000 ft or horizontal shear is ≥40 kt/150 mi. The threshold jet core speed for CAT is about 110 kt. — [FAA Aviation Weather Handbook ch. 19](https://data.ntsb.gov/Docket/Document/docBLOB?ID=19973276&FileExtension=pdf&FileName=FAA+Aviation+Weather+Handbook+-Turbulence+Excerpts-Rel.pdf)
- 14 CFR 25.341 design gusts:
  - Discrete gust: `U = (Uds/2)[1 − cos(πs/H)]` over 0 ≤ s ≤ 2H, with H from 30 to 350 ft.
  - Uref (EAS, VB–VC): 56.0 ft/s at sea level, 44.0 ft/s at 15,000 ft, 20.86 ft/s at 60,000 ft, linear in between. Halved at VD.
  - Alleviation factor Fg rises linearly to 1.0 at maximum operating altitude.
  - Continuous turbulence Uσref: 90 ft/s TAS at sea level falling to 79 ft/s TAS at 24,000 ft, then constant to 60,000 ft.

  — [14 CFR 25.341 (LII)](https://www.law.cornell.edu/cfr/text/14/25.341)

### Inferences (3)

- These are realistic sim behaviours for severe turbulence on a 747 at cruise: altitude excursions of about ±100 to 200 ft over a few seconds (see SQ321 and UA826 below), IAS swings of ±10 to 20 kt with overspeed clacker possible near Mmo (hence M0.82 to 0.85 penetration), and a possible AP disconnect or stick shaker on strong updraft/downdraft pairs.
- Moderate turbulence should leave the AP engaged with altitude held within roughly ±50 ft or less. This is inferred from the FAA "positive control at all times" wording; there is no numeric source.
- Using FL350 (Uref about 30 ft/s EAS by linear interpolation), the design discrete gust is about twice the gust implied by a severe in-service event. In-service events reaching nz from −1.5 to +1.8 approach but do not exceed the +2.5/−1.0 limit, which matches SQ321 (−1.5 g briefly exceeded the −1.0 limit at the CG with no reported structural failure).

### Gaps (3)

- No Boeing AERO turbulence article or official 747-400 FCTM text was retrieved. Autopilot guidance comes from a third-party B777 OM copy.
- 747-400 short-period and phugoid frequencies were not found in this search. NASA CR-2144 (Hanke 1970, 747 simulation data) is the classic source but was not fetched.
- No quantitative data on typical airspeed fluctuation (kt) by category was found.

## 4. Incidents with measured flight-data accelerations

### Takeaway (4)

Measured severe and extreme events on widebodies cluster at nz of about −0.8 to −1.5 g (minimum) and +1.5 to +1.8 g (maximum), a peak-to-peak of about 2.5 to 3 g. The g reversal takes about 0.6 to 4 s, and the altitude change is only about 100 to 180 ft. Moderate-to-severe "background" turbulence around these events ran at nz of about +0.4 to +1.6 g.

### Cited Findings (4)

- **SQ321, B777-300ER, 21 May 2024**, cruise FL370 over southern Myanmar, developing convection:
  - From about 07:49:21 UTC, nz fluctuated between +0.44 and +1.57 g for about 19 s ("slight vibration").
  - At 07:49:40, nz fell from +1.35 g to −1.5 g within 0.6 s, and unbelted occupants became airborne.
  - It then went from −1.5 g to +1.5 g within about 4 s.
  - The whole sequence lasted 4.6 s, with an altitude drop of 178 ft (37,362 to 37,184 ft). The aircraft was back at 37,000 ft at 07:50:23.
  - 1 fatality and dozens injured.

  — [AirlineRatings (TSIB preliminary)](https://www.airlineratings.com/articles/preliminary-singapore-airlines-turbulence-report); [LoyaltyLobby](https://loyaltylobby.com/2024/05/29/preliminary-findings-on-the-singapore-airlines-sq321-flight-turbulence-incident/); [Flightradar24](https://www.flightradar24.com/blog/flight-tracking-news/major-incident/1-dead-dozens-injured-in-sq321-turbulence/)
- **SQ321 TSIB final report (19 May 2026):**
  - Cause: a rapidly growing cloud with an updraft estimated at 8,000 to 9,000 fpm.
  - It is inconclusive whether the weather radar malfunctioned; the same aircraft's radar had failed to display weather 6 days earlier.
  - A passenger lawyer cites "+1.57 g, a change of 2.85 g within 0.6 s"; this is unverified against the report.

  — [Asia News Network](https://asianews.network/severe-turbulence-that-hit-singapore-airlines-flight-in-2024-not-detected-by-weather-radar-investigators/); [Travel Weekly](https://travelweekly.com.au/not-too-late-to-claim-over-sq321-turbulence-as-tsib-issues-final-report/)
- **UA826, B747-122 N4723U, 28 Dec 1997**, FL310 cruise over the Pacific, clear-air turbulence. Peak nz was +1.814 g in the first sharp ascent and −0.824 g at the minimum, with 0.1 g lateral; the down phase came about 6 s after the up phase (ASN). Altitude dropped about 100 ft. 1 fatality (unbelted) and 102 injured, of whom 15 passengers and 3 flight attendants had spine or neck fractures. — [Wikipedia](https://en.wikipedia.org/wiki/United_Airlines_Flight_826); [Aviation Safety Network](https://aviation-safety.net/asndb/324068); [Deseret News](https://www.deseret.com/1997/12/31/19354367/plane-plunged-only-100-feet-black-box-says/)
- **B737-900ER, NTSB DCA16CA010:** estimated aft-cabin nz of about −0.4 g, then 2.0 g. That is about 0.2 g larger than at the CG, so the CG saw roughly −0.2 to +1.8 g. — [NTSB SS-21/01 fig. 22](https://ntsb.gov/safety/safety-studies/Documents/SS2101.pdf)
- **Delta B777, 2013, descent:** nz "fluctuated between +1.56 g's and +0.024 g's over a period of about 10 seconds". — [NTSB CAROL report](https://data.ntsb.gov/carol-repgen/api/Aviation/ReportMain/GenerateNewestReport/82465/pdf) (search summary; the exact report ID mapping is unverified)
- **American Eagle EMB-145, 2011, climb:** nz "ranged from 1.54 g to 0.60 g in a 1-second period". — NTSB CAROL report (search summary; [one of these](https://data.ntsb.gov/carol-repgen/api/Aviation/ReportMain/GenerateNewestReport/78703/pdf))
- **NTSB MIA96LA220:** peaks of 1.81 g and −1.38 g. The aircraft type was not retrieved. — search summary only (via [ATSB/ASN search result](https://aviation-safety.net/asndb/324068)); unverified
- **Hawaiian 35, A330, 18 Dec 2022**, descent near Maui. The crew flew over a building storm cell ("moving fast", "building fast"); the A330 radar does not detect clouds. 24 injured, 4 seriously. The NTSB final report (Dec 2024) blamed the crew's decision to overfly the cell. No g values were found in the coverage. — [Hawaii News Now](https://www.hawaiinewsnow.com/2024/12/13/ntsb-report-crew-blame-severe-turbulence-2022-hawaiian-air-flight)
- **QR017, B787, 26 May 2024** over Turkey during meal service: lasted under 20 s, 12 injured, 8 hospitalised. No published g values were found. — [Arab News](https://www.arabnews.com/node/2517396/amp)
- **Air Europa UX045, B787-9, 1 July 2024** over the Atlantic near Brazil, diverted to Natal: about 30 to 40 injured, and one passenger was lodged in the overhead bin area. No CIAIAC g values were found. — [AirlineRatings](https://airlineratings.com/articles/turbulence-hits-air-europa-787)

### Inferences (4)

- Calibration targets for an "extreme / injury-causing" convective or CAT hit on a 747 at cruise:
  - nz minimum −0.8 to −1.5 g and maximum +1.5 to +1.8 g.
  - The negative excursion happens in about 0.5 to 1 s, and the full up-down cycle takes 4 to 6 s.
  - Net altitude loss is only about 100 to 200 ft. The popular "dropped 1,000 ft" is wrong for both UA826 and SQ321.
  - The extreme pulse is preceded by about 20 s of moderate-to-severe chop at nz +0.4 to +1.6 (Δg ±0.6).
- "Severe but no negative g" events, such as Delta 777 2013 at +0.02 to +1.56 g over 10 s and EMB-145 at +0.6 to +1.54 g in 1 s, show that severe as reported by crews and injuries starts around Δg ≈ ±0.5 to 1.0, peak-to-peak 1 to 1.5 g.

### Gaps (4)

- No public g data was found for QR017, UX045 or Hawaiian 35. The NTSB HA35 docket and the CIAIAC/Qatar investigations were not reached.
- No Qantas incident with g data was researched within the call budget.
- The Delta 2013 and EMB-145 2011 figures come from search snippets of NTSB CAROL reports. Their report IDs and full context were not opened.
